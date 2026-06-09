# 🔑 KeyMaster

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Dependencies: requests + playwright](https://img.shields.io/badge/dependencies-2-yellow)](requirements.txt)

> **Because manually copying API keys in 2026 feels like sending fax messages from a cyberpunk future.**

KeyMaster is a zero-hassle, auto-magical utility that creates, stores, rotates, and injects OpenRouter API keys so you _never_ see _"API key is missing"_ again.

---

## 🧠 Why This Exists

You know that feeling when you fire up a tool and it screams:

```
Error: OpenRouter API key is missing.
Pass it using the 'apiKey' parameter or the OPENROUTER_API_KEY environment variable.
```

Yeah. That.

Instead of:

1. Opening a browser 📖
2. Logging in 🔐
3. Clicking through menus 🖱️
4. Generating a key 🔄
5. Copying it 📋
6. Pasting it somewhere 💾
7. Repeating when keys expire 🔁

…you run one command and move on with your life. 🚀

**KeyMaster runs in two modes:**

| Mode | How | Requires |
|---|---|---|
| **API** 🚀 | Direct REST calls via Management API | One-time Management API key setup (~30s) |
| **Browser** 🌐 | Playwright automates the login + key creation | OpenRouter email & password |

> 💡 **API mode is faster and more reliable.** Browser mode is the fully-automated fallback if you don't want to create a Management API key manually.

---

## ✨ Features

| Feature | Status |
|---|---|---|
| 🔐 Official OpenRouter Management API integration | ✅ |
| 🌐 Playwright browser automation (email/password login) | ✅ |
| 🏷️ Auto-named keys (`YYYYMMDD_HHMMSS_project`) | ✅ |
| 📜 Append-only key history (`API_KEYS.txt`) | ✅ |
| 🌍 Automatic `OPENROUTER_API_KEY` env var update | ✅ |
| 📝 `.env` file management | ✅ |
| ⚙️ OpenCode config auto-update | ✅ |
| 🔄 Key rotation (create fresh keys on demand) | ✅ |
| ✅ Key validation before use | ✅ |
| 📋 List all keys on your account | ✅ |
| 📊 Status & history viewer | ✅ |
| ⏳ Expiry checking | ✅ |
| 🏗️ Provider abstraction (extend to OpenAI, Anthropic, etc.) | ✅ |
| 🛡️ Sensitive data masking in logs | ✅ |
| 📸 Screenshots on browser failures | ✅ |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      KeyMaster                          │
│  ┌──────────────────┐  ┌──────────┐  ┌───────────────┐ │
│  │   KeyProvider     │  │ KeyStore  │  │  EnvManager   │ │
│  │   (abstract)      │  │(API_KEYS  │  │ (env vars,    │ │
│  │                   │  │ .txt)     │  │  .env file)   │ │
│  ├──────────────────┤  └──────────┘  └───────────────┘ │
│  │  ┌────────────┐  │                  ┌─────────────┐ │
│  │  │ Management  │  │                  │  OpenCode   │ │
│  │  │ API (fast)  │  │                  │  Integrator │ │
│  │  ├────────────┤  │                  └─────────────┘ │
│  │  │  Browser   │  │                                   │
│  │  │ Automation │  │                                   │
│  │  └────────────┘  │                                   │
│  └──────────────────┘                                   │
└─────────────────────────────────────────────────────────┘
         │                    │
         ▼                    ▼
  OpenRouter API      OpenRouter Website
  (api/v1/keys)       (playwright login)
```

### How It Works

1. **KeyMaster picks your auth method** — Management API if a key is set, otherwise browser automation.
2. **API mode:** Talks directly to `POST /api/v1/keys` via OpenRouter's Management API.
3. **Browser mode:** Launches Playwright, logs in with your email/password, clicks through to create a key.
4. **Keys are auto-named** with timestamps so you know when each was created.
5. **The key is appended** to `API_KEYS.txt` (never overwritten — full history preserved).
6. **The `OPENROUTER_API_KEY` env var** is updated in the current process + `.env` file.
7. **OpenCode config** is auto-located and updated if found.
8. **Key validation** confirms the new key works.

---

## 📦 Installation

### Prerequisites

- Python 3.11+
- An OpenRouter account
- pip

### Setup

```bash
# Clone the repo
git clone https://github.com/nitinkumar30/openrouter-keymaster.git
cd openrouter-keymaster

# Copy the example key file (optional — created automatically on first run)
cp API_KEYS.example.txt API_KEYS.txt

# Install Python dependencies
pip install -r requirements.txt

# If using browser automation mode, also install Playwright browsers:
playwright install chromium
```

KeyMaster has **two dependencies**: `requests` + `playwright` (see [requirements.txt](requirements.txt)). Minimal and focused.

---

## ⚙️ Configuration

All configuration lives in one place: the `Config` dataclass at the top of `main.py`.

```python
@dataclass
class Config:
    # Option A: Management API key (recommended — fast & reliable)
    MANAGEMENT_API_KEY: str = ""

    # Option B: Login credentials (for browser automation)
    OPENROUTER_EMAIL: str = ""
    OPENROUTER_PASSWORD: str = ""

    PROJECT_NAME: str = "project"
    API_KEYS_FILE: str = "API_KEYS.txt"
    ENV_VAR_NAME: str = "OPENROUTER_API_KEY"
    AUTO_UPDATE_ENV: bool = True
    AUTO_UPDATE_OPENCODE: bool = True
    HEADLESS: bool = False          # Set True to hide browser window
    SCREENSHOT_ON_FAILURE: bool = True
    DOT_ENV_PATH: str = ".env"
    REQUEST_TIMEOUT: int = 30
    MAX_RETRIES: int = 3
    VALIDATE_KEY_BEFORE_USE: bool = True
    LOG_LEVEL: str = "INFO"
```

> 💡 **KeyMaster auto-detects which method to use.** If `MANAGEMENT_API_KEY` is set, it uses the API. If only email/password are set, it uses browser automation. If both are set, API mode wins (it's faster).

### Option A: Management API Key (Recommended)

1. Go to [openrouter.ai/settings/management-keys](https://openrouter.ai/settings/management-keys)
2. Click **"Create New Key"**
3. Give it a memorable name (like `keymaster-management`)
4. **Copy the key immediately** — it's shown only once

> ⚠️ **Warning:** Management keys are _not_ regular API keys. They cannot call completion endpoints. They are exclusively for managing other keys. Keep them safe.

### Option B: Browser Automation

If you'd rather skip the Management API setup, just provide your OpenRouter login credentials and KeyMaster will use Playwright to automate the browser:

1. Set your email and password as environment variables or in the Config class
2. Run `playwright install chromium` if you haven't already
3. Run KeyMaster — it logs in, creates the key, and copies it

> ⚠️ **Caution:** Browser automation is more fragile than the Management API. UI changes on OpenRouter's site could break the automation. If you hit issues, [create a Management API key](#option-a-management-api-key-recommended) instead.

### Setting Credentials

**Option A (Management API) — fast & reliable:**
```bash
$env:MANAGEMENT_API_KEY = "sk-or-v1-..."
```

**Option B (Browser automation) — fully automated, no manual setup:**
```bash
$env:OPENROUTER_EMAIL = "your@email.com"
$env:OPENROUTER_PASSWORD = "your-password"
```

**Or pass them directly to the CLI:**
```bash
python main.py --management-key "sk-or-v1-..."
python main.py --email "your@email.com" --password "your-password" --visible
```

> `--visible` shows the browser window so you can watch. Omit for headless mode.

---

## 🚀 Usage

### Basic — Create a New Key

```bash
python main.py
```

KeyMaster auto-detects your auth method. This:
- Generates a name like `20260610_143501_project`
- Creates a new key (via API or browser automation)
- Appends it to `API_KEYS.txt`
- Sets `OPENROUTER_API_KEY` in your environment + `.env`
- Updates OpenCode config if found
- Validates the key works

### Using Browser Automation

If you're using email/password login instead of the Management API:

```bash
# Run with visible browser (helps with debugging)
python main.py --email "your@email.com" --password "your-password" --visible

# Or set env vars and just run
$env:OPENROUTER_EMAIL = "your@email.com"
$env:OPENROUTER_PASSWORD = "your-password"
python main.py
```

KeyMaster will:
1. Launch Chromium (visible or headless)
2. Navigate to openrouter.ai
3. Click "Sign In" and enter your credentials
4. Navigate to the API Keys page
5. Click "New Key" and enter the auto-generated name
6. Retrieve the key from the page
7. Close the browser

> 💡 **Troubleshooting:** If the browser automation fails, a screenshot is saved to `screenshots/failure.png`. Check it to see what went wrong, then report the issue or switch to the Management API.

### Custom Project Name

```bash
python main.py --project "my-awesome-tool"
# → Key name: 20260610_143501_my-awesome-tool
```

### Key Rotation

```bash
python main.py --rotate
```

Creates a fresh key without the normal workflow output. Great for cron jobs.

### Check Status

```bash
python main.py --status
```

Shows current env var status, latest stored key, and history count.

### List All Keys

```bash
python main.py --list-keys
```

Lists every API key on your OpenRouter account.

### Skip Integrations

```bash
python main.py --no-env --no-opencode
```

Just create the key and save it to `API_KEYS.txt` — skip the rest.

---

## 🔗 OpenCode Integration

KeyMaster automatically:

1. **Searches** common OpenCode config locations:
   - `~/.opencode/opencode.json`
   - `~/.config/opencode/opencode.json`
   - `.opencode/opencode.json` (project-local)
   - `opencode.json` (project-local)

2. **Updates** the config with your new key under `provider.openrouter.apiKey`

3. **If auto-update fails**, prints manual instructions with the exact file path

> 💡 **Tip:** Set `AUTO_UPDATE_OPENCODE = True` and KeyMaster handles it every time.

---

## 🌍 Environment Variables

| Variable | Purpose |
|---|---|
| `MANAGEMENT_API_KEY` | Your OpenRouter Management API key |
| `OPENROUTER_EMAIL` | Your OpenRouter login email (browser mode) |
| `OPENROUTER_PASSWORD` | Your OpenRouter login password (browser mode) |
| `OPENROUTER_API_KEY` | Set automatically by KeyMaster |

KeyMaster updates:

- **Current process** — `os.environ["OPENROUTER_API_KEY"]`
- **`.env` file** — appended or updated in place

> 🔒 **Security Note:** Add `.env` to your `.gitignore`. Do not commit API keys.

---

## 🛡️ Security Considerations

| Risk | Mitigation |
|---|---|
| Management key exposure | Store in env var, never in code. Add to `.gitignore`. |
| API key leakage | Keys are masked in logs (`sk-or-v1-****abcd`). |
| `.env` file in repo | Add `.env` to `.gitignore`. Seriously. |
| Compromised Management key | Revoke at OpenRouter dashboard immediately. |
| Browser automation risks | Screenshots on failure. Use Management API for reliability. |
| Credential theft (browser mode) | Email/password stored in env vars, never logged. |
| CAPTCHA / MFA (browser mode) | Currently not supported. Use Management API if you have 2FA. |

### Best Practices

1. **Never commit API keys** to version control.
2. **Use environment variables** for the Management API key.
3. **Rotate keys regularly** with `--rotate`.
4. **Review key history** in `API_KEYS.txt`.
5. **Delete old unused keys** via the OpenRouter dashboard.

---

## 🔧 Troubleshooting

| Problem | Solution |
|---|---|
| `No authentication method configured` | Set either `MANAGEMENT_API_KEY` or `OPENROUTER_EMAIL`+`OPENROUTER_PASSWORD` |
| `Management API key is invalid` | Regenerate at [openrouter.ai/settings/management-keys](https://openrouter.ai/settings/management-keys) |
| `401 Unauthorized` | Your Management key is expired or wrong. Double-check. |
| `Rate limited (429)` | Backoff is built-in. Wait and retry. |
| OpenCode config not found | Check `OPENCODE_CONFIG_PATHS` in config. Or update manually. |
| `.env` file not created | Check directory permissions. Run from project root. |
| Key creation fails (API mode) | Your Management key may lack permissions. Check dashboard. |
| Browser login fails | Check email/password. Screenshot saved to `screenshots/failure.png`. |
| Browser can't find "New Key" button | OpenRouter UI may have changed. Screenshot saved for debugging. |
| CAPTCHA or 2FA during login | Browser mode can't bypass these. Use Management API instead. |
| `playwright` module not found | Run `pip install playwright` and `playwright install chromium` |

---

## ⚠️ Known Limitations

1. **Management API requires one-time manual setup** — You create 1 Management API key manually. This is an OpenRouter requirement.
2. **Browser automation is fragile** — UI changes on OpenRouter's site can break the automation. Screenshots help debug. The Management API is more reliable.
3. **No CAPTCHA/2FA support** — Browser mode can't handle CAPTCHA or multi-factor auth. Use Management API if you have 2FA enabled.
4. **OpenCode config format** — Assumes the standard `opencode.json` schema. Custom configs may need manual updates.
5. **Windows persistent env vars** — Setting system-wide persistent env vars on Windows requires admin privileges. KeyMaster updates process + `.env` instead.
6. **No GUI** — Terminal only. If you need a button to press, this isn't it.

---

## 🚧 Future Improvements

- [ ] Scheduled key rotation (cron integration)
- [ ] Automatic expiry checks with pre-expiry rotation
- [ ] Multi-provider support (OpenAI, Anthropic, Gemini, Groq, Mistral)
- [ ] Backup key support (failover keys)
- [ ] Telegram/Discord notifications on rotation
- [ ] Health check endpoint monitoring
- [ ] Key usage analytics
- [ ] Web UI for key management

---

## ❓ FAQ

**Q: Does this work with existing OpenRouter keys?**  
A: Yes. KeyMaster appends to your history and can validate existing keys.

**Q: Will this break my existing setup?**  
A: No. Existing keys stay valid. KeyMaster just adds new ones.

**Q: Can I use this with CI/CD pipelines?**  
A: Absolutely. `python main.py --rotate` is perfect for CI.

**Q: What if OpenRouter's API changes?**  
A: The Management API is documented and stable. We track changes.

**Q: Do I need Playwright/Selenium?**  
A: Nope. Zero browser automation. Pure REST API goodness.

**Q: Can I contribute providers for other services?**  
A: Yes! Implement `KeyProvider` and submit a PR.

---

## 🤝 Contributing

PRs welcome! Especially:

- Additional `KeyProvider` implementations
- Better OpenCode config detection
- Key expiry monitoring
- Web UI

### Development

```bash
pip install requests
python main.py
```

Keep it simple. One dependency. Clean code.

---

## 📄 License

[MIT](LICENSE) — Use it, fork it, ship it, break it, fix it, whatever.

---

## 🏆 Project Name Suggestions

Because naming things is the second hardest problem in computer science:

| Name | Vibes |
|---|---|
| **KeyForge** | 🔨 Fantasy Dwarf blacksmith energy |
| **Token Golem** | 🗿 Ancient, reliable, does one thing well |
| **API Wrangler** | 🤠 Yeehaw, pardner, let's rotate them keys |
| **Credential Ninja** | 🥷 Silent, deadly, never misses a key |
| **Key Slinger** | 🔫 Western outlaw who draws first |

> *Current winner: **KeyMaster** — because you're the master of your keys.* 😎

---

*Made with ☕ and mild frustration at missing API keys.*
