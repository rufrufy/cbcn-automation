#!/usr/bin/env python3
"""
CodeBuddy.cn Batch Registration — Auto-Inject to 9router DB
============================================================
Registers CodeBuddy.cn accounts via 5SIM HK OTP, creates API keys,
and auto-injects them directly into 9router SQLite database.

Features:
- 5SIM JWT Bearer auth (Hong Kong numbers)
- Webshare rotating proxies (HK/MO/CN) with IP session binding
- Referer + Origin headers (bypasses Keycloak 403)
- Auto-inject API keys to 9router DB (provider: codebuddy-cn)
- Dedup against existing DB keys by suffix
- ~82% success rate

Usage:
    python3 batch_register.py [target_keys]

Environment:
    .5sim_key  — File containing 5SIM JWT API key

Requirements:
    pip install requests beautifulsoup4
"""

import json
import time
import sys
import os
import html as html_module
import sqlite3
import uuid
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

_script_dir = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_script_dir, ".5sim_key"), "r") as _f:
    FIVESIM_API_KEY = _f.read().strip()

FIVESIM_BASE = "https://5sim.net/v1"
CODEBUDDY_BASE = "https://www.codebuddy.cn"
COUNTRY = "hongkong"
SERVICE = "codebuddy"
USER_ENTERPRISE_ID = "personal-edition-user-id"
OUTPUT_DIR = _script_dir

# 9router DB
ROUTER_DB = os.path.expanduser("~/.9router/db/data.sqlite")
ROUTER_PROVIDER = "codebuddy-cn"

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ═══════════════════════════════════════════════════════════════
# PROXY ROTATION
# ═══════════════════════════════════════════════════════════════

WEBSHARE_PROXIES = [
    os.environ.get("PROXY_HK_1", ""),
    os.environ.get("PROXY_HK_2", ""),
    os.environ.get("PROXY_MO_1", ""),
    os.environ.get("PROXY_MO_2", ""),
    os.environ.get("PROXY_CN_1", ""),
    os.environ.get("PROXY_CN_2", ""),
]
# Remove empty entries
WEBSHARE_PROXIES = [p for p in WEBSHARE_PROXIES if p]

_proxy_idx = 0


def get_next_proxy():
    """Round-robin proxy rotation. Returns (proxy_url, location_label)."""
    global _proxy_idx
    proxy = WEBSHARE_PROXIES[_proxy_idx % len(WEBSHARE_PROXIES)]
    _proxy_idx += 1
    loc = "HK" if "HK" in proxy else ("MO" if "MO" in proxy else "CN")
    return proxy, loc


# ═══════════════════════════════════════════════════════════════
# 9ROUTER DB OPERATIONS
# ═══════════════════════════════════════════════════════════════

def get_existing_keys_9router():
    """Get set of last-8-chars of all existing codebuddy-cn API keys."""
    conn = sqlite3.connect(ROUTER_DB)
    conn.text_factory = lambda x: x.decode('utf-8', errors='replace')
    c = conn.cursor()
    c.execute("SELECT data FROM providerConnections WHERE provider = ?", (ROUTER_PROVIDER,))
    existing = set()
    for row in c.fetchall():
        try:
            d = json.loads(row[0])
            key = d.get("apiKey", "")
            if key:
                existing.add(key[-8:])
        except:
            pass
    conn.close()
    return existing


def get_next_priority_9router():
    conn = sqlite3.connect(ROUTER_DB)
    c = conn.cursor()
    c.execute("SELECT MAX(priority) FROM providerConnections WHERE provider = ?", (ROUTER_PROVIDER,))
    result = c.fetchone()[0]
    conn.close()
    return (result or 0) + 1


def inject_key_to_9router(api_key, name):
    """Inject API key directly into 9router SQLite DB."""
    conn = sqlite3.connect(ROUTER_DB)
    c = conn.cursor()
    new_id = str(uuid.uuid4())
    priority = get_next_priority_9router()
    now_iso = datetime.now().isoformat() + "Z"
    data = json.dumps({
        "apiKey": api_key,
        "testStatus": "active",
        "providerSpecificData": {
            "connectionProxyEnabled": False,
            "connectionProxyUrl": "",
            "connectionNoProxy": "",
        },
    })
    try:
        c.execute(
            """INSERT INTO providerConnections
               (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
               VALUES (?, ?, 'apikey', ?, NULL, ?, 1, ?, ?, ?)""",
            (new_id, ROUTER_PROVIDER, name, priority, data, now_iso, now_iso)
        )
        conn.commit()
        conn.close()
        return True, new_id
    except Exception as e:
        conn.close()
        return False, str(e)


def count_9router_connections():
    conn = sqlite3.connect(ROUTER_DB)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM providerConnections WHERE provider = ?", (ROUTER_PROVIDER,))
    count = c.fetchone()[0]
    conn.close()
    return count


# ═══════════════════════════════════════════════════════════════
# 5SIM CLIENT
# ═══════════════════════════════════════════════════════════════

class FiveSimClient:
    """5SIM API client using JWT Bearer auth."""

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {FIVESIM_API_KEY}",
            "Accept": "application/json",
        })

    def balance(self):
        return self.s.get(f"{FIVESIM_BASE}/user/profile", timeout=15).json()

    def buy(self, max_retries=3):
        for attempt in range(max_retries):
            try:
                r = self.s.get(
                    f"{FIVESIM_BASE}/user/buy/activation/{COUNTRY}/any/{SERVICE}",
                    timeout=30,
                )
                if r.status_code == 200:
                    return r.json()
                print(f"    [!] Buy {attempt+1}: HTTP {r.status_code} - {r.text[:80]}")
            except Exception as e:
                print(f"    [!] Buy {attempt+1}: {e}")
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
        return None

    def check(self, oid):
        return self.s.get(f"{FIVESIM_BASE}/user/check/{oid}", timeout=15).json()

    def finish(self, oid):
        try:
            self.s.get(f"{FIVESIM_BASE}/user/finish/{oid}", timeout=15)
        except:
            pass

    def cancel(self, oid):
        try:
            self.s.get(f"{FIVESIM_BASE}/user/cancel/{oid}", timeout=15)
        except:
            pass

    def wait_sms(self, oid, timeout=90, interval=5):
        start = time.time()
        while time.time() - start < timeout:
            try:
                o = self.check(oid)
                if o.get("sms"):
                    return o["sms"][0].get("code")
                if o.get("status") in ["CANCELED", "TIMEOUT", "BANNED"]:
                    return None
            except:
                pass
            time.sleep(interval)
        return None


# ═══════════════════════════════════════════════════════════════
# REGISTRATION FLOW
# ═══════════════════════════════════════════════════════════════

def try_register(fivesim, attempt_num, use_proxy=True):
    """
    Single registration attempt.
    Flow: buy number → login form → send SMS → wait OTP → submit login →
          account setup → create API key
    Returns API key string or None.
    """
    print(f"\n{'='*50}")
    print(f"  ATTEMPT #{attempt_num}")
    print(f"{'='*50}")

    # 1. Buy number
    print(f"[1] Buying 5sim number...")
    order = fivesim.buy()
    if not order:
        print(f"  [!] Failed to buy number")
        return None
    oid = order["id"]
    phone = order["phone"]
    if not phone.startswith("+"):
        phone = "+" + phone.lstrip("+")
    print(f"  Phone: {phone} | Order: {oid} | ${order['price']}")

    # 2. Setup session with proxy
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    })
    s.verify = False

    proxy_url = None
    proxy_loc = "direct"
    if use_proxy and WEBSHARE_PROXIES:
        proxy_url, proxy_loc = get_next_proxy()
        s.proxies = {"http": proxy_url, "https": proxy_url}
        print(f"  [*] Proxy: Webshare-{proxy_loc} (#{_proxy_idx})")

    # 3. Login flow — KEEP PROXY throughout (codebuddy.cn binds session to IP)
    print(f"[2] Login flow → /console/accounts...")
    try:
        r = s.get(f"{CODEBUDDY_BASE}/console/accounts", allow_redirects=True, timeout=30)
    except Exception as e:
        print(f"  [!] Connection failed (proxy {proxy_loc}): {e}")
        proxy_url, proxy_loc = get_next_proxy()
        s.proxies = {"http": proxy_url, "https": proxy_url}
        print(f"  [*] Retrying with Webshare-{proxy_loc} (#{_proxy_idx})...")
        try:
            r = s.get(f"{CODEBUDDY_BASE}/console/accounts", allow_redirects=True, timeout=30)
        except Exception as e2:
            print(f"  [!] Retry also failed: {e2}")
            fivesim.cancel(oid)
            return None

    login_page_url = r.url  # Save for Referer header in POST

    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find("form", {"id": "kc-form-login"})
    if not form:
        print(f"  [!] Login form not found")
        fivesim.cancel(oid)
        return None
    action = form.get("action")
    action = html_module.unescape(action)

    # 4. Send SMS — KEEP PROXY (same IP as login flow)
    print(f"[3] Sending SMS to {phone}...")
    sms_sent = False
    for sms_attempt in range(3):
        try:
            r = s.get(
                f"{CODEBUDDY_BASE}/auth/realms/copilot/sms/authentication-code",
                params={"phoneNumber": phone},
                timeout=15,
            )
        except Exception as e:
            print(f"    [!] SMS attempt {sms_attempt+1}: proxy error - {e}")
            proxy_url, proxy_loc = get_next_proxy()
            s.proxies = {"http": proxy_url, "https": proxy_url}
            print(f"    [*] Switched to Webshare-{proxy_loc} (#{_proxy_idx})")
            try:
                r = s.get(
                    f"{CODEBUDDY_BASE}/auth/realms/copilot/sms/authentication-code",
                    params={"phoneNumber": phone},
                    timeout=15,
                )
            except Exception as e2:
                print(f"    [!] Retry SMS also failed: {e2}")
                continue
        if r.status_code == 200:
            try:
                expires = r.json().get("expires_in", "?")
            except:
                expires = "?"
            print(f"  [+] SMS sent (expires_in={expires}s)")
            sms_sent = True
            break
        print(f"    [!] SMS attempt {sms_attempt+1}: HTTP {r.status_code}, retry in 5s...")
        time.sleep(5)

    if not sms_sent:
        print(f"  [!] SMS send failed")
        fivesim.cancel(oid)
        return None

    # 5. Wait for OTP
    print(f"[4] Waiting for OTP (90s timeout)...")
    otp = fivesim.wait_sms(oid, timeout=90, interval=5)
    if not otp:
        print(f"  [!] No OTP received — cancelling")
        fivesim.cancel(oid)
        return None
    print(f"  [+] OTP: {otp}")

    # 6. Submit login — KEEP PROXY + Referer + Origin headers (fixes 403)
    print(f"[5] Submitting login via Webshare-{proxy_loc}...")
    try:
        r = s.post(action, data={
            "phoneActivated": "true",
            "username": "",
            "password": "",
            "phoneNumber": phone,
            "code": otp,
            "credentialId": "",
            "login": "",
            "rememberMe": "",
        }, allow_redirects=True, timeout=30, headers={
            "Referer": login_page_url,
            "Origin": "https://www.codebuddy.cn",
        })
    except Exception as e:
        print(f"  [!] Login POST failed: {e}")
        fivesim.cancel(oid)
        return None
    print(f"  [*] Status: {r.status_code}, URL: {r.url[:80]}")

    # 7. Finish 5sim
    fivesim.finish(oid)

    # 8. Account setup — KEEP PROXY
    print(f"[6] Account setup via Webshare-{proxy_loc}...")
    try:
        s.get(f"{CODEBUDDY_BASE}/console/accounts", timeout=15)
        s.post(f"{CODEBUDDY_BASE}/console/login/enterprise", json={"state": 2}, timeout=15)
        s.post(f"{CODEBUDDY_BASE}/billing/ide/trial", timeout=15)
    except Exception as e:
        print(f"  [!] Setup warning: {e}")

    # 9. Create API key — KEEP PROXY
    print(f"[7] Creating API key via Webshare-{proxy_loc}...")
    try:
        r = s.post(f"{CODEBUDDY_BASE}/console/api/client/v1/api-keys", json={
            "name": f"key-{attempt_num}",
            "expire_in_days": 365,
            "user_enterprise_id": USER_ENTERPRISE_ID,
        }, timeout=15)
    except Exception as e:
        print(f"  [!] API key creation failed: {e}")
        return None

    if r.status_code == 200:
        data = r.json()
        if data.get("code") == 0:
            key = data["data"]["key"]
            print(f"  [+] KEY: {key}")
            return key
        else:
            print(f"  [!] Error: {data.get('msg')}")
    else:
        print(f"  [!] HTTP {r.status_code}: {r.text[:200]}")

    return None


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    target_keys = int(sys.argv[1]) if len(sys.argv) > 1 else 10

    print("=" * 50)
    print("  CodeBuddy.cn Batch Registration + 9router Auto-Inject")
    print(f"  Target: {target_keys} API keys")
    print(f"  Proxies: {len(WEBSHARE_PROXIES)} Webshare rotating (HK/MO/CN)")
    print("=" * 50)

    fivesim = FiveSimClient()
    bal = fivesim.balance()
    balance = float(bal["balance"])
    cost_per = 0.13
    max_attempts = int(balance / cost_per)
    print(f"  5SIM Balance: ${balance}")
    print(f"  Max attempts: {max_attempts}")
    print(f"  OTP timeout: 90s per attempt")
    print(f"  Wait between attempts: 10s")

    # 9router status
    existing_in_router = count_9router_connections()
    existing_keys = get_existing_keys_9router()
    print(f"  9router codebuddy-cn connections: {existing_in_router}")
    print(f"  Unique keys in 9router (by suffix): {len(existing_keys)}")
    print()

    if max_attempts < target_keys:
        print(f"  [!] Warning: balance only supports {max_attempts} attempts")
        print(f"  [!] Proceeding with available balance...")

    keys_file = os.path.join(OUTPUT_DIR, "API_KEYS.txt")
    keys = []
    injected = 0
    skipped_dup = 0
    attempts = 0
    success = 0
    fail = 0

    for i in range(1, max_attempts + 1):
        if success >= target_keys:
            break

        # Re-check balance periodically
        if i > 1 and i % 5 == 0:
            try:
                bal = fivesim.balance()
                balance = float(bal["balance"])
                if balance < cost_per:
                    print(f"\n  [!] Balance exhausted: ${balance}")
                    break
                print(f"\n  [*] Balance check: ${balance}")
            except:
                pass

        attempts = i
        key = try_register(fivesim, i)

        if key:
            keys.append(key)
            success += 1
            with open(keys_file, "a") as f:
                f.write(key + "\n")
            print(f"  [✓] KEY saved to API_KEYS.txt")

            # Auto-inject to 9router DB
            key_suffix = key[-8:]
            if key_suffix in existing_keys:
                print(f"  [⚠] Key suffix {key_suffix} already in 9router — skip inject")
                skipped_dup += 1
            else:
                conn_name = f"Account {existing_in_router + 1}"
                ok, result = inject_key_to_9router(key, conn_name)
                if ok:
                    injected += 1
                    existing_in_router += 1
                    existing_keys.add(key_suffix)
                    print(f"  [✓] INJECTED to 9router DB: {conn_name} (id={result[:8]}...)")
                else:
                    print(f"  [!] INJECT FAILED: {result}")

            print(f"  [✓] SUCCESS #{success}")
            try:
                bal = fivesim.balance()
                print(f"  Balance: ${bal['balance']}")
            except:
                pass
        else:
            fail += 1
            print(f"\n  [✗] FAILED #{fail}")

        if success < target_keys and i < max_attempts:
            wait = 10
            print(f"\n  Waiting {wait}s...")
            time.sleep(wait)

    # Summary
    print(f"\n{'='*50}")
    print(f"  BATCH COMPLETE")
    print(f"{'='*50}")
    print(f"  Target:      {target_keys}")
    print(f"  Attempts:    {attempts}")
    print(f"  Success:     {success}")
    print(f"  Failed:      {fail}")
    print(f"  Injected:    {injected} (to 9router DB)")
    print(f"  Skipped dup: {skipped_dup}")
    print(f"  9router now: {count_9router_connections()} codebuddy-cn connections")
    if keys:
        print(f"  Keys:")
        for k in keys:
            print(f"    {k}")
    print(f"  Keys file: {keys_file}")
    try:
        bal = fivesim.balance()
        cost = attempts * cost_per
        print(f"  Cost:        ${cost:.2f}")
        print(f"  Balance:     ${bal['balance']}")
    except:
        pass
    rate = success / max(attempts, 1) * 100
    print(f"  Success rate: {success}/{attempts} = {rate:.0f}%")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
