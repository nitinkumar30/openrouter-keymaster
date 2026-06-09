# 🔑 KeyMaster

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Dependencies: requests](https://img.shields.io/badge/dependencies-1-green)](requirements.txt)

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

**KeyMaster uses OpenRouter's official Management API** — no brittle browser automation, no CAPTCHA headaches, no "oops the button moved" maintenance.

---

## ✨ Features

| Feature | Status |
|---|---|
| 🔐 Official OpenRouter Management API integration | ✅ |
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
| 🚫 Zero browser automation | ✅ |

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────┐
│                    KeyMaster                      │
│  ┌─────────────┐  ┌──────────┐  ┌─────────────┐ │
│  │ KeyProvider  │  │ KeyStore  │  │  EnvManager  │ │
│  │  (abstract)  │  │(API_KEYS  │  │ (env vars,   │ │
│  │              │  │ .txt)     │  │  .env file)  │ │
│  ├─────────────┤  └──────────┘  └─────────────┘ │
│  │OpenRouter   │                  ┌─────────────┐ │
│  │KeyProvider  │                  │OpenCode     │ │
│  │(Management  │                  │Integrator   │ │
│  │ API)        │                  │             │ │
│  └─────────────┘                  └─────────────┘ │
└──────────────────────────────────────────────────┘
         │
         ▼
  OpenRouter API
  (api/v1/keys)
```

### How It Works

1. **You create one Management API Key** manually (one-time, ~30 seconds).
2. **KeyMaster talks directly to OpenRouter's API** (`POST /api/v1/keys`) to create new keys.
3. **Keys are auto-named** with timestamps so you know when each was created.
4. **The key is appended** to `API_KEYS.txt` (never overwritten — full history preserved).
5. **The `OPENROUTER_API_KEY` env var** is updated in the current process + `.env` file.
6. **OpenCode config** is auto-located and updated if found.
7. **Key validation** confirms the new key works.

---

## 📦 Installation

### Prerequisites

- Python 3.11+
- An OpenRouter account
- pip

### Setup

```bash
# Clone the repo
git clone https://github.com/your-username/keymaster.git
cd keymaster

# Copy the example key file (optional — created automatically on first run)
cp API_KEYS.example.txt API_KEYS.txt

# Install dependencies (just requests)
pip install -r requirements.txt
```

KeyMaster has **one dependency**: `requests` (see [requirements.txt](requirements.txt)). We kept it lean.

---

## ⚙️ Configuration

All configuration lives in one place: the `Config` dataclass at the top of `main.py`.

```python
@dataclass
class Config:
    MANAGEMENT_API_KEY: str = ""       # Your Management API key
    PROJECT_NAME: str = "project"       # Appears in key names
    API_KEYS_FILE: str = "API_KEYS.txt" # History file
    ENV_VAR_NAME: str = "OPENROUTER_API_KEY"
    AUTO_UPDATE_ENV: bool = True
    AUTO_UPDATE_OPENCODE: bool = True
    DOT_ENV_PATH: str = ".env"
    REQUEST_TIMEOUT: int = 30
    MAX_RETRIES: int = 3
    VALIDATE_KEY_BEFORE_USE: bool = True
    LOG_LEVEL: str = "INFO"
```

### Getting Your Management API Key

1. Go to [openrouter.ai/settings/management-api-keys](https://openrouter.ai/settings/management-api-keys)
2. Click **"Create New Key"**
3. Give it a memorable name (like `keymaster-management`)
4. **Copy the key immediately** — it's shown only once

> ⚠️ **Warning:** Management keys are _not_ regular API keys. They cannot call completion endpoints. They are exclusively for managing other keys. Keep them safe.

### Setting the Management Key

Choose your fighter:

```bash
# Option A: Environment variable (recommended)
export MANAGEMENT_API_KEY="sk-or-v1-..."

# Option B: CLI argument
python main.py --management-key "sk-or-v1-..."

# Option C: Edit Config class in main.py
# (not recommended for shared environments)
```

---

## 🚀 Usage

### Basic — Create a New Key

```bash
python main.py
```

This:
- Generates a name like `20260610_143501_project`
- Creates a new key via the Management API
- Appends it to `API_KEYS.txt`
- Sets `OPENROUTER_API_KEY` in your environment + `.env`
- Updates OpenCode config if found
- Validates the key works

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
| Browser automation risks | **Not applicable** — we use the official API. |
| Clipboard interception | **Not applicable** — keys go directly to storage. |

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
| `Management API key is invalid` | Regenerate at [openrouter.ai/settings/management-api-keys](https://openrouter.ai/settings/management-api-keys) |
| `401 Unauthorized` | Your Management key is expired or wrong. Double-check. |
| `Rate limited (429)` | Backoff is built-in. Wait and retry. |
| OpenCode config not found | Check `OPENCODE_CONFIG_PATHS` in config. Or update manually. |
| `.env` file not created | Check directory permissions. Run from project root. |
| Key creation fails | Your Management key may lack permissions. Check dashboard. |

---

## ⚠️ Known Limitations

1. **One-time Management Key setup** — You still need to manually create _one_ Management API key first. This is an OpenRouter requirement, not a KeyMaster limitation.
2. **No browser automation fallback** — If OpenRouter removes the Management API, KeyMaster would need browser automation. We track this scenario but it's unlikely.
3. **OpenCode config format** — Assumes the standard `opencode.json` schema. Custom configs may need manual updates.
4. **Windows persistent env vars** — Setting system-wide persistent env vars on Windows requires admin privileges. KeyMaster updates process + `.env` instead.
5. **No GUI** — Terminal only. If you need a button to press, this isn't it.

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
