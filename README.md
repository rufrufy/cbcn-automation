# cbcn-automation

Automated CodeBuddy.cn account registration, API key generation, and 9router database injection.

## What It Does

1. **Buys** a Hong Kong phone number from 5SIM ($0.13/number)
2. **Registers** a CodeBuddy.cn account via Keycloak SMS OTP flow
3. **Activates** the trial and sets up enterprise account
4. **Creates** a 365-day API key
5. **Injects** the key directly into the local 9router SQLite database (`codebuddy-cn` provider)
6. **Deduplicates** against existing keys in the 9router DB

## Quickstart

```bash
# 1. Install dependencies
pip install requests beautifulsoup4

# 2. Add your 5SIM JWT API key
echo "eyJhbG...MiIs..." > .5sim_key
chmod 600 .5sim_key

# 3. Configure proxies via environment variables
export PROXY_HK_1="http://user-HK-rotate:pass@p.webshare.io:80"
export PROXY_HK_2="http://user-HK-rotate:pass@p.webshare.io:80"
export PROXY_MO_1="http://user-MO-rotate:pass@p.webshare.io:80"
export PROXY_MO_2="http://user-MO-rotate:pass@p.webshare.io:80"
export PROXY_CN_1="http://user-CN-rotate:pass@p.webshare.io:80"
export PROXY_CN_2="http://user-CN-rotate:pass@p.webshare.io:80"

# 4. Ensure 9router is running locally on port 20128
#    DB path: ~/.9router/db/data.sqlite

# 5. Run — register 10 API keys
python3 bot.py 10

# 6. Check results
cat API_KEYS.txt          # all generated keys
sqlite3 ~/.9router/db/data.sqlite \
  "SELECT COUNT(*) FROM providerConnections WHERE provider='codebuddy-cn'"
```

## Requirements

| Component | Details |
|-----------|---------|
| **5SIM account** | JWT API key with balance. Buy at [5sim.net](https://5sim.net) |
| **Webshare proxies** | Rotating residential proxies (HK/MO/CN). Buy at [webshare.io](https://webshare.io) |
| **9router** | Local instance running on port 20128 with SQLite DB at `~/.9router/db/data.sqlite` |
| **Python 3.10+** | `pip install requests beautifulsoup4` |

## Usage

```bash
# Default: 10 keys
python3 bot.py

# Custom target: 50 keys
python3 bot.py 50

# Keys are saved to API_KEYS.txt and auto-injected to 9router DB
```

## How It Works

```
5SIM (HK number) → CodeBuddy.cn Keycloak SMS OTP → Account setup → API key creation → 9router DB inject
```

### Registration Flow

1. **Buy number** — `GET 5sim.net/v1/user/buy/activation/hongkong/any/codebuddy`
2. **Login form** — `GET codebuddy.cn/console/accounts` → Keycloak OAuth redirect
3. **Send SMS** — `GET codebuddy.cn/auth/realms/copilot/sms/authentication-code?phoneNumber=+852...`
4. **Wait OTP** — Poll `GET 5sim.net/v1/user/check/{order_id}` until SMS arrives
5. **Submit login** — `POST codebuddy.cn/auth/realms/copilot/login-actions/authenticate` with OTP
6. **Account setup** — `POST /console/login/enterprise` + `POST /billing/ide/trial`
7. **Create API key** — `POST /console/api/client/v1/api-keys`
8. **Inject to 9router** — `INSERT INTO providerConnections ...`

### Key Fixes (vs naive approach)

| Issue | Fix |
|-------|-----|
| **403 on login POST** | Add `Referer` + `Origin` headers (Keycloak validates these) |
| **Session IP binding** | Keep same proxy throughout entire flow (GET → SMS → POST → key creation) |
| **HTML entities in form action** | `html.unescape()` the Keycloak form action URL |
| **Duplicate keys in 9router** | Dedup by last 8 chars of API key before inject |

## 9router Integration

Injected connections use this data structure:

```json
{
  "apiKey": "ck_xxxx.yyyy",
  "testStatus": "active",
  "providerSpecificData": {
    "connectionProxyEnabled": false,
    "connectionProxyUrl": "",
    "connectionNoProxy": ""
  }
}
```

Provider node: `codebuddy-cn` (built-in to 9router, no providerNodes entry needed).

## Project Structure

```
cbcn-automation/
├── bot.py               # Main script (single file)
├── .env.example         # Proxy env var template
├── .gitignore
├── README.md
├── .5sim_key            # 5SIM JWT key (gitignored)
└── API_KEYS.txt         # Generated keys (gitignored)
```

## Costs

| Item | Cost |
|------|------|
| 5SIM HK number | $0.13 per attempt |
| Success rate | ~82% |
| Effective cost per key | ~$0.16 |

## License

MIT

## Author

**exd77** — [github.com/exd77](https://github.com/exd77)
