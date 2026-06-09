#!/usr/bin/env python3
"""
KeyMaster — OpenRouter API Key Auto-Rotation & Self-Healing Utility.

Two modes:
  A) Management API (fast) — uses a pre-created Management API key
  B) Browser automation (fully automated) — logs in with email/password via Playwright

OpenRouter does NOT provide an official login API, so option B uses Playwright
to automate the browser. Option A is preferred when available.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import argparse
import requests

# ---------------------------------------------------------------------------
# Configuration — all user-editable values live here
# ---------------------------------------------------------------------------

@dataclass
class Config:
    # ── Authentication (choose one method) ─────────────────────────────────
    # Option A: Management API key (create at openrouter.ai/settings/management-keys)
    MANAGEMENT_API_KEY: str = ""
    # Option B: Login credentials (for browser automation)
    OPENROUTER_EMAIL: str = ""
    OPENROUTER_PASSWORD: str = ""

    # ── Key naming ─────────────────────────────────────────────────────────
    PROJECT_NAME: str = "project"

    # ── Storage ────────────────────────────────────────────────────────────
    API_KEYS_FILE: str = "API_KEYS.txt"

    # ── Environment variable to update ─────────────────────────────────────
    ENV_VAR_NAME: str = "OPENROUTER_API_KEY"

    # ── Automation toggles ─────────────────────────────────────────────────
    AUTO_UPDATE_ENV: bool = True
    AUTO_UPDATE_OPENCODE: bool = True

    # ── OpenCode integration ───────────────────────────────────────────────
    OPENCODE_CONFIG_PATHS: list[str] = field(
        default_factory=lambda: [
            str(Path.home() / ".opencode" / "opencode.json"),
            str(Path.home() / ".config" / "opencode" / "opencode.json"),
            ".opencode/opencode.json",
            "opencode.json",
        ]
    )

    # ── .env file handling ─────────────────────────────────────────────────
    DOT_ENV_PATH: str = ".env"

    # ── HTTP / retry ───────────────────────────────────────────────────────
    REQUEST_TIMEOUT: int = 30
    MAX_RETRIES: int = 3
    RETRY_BACKOFF: float = 1.0

    # ── Key validation ─────────────────────────────────────────────────────
    VALIDATE_KEY_BEFORE_USE: bool = True

    # ── Browser automation settings ────────────────────────────────────────
    HEADLESS: bool = False
    SCREENSHOT_ON_FAILURE: bool = True
    SCREENSHOTS_DIR: str = "screenshots"

    # ── Logging ────────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"


# Global config instance
CFG = Config()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class SensitiveDataFilter(logging.Filter):
    """Mask API keys and other secrets in log output."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._mask(record.msg)
        if record.args:
            record.args = tuple(
                self._mask(a) if isinstance(a, str) else a for a in record.args
            )
        return True

    @staticmethod
    def _mask(text: str) -> str:
        text = re.sub(
            r"(sk-or-v[12]-)[a-f0-9]{32,}([a-f0-9]{4})?",
            r"\1****\2",
            text,
        )
        return text


def setup_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("keymaster")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    handler.addFilter(SensitiveDataFilter())
    logger.addHandler(handler)
    return logger


log = setup_logging()

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class KeyMasterError(Exception):
    """Base exception for all KeyMaster errors."""


class AuthenticationError(KeyMasterError):
    """Raised when authentication fails."""


class KeyCreationError(KeyMasterError):
    """Raised when API key creation fails."""


class KeyValidationError(KeyMasterError):
    """Raised when a created key fails validation."""


class ConfigurationError(KeyMasterError):
    """Raised when the configuration is incomplete or invalid."""


class OpenCodeUpdateError(KeyMasterError):
    """Raised when OpenCode configuration cannot be updated."""


class BrowserAutomationError(KeyMasterError):
    """Raised when browser automation fails."""


# ---------------------------------------------------------------------------
# Key management — Provider abstraction
# ---------------------------------------------------------------------------

class KeyProvider(ABC):
    """Abstract base for API key providers."""

    @abstractmethod
    def create_key(self, name: str, **kwargs: Any) -> str:
        ...

    @abstractmethod
    def validate_key(self, key: str) -> bool:
        ...

    @abstractmethod
    def list_keys(self) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def delete_key(self, key_hash: str) -> bool:
        ...


class OpenRouterKeyProvider(KeyProvider):
    """Key provider using OpenRouter's official Management API."""

    BASE_URL = "https://openrouter.ai/api/v1/keys"

    def __init__(self, management_api_key: str) -> None:
        if not management_api_key:
            raise ConfigurationError(
                "Management API key is required for API-based key creation. "
                "Get one at https://openrouter.ai/settings/management-keys"
            )
        self._api_key = management_api_key
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
        )

    def _request(
        self,
        method: str,
        endpoint: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        url = f"{self.BASE_URL}/{endpoint}".rstrip("/")
        last_exc: Exception | None = None

        for attempt in range(1, CFG.MAX_RETRIES + 1):
            try:
                resp = self._session.request(
                    method, url, timeout=CFG.REQUEST_TIMEOUT, **kwargs,
                )
            except requests.RequestException as exc:
                last_exc = exc
                log.warning("HTTP error (attempt %d/%d): %s", attempt, CFG.MAX_RETRIES, exc)
                time.sleep(CFG.RETRY_BACKOFF * attempt)
                continue

            if resp.status_code == 401:
                raise AuthenticationError(
                    "Management API key is invalid or expired. "
                    "Regenerate at https://openrouter.ai/settings/management-keys"
                )
            if resp.status_code == 429:
                log.warning("Rate limited (attempt %d/%d), backing off...", attempt, CFG.MAX_RETRIES)
                time.sleep(CFG.RETRY_BACKOFF * attempt * 2)
                continue
            if resp.status_code >= 500:
                log.warning("Server error %d (attempt %d/%d)", resp.status_code, attempt, CFG.MAX_RETRIES)
                time.sleep(CFG.RETRY_BACKOFF * attempt)
                continue
            if not resp.ok:
                raise KeyCreationError(f"API returned {resp.status_code}: {resp.text}")
            return resp.json()

        raise KeyCreationError(
            f"Request failed after {CFG.MAX_RETRIES} attempts"
        ) from last_exc

    def create_key(
        self,
        name: str,
        limit: float | None = None,
        limit_reset: str | None = None,
        expires_at: str | None = None,
    ) -> str:
        log.info("Creating API key via Management API: '%s'", name)
        payload: dict[str, Any] = {"name": name}
        if limit is not None:
            payload["limit"] = limit
        if limit_reset is not None:
            payload["limit_reset"] = limit_reset
        if expires_at is not None:
            payload["expires_at"] = expires_at

        data = self._request("POST", json=payload)
        raw_key = data.get("data", {}).get("key")
        if not raw_key:
            raise KeyCreationError(
                "Response missing 'data.key'. "
                f"Raw: {json.dumps(data, default=str)}"
            )
        log.info("Key created (hash: %s)", data.get("data", {}).get("hash", "unknown"))
        return raw_key

    def validate_key(self, key: str) -> bool:
        try:
            resp = requests.get(
                self.BASE_URL,
                headers={"Authorization": f"Bearer {key}"},
                timeout=CFG.REQUEST_TIMEOUT,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def list_keys(self) -> list[dict[str, Any]]:
        return self._request("GET").get("data", [])

    def delete_key(self, key_hash: str) -> bool:
        return self._request("DELETE", endpoint=key_hash).get("success", False)

    def get_current_key_metadata(self, key: str) -> dict[str, Any] | None:
        try:
            resp = requests.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {key}"},
                timeout=CFG.REQUEST_TIMEOUT,
            )
            return resp.json().get("data") if resp.status_code == 200 else None
        except requests.RequestException:
            return None


# ---------------------------------------------------------------------------
# Browser automation provider
# ---------------------------------------------------------------------------

class OpenRouterBrowserProvider(KeyProvider):
    """Key provider using Playwright browser automation.

    Logs into openrouter.ai with email/password, navigates to API keys,
    creates a new key, and retrieves it from the page.
    """

    LOGIN_URL = "https://openrouter.ai/sign-in?redirect_url=https%3A%2F%2Fopenrouter.ai"
    KEYS_URL = "https://openrouter.ai/workspaces/default/keys"

    def __init__(self, email: str, password: str) -> None:
        if not email or not password:
            raise ConfigurationError(
                "OPENROUTER_EMAIL and OPENROUTER_PASSWORD must be set "
                "for browser-based key creation."
            )
        self._email = email
        self._password = password

    def _take_screenshot(self, page: Any, name: str) -> None:
        if not CFG.SCREENSHOT_ON_FAILURE:
            return
        screenshots_dir = Path(CFG.SCREENSHOTS_DIR)
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        path = screenshots_dir / f"{name}.png"
        try:
            page.screenshot(path=str(path))
            log.info("Screenshot saved: %s", path)
        except Exception as exc:
            log.warning("Failed to save screenshot: %s", exc)

    def create_key(self, name: str, **kwargs: Any) -> str:
        log.info("Launching browser for key creation...")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=CFG.HEADLESS)
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            try:
                return self._do_create_key(page, name)
            except Exception:
                self._take_screenshot(page, "failure")
                raise
            finally:
                browser.close()

    def _do_create_key(self, page: Any, name: str) -> str:
        log.info("Navigating to %s", self.LOGIN_URL)
        page.goto(self.LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        self._login(page)
        self._navigate_to_keys(page)
        key = self._create_and_retrieve_key(page, name)
        return key

    def _login(self, page: Any) -> None:
        log.info("Logging in...")
        page.wait_for_timeout(1000)

        sign_in_selectors = [
            "button:has-text('Sign In')",
            "a:has-text('Sign In')",
            "button:has-text('Log in')",
            "a:has-text('Log in')",
            '[href*="sign-in"]',
            '[href*="login"]',
            'button:has-text("Sign in with")',
        ]
        sign_in_btn = None
        for sel in sign_in_selectors:
            try:
                btn = page.wait_for_selector(sel, timeout=5000)
                if btn and btn.is_visible():
                    sign_in_btn = btn
                    break
            except Exception:
                continue

        if sign_in_btn:
            log.info("Clicking sign in button")
            sign_in_btn.click()
            page.wait_for_timeout(2000)
        else:
            log.info("No sign-in button found, may already be on login page")

        email_input_selectors = [
            'input[type="email"]',
            'input[name="email"]',
            'input[placeholder*="email" i]',
            'input[placeholder*="Email" i]',
            'input:not([type="hidden"])',
        ]
        email_input = None
        for sel in email_input_selectors:
            try:
                el = page.wait_for_selector(sel, timeout=3000)
                if el and el.is_visible():
                    email_input = el
                    break
            except Exception:
                continue

        if not email_input:
            self._take_screenshot(page, "login-page-no-email-field")
            raise BrowserAutomationError(
                "Could not locate email input on login page. "
                "The page layout may have changed. Screenshot saved."
            )

        email_input.fill(self._email)
        page.wait_for_timeout(500)

        password_selectors = [
            'input[type="password"]',
            'input[name="password"]',
            'input[placeholder*="password" i]',
            'input[placeholder*="Password" i]',
        ]
        password_input = None
        for sel in password_selectors:
            try:
                el = page.wait_for_selector(sel, timeout=3000)
                if el and el.is_visible():
                    password_input = el
                    break
            except Exception:
                continue

        if not password_input:
            self._take_screenshot(page, "login-page-no-password-field")
            raise BrowserAutomationError(
                "Could not locate password input on login page."
            )

        password_input.fill(self._password)
        page.wait_for_timeout(500)

        submit_selectors = [
            'button[type="submit"]',
            'button:has-text("Sign In")',
            'button:has-text("Log in")',
            'button:has-text("Continue")',
            'button:has-text("Sign in")',
        ]
        submit_btn = None
        for sel in submit_selectors:
            try:
                btn = page.wait_for_selector(sel, timeout=3000)
                if btn and btn.is_visible():
                    submit_btn = btn
                    break
            except Exception:
                continue

        if submit_btn:
            submit_btn.click()
        else:
            email_input.press("Enter")

        log.info("Waiting for login to complete...")
        page.wait_for_timeout(5000)

        self._handle_otp_if_present(page)

        for _ in range(30):
            if self._is_logged_in(page):
                log.info("Login successful")
                return
            page.wait_for_timeout(1000)

        self._take_screenshot(page, "login-failed")
        raise AuthenticationError(
            "Login failed. Check your email and password. "
            "If 2FA/MFA is enabled, you may need to disable it or use "
            "a Management API key instead (faster and more reliable)."
        )

    def _handle_otp_if_present(self, page: Any) -> None:
        otp_selectors = [
            'input[autocomplete="one-time-code"]',
            'input[name*="otp" i]',
            'input[name*="code" i]',
            'input[name*="token" i]',
            'input[inputmode="numeric"]',
            'input[maxlength="6"]',
            'input[placeholder*="code" i]',
            'input[placeholder*="OTP" i]',
            'input[placeholder*="2FA" i]',
            'input:not([type="hidden"]):not([type="email"]):not([type="password"])',
        ]

        otp_input = None
        for sel in otp_selectors:
            try:
                el = page.wait_for_selector(sel, timeout=5000)
                if el and el.is_visible():
                    otp_input = el
                    break
            except Exception:
                continue

        if otp_input is None:
            return

        log.info("OTP/2FA code input detected")
        page.wait_for_timeout(1000)

        print()
        print("  " + "=" * 55)
        print("  2-Factor Authentication Required")
        print("  " + "=" * 55)
        print()
        print("  A verification code was sent to your email/authenticator app.")
        print()
        otp = input("  Enter 6-digit code: ").strip()
        page.bring_to_front()
        page.wait_for_timeout(500)

        otp_input.fill(otp)
        page.wait_for_timeout(500)

        otp_submit_selectors = [
            'button[type="submit"]',
            'button:has-text("Verify")',
            'button:has-text("Confirm")',
            'button:has-text("Submit")',
            'button:has-text("Continue")',
        ]
        for sel in otp_submit_selectors:
            try:
                btn = page.wait_for_selector(sel, timeout=3000)
                if btn and btn.is_visible():
                    btn.click()
                    log.info("OTP submitted")
                    page.wait_for_timeout(3000)
                    return
            except Exception:
                continue

        otp_input.press("Enter")
        log.info("OTP submitted (via Enter)")
        page.wait_for_timeout(3000)

    def _is_logged_in(self, page: Any) -> bool:
        indicator_selectors = [
            'a[href*="keys"]',
            'a[href*="settings"]',
            'a[href*="dashboard"]',
            'button:has-text("New Key")',
            'a:has-text("API Keys")',
            'text=API Keys',
            '[data-testid*="user"]',
            'button:has-text("Workspaces")',
            'a[href*="workspaces"]',
        ]
        for sel in indicator_selectors:
            try:
                el = page.wait_for_selector(sel, timeout=2000)
                if el and el.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _navigate_to_keys(self, page: Any) -> None:
        log.info("Navigating to API keys page...")
        page.wait_for_timeout(1000)

        keys_link_selectors = [
            'a[href*="keys"]',
            'a:has-text("API Keys")',
            'a:has-text("Keys")',
            'a[href*="api-keys"]',
            'a[href*="workspaces"]',
        ]
        for sel in keys_link_selectors:
            try:
                links = page.query_selector_all(sel)
                for link in links:
                    if link.is_visible():
                        href = link.get_attribute("href") or ""
                        if "key" in href.lower() or "key" in (link.text_content() or "").lower():
                            log.info("Clicking keys link: %s", sel)
                            link.click()
                            page.wait_for_timeout(3000)
                            return
            except Exception:
                continue

        log.info("No keys link found in sidebar, navigating directly to URL")
        page.goto(self.KEYS_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

    def _create_and_retrieve_key(self, page: Any, key_name: str) -> str:
        log.info("Looking for 'New Key' button...")
        page.wait_for_timeout(1000)

        new_key_selectors = [
            'button:has-text("New Key")',
            'button:has-text("New")',
            'button:has-text("Create Key")',
            'button:has-text("Create")',
            'button:has-text("Generate")',
            '[data-testid*="new-key"]',
            '[data-testid*="create-key"]',
            'a:has-text("New Key")',
        ]
        new_key_btn = None
        for sel in new_key_selectors:
            try:
                btn = page.wait_for_selector(sel, timeout=5000)
                if btn and btn.is_visible():
                    new_key_btn = btn
                    log.info("Found 'New Key' button: %s", sel)
                    break
            except Exception:
                continue

        if not new_key_btn:
            self._take_screenshot(page, "no-new-key-button")
            raise BrowserAutomationError(
                "Could not find 'New Key' button. The page layout may have changed. "
                "Screenshot saved for debugging."
            )

        new_key_btn.click()
        log.info("Clicked 'New Key' button")
        page.wait_for_timeout(2000)

        modal_name_input_selectors = [
            'input[placeholder*="name" i]',
            'input[placeholder*="Name" i]',
            'input[placeholder*="key" i]',
            'input[name="name"]',
            'input:not([type="hidden"]):not([type="email"]):not([type="password"])',
            'input[type="text"]',
            'textarea',
        ]
        name_input = None
        for sel in modal_name_input_selectors:
            try:
                els = page.query_selector_all(sel)
                for el in els:
                    if el.is_visible():
                        name_input = el
                        break
                if name_input:
                    break
            except Exception:
                continue

        if not name_input:
            self._take_screenshot(page, "modal-no-name-input")
            raise BrowserAutomationError(
                "Could not locate name input in the creation modal."
            )

        name_input.fill(key_name)
        log.info("Entered key name: %s", key_name)
        page.wait_for_timeout(500)

        create_btn_selectors = [
            'button:has-text("Create")',
            'button:has-text("Generate")',
            'button[type="submit"]',
            'button:has-text("Confirm")',
            'button:has-text("Save")',
        ]
        create_btn = None
        for sel in create_btn_selectors:
            try:
                btn = page.wait_for_selector(sel, timeout=5000)
                if btn and btn.is_visible():
                    create_btn = btn
                    log.info("Found create button: %s", sel)
                    break
            except Exception:
                continue

        if not create_btn:
            self._take_screenshot(page, "modal-no-create-button")
            raise BrowserAutomationError(
                "Could not find the create button in the modal."
            )

        create_btn.click()
        log.info("Submitted key creation")
        page.wait_for_timeout(3000)

        for _ in range(20):
            key = self._try_read_key_from_page(page)
            if key:
                log.info("Key retrieved from page")
                return key
            page.wait_for_timeout(1000)

        self._take_screenshot(page, "key-not-found-after-creation")
        raise BrowserAutomationError(
            "Key was created but could not be retrieved from the page. "
            "It may be displayed in a way we don't recognise. "
            "Check your OpenRouter dashboard. Screenshot saved."
        )

    def _try_read_key_from_page(self, page: Any) -> str | None:
        page_text = page.content()

        key_match = re.search(
            r"(sk-or-v[12]-[a-f0-9]{32,})",
            page_text,
        )
        if key_match:
            return key_match.group(1)

        key_inline = re.search(r"(sk-or-[a-z0-9]+-[a-zA-Z0-9]+)", page_text)
        if key_inline:
            return key_inline.group(1)

        visible_selectors = [
            '[class*="key"] code',
            '[class*="key"] pre',
            '[class*="key-value"]',
            '[class*="api-key"]',
            '[data-testid*="key"]',
            'code',
            'pre',
            '[class*="copy"]',
            '[class*="clipboard"]',
        ]
        for sel in visible_selectors:
            try:
                els = page.query_selector_all(sel)
                for el in els:
                    if el.is_visible():
                        text = el.text_content() or ""
                        if text.startswith("sk-or-"):
                            return text.strip()
            except Exception:
                continue

        return None

    def validate_key(self, key: str) -> bool:
        try:
            resp = requests.get(
                "https://openrouter.ai/api/v1/keys/",
                headers={"Authorization": f"Bearer {key}"},
                timeout=CFG.REQUEST_TIMEOUT,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def list_keys(self) -> list[dict[str, Any]]:
        raise BrowserAutomationError("list_keys is not supported via browser automation. Use Management API instead.")

    def delete_key(self, key_hash: str) -> bool:
        raise BrowserAutomationError("delete_key is not supported via browser automation. Use Management API instead.")


# ---------------------------------------------------------------------------
# Key generator
# ---------------------------------------------------------------------------

def generate_key_name(project: str = "") -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{project}" if project else ""
    return f"{ts}{suffix}"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

class KeyStore:
    """Manages the API_KEYS.txt file with append-only history."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def append(self, key: str) -> None:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] {key}\n"
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(entry)
        log.info("Key appended to %s", self._path.name)

    def get_latest(self) -> str | None:
        if not self._path.exists():
            return None
        with open(self._path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        if not lines:
            return None
        last = lines[-1]
        match = re.search(r"\[.*?\]\s+(sk-or-v[12]-.+)$", last)
        return match.group(1) if match else None

    def read_all(self) -> list[str]:
        if not self._path.exists():
            return []
        with open(self._path, "r", encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip()]


# ---------------------------------------------------------------------------
# Environment variable management
# ---------------------------------------------------------------------------

class EnvManager:
    def __init__(self, var_name: str = "OPENROUTER_API_KEY") -> None:
        self._var_name = var_name

    def set_process_env(self, key: str) -> None:
        os.environ[self._var_name] = key
        log.info("Environment variable %s set for current process", self._var_name)

    def update_dot_env(self, dot_env_path: str | Path, key: str) -> bool:
        path = Path(dot_env_path)
        var_name = self._var_name
        if path.exists():
            content = path.read_text(encoding="utf-8")
            pattern = re.compile(rf"^{re.escape(var_name)}=.*", re.MULTILINE)
            if pattern.search(content):
                content = pattern.sub(f"{var_name}={key}", content)
            else:
                content += f"\n{var_name}={key}\n"
        else:
            content = f"{var_name}={key}\n"
        path.write_text(content, encoding="utf-8")
        log.info("%s updated in %s", var_name, path.name)
        return True

    def get_current(self) -> str | None:
        return os.environ.get(self._var_name)


# ---------------------------------------------------------------------------
# OpenCode integration
# ---------------------------------------------------------------------------

class OpenCodeIntegrator:
    def __init__(self, config_paths: list[str]) -> None:
        self._config_paths = [Path(p) for p in config_paths]

    def find_config(self) -> Path | None:
        for p in self._config_paths:
            if p.exists():
                log.info("Found OpenCode config at %s", p)
                return p
        return None

    def update_config(self, key: str) -> bool:
        config_path = self.find_config()
        if not config_path:
            log.warning("No OpenCode configuration file found.")
            return False
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise OpenCodeUpdateError(f"Failed to read {config_path}: {exc}") from exc
        if "provider" not in data:
            data["provider"] = {}
        if "openrouter" not in data["provider"]:
            data["provider"]["openrouter"] = {}
        data["provider"]["openrouter"]["apiKey"] = key
        config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        log.info("OpenCode config updated at %s", config_path)
        return True

    def print_manual_instructions(self, config_path: Path | None = None) -> None:
        print()
        print("=" * 60)
        print("  OpenCode Manual Integration Instructions")
        print("=" * 60)
        if config_path:
            print(f"  Config file: {config_path}")
        print()
        print("  Set the OPENROUTER_API_KEY environment variable:")
        print()
        print("    # PowerShell")
        print("    $env:OPENROUTER_API_KEY = '<your-key>'")
        print()
        print("    # CMD")
        print("    set OPENROUTER_API_KEY=<your-key>")
        print()
        print("    # Or add to your shell profile (.bashrc, .zshrc, $PROFILE)")
        print('    export OPENROUTER_API_KEY="<your-key>"')
        print()
        if config_path:
            print("  Or add to OpenCode config manually:")
            print(f"    Edit: {config_path}")
            print('    Add: "provider"."openrouter"."apiKey": "<your-key>"')
        print("=" * 60)
        print()


# ---------------------------------------------------------------------------
# Key rotation / expiry
# ---------------------------------------------------------------------------

class KeyRotationManager:
    def __init__(self, provider: KeyProvider, store: KeyStore) -> None:
        self._provider = provider
        self._store = store

    def check_expiry(self, current_key: str) -> bool:
        try:
            metadata = self._provider.get_current_key_metadata(current_key)
        except AttributeError:
            log.warning("Expiry check not supported by this provider")
            return True
        if not metadata:
            return False
        expires_at = metadata.get("expires_at")
        if not expires_at:
            return True
        try:
            expiry = datetime.datetime.fromisoformat(expires_at)
            return expiry > datetime.datetime.now(expiry.tzinfo)
        except (ValueError, TypeError):
            return True

    def rotate_key(self, project: str) -> str:
        log.info("Performing key rotation...")
        name = generate_key_name(project)
        return self._provider.create_key(name)


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------

class ProviderType(Enum):
    MANAGEMENT_API = "api"
    BROWSER = "browser"
    AUTO = "auto"


def resolve_provider(cfg: Config, preferred: ProviderType = ProviderType.AUTO) -> KeyProvider:
    has_management_key = bool(cfg.MANAGEMENT_API_KEY)
    has_credentials = bool(cfg.OPENROUTER_EMAIL and cfg.OPENROUTER_PASSWORD)

    if preferred == ProviderType.MANAGEMENT_API:
        if not has_management_key:
            raise ConfigurationError("Management API key is required (set MANAGEMENT_API_KEY).")
        log.info("Using Management API provider")
        return OpenRouterKeyProvider(cfg.MANAGEMENT_API_KEY)

    if preferred == ProviderType.BROWSER:
        if not has_credentials:
            raise ConfigurationError(
                "Browser automation requires OPENROUTER_EMAIL and OPENROUTER_PASSWORD."
            )
        log.info("Using browser automation provider")
        return OpenRouterBrowserProvider(cfg.OPENROUTER_EMAIL, cfg.OPENROUTER_PASSWORD)

    if has_management_key:
        log.info("Using Management API provider (recommended fast path)")
        return OpenRouterKeyProvider(cfg.MANAGEMENT_API_KEY)
    if has_credentials:
        log.info("Using browser automation provider")
        return OpenRouterBrowserProvider(cfg.OPENROUTER_EMAIL, cfg.OPENROUTER_PASSWORD)

    raise ConfigurationError(
        "No authentication method configured.\n\n"
        "Run with --interactive (-i) to set up interactively:\n"
        "  python main.py --interactive\n\n"
        "Or configure manually:\n"
        "  1) MANAGEMENT_API_KEY env var (recommended):\n"
        "     $env:MANAGEMENT_API_KEY = 'sk-or-v1-...'\n\n"
        "  2) Login credentials for browser automation:\n"
        "     $env:OPENROUTER_EMAIL = 'your@email.com'\n"
        "     $env:OPENROUTER_PASSWORD = 'your-password'\n"
    )


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

class KeyMaster:
    """Top-level orchestrator for key creation, storage, and integration."""

    def __init__(self, config: Config | None = None, provider: KeyProvider | None = None) -> None:
        self.cfg = config or CFG
        self._provider = provider
        self.store = KeyStore(self.cfg.API_KEYS_FILE)
        self.env_mgr = EnvManager(self.cfg.ENV_VAR_NAME)
        self.opencode = OpenCodeIntegrator(self.cfg.OPENCODE_CONFIG_PATHS)

    @property
    def provider(self) -> KeyProvider:
        if self._provider is None:
            self._provider = resolve_provider(self.cfg)
        return self._provider

    @property
    def rotator(self) -> KeyRotationManager:
        return KeyRotationManager(self.provider, self.store)

    def run(self) -> str:
        log.info("=" * 50)
        log.info("KeyMaster v1.0 -- starting key generation workflow")
        log.info("=" * 50)

        name = generate_key_name(self.cfg.PROJECT_NAME)
        log.info("Generated key name: %s", name)

        key = self.provider.create_key(name)

        self.store.append(key)
        log.info("Key saved to %s", self.cfg.API_KEYS_FILE)

        if self.cfg.AUTO_UPDATE_ENV:
            self._update_environment(key)

        if self.cfg.AUTO_UPDATE_OPENCODE:
            self._update_opencode(key)

        if self.cfg.VALIDATE_KEY_BEFORE_USE:
            self._validate_key(key)

        log.info("=" * 50)
        log.info("Workflow complete. New key is active.")
        log.info("=" * 50)

        print()
        print(f"  New API Key: sk-or-v1-****{key[-4:]}")
        print(f"  Stored in:   {self.cfg.API_KEYS_FILE}")
        print()

        return key

    def rotate(self) -> str:
        key = self.rotator.rotate_key(self.cfg.PROJECT_NAME)
        self.store.append(key)
        if self.cfg.AUTO_UPDATE_ENV:
            self._update_environment(key)
        if self.cfg.AUTO_UPDATE_OPENCODE:
            self._update_opencode(key)
        return key

    def list_keys(self) -> list[dict[str, Any]]:
        return self.provider.list_keys()

    def get_history(self) -> list[str]:
        return self.store.read_all()

    def show_status(self) -> dict[str, Any]:
        current = self.env_mgr.get_current()
        history = self.get_history()
        latest = self.store.get_latest()
        return {
            "current_env_key": current,
            "latest_stored_key": latest,
            "history_count": len(history),
            "history": history,
        }

    def _update_environment(self, key: str) -> None:
        self.env_mgr.set_process_env(key)
        try:
            self.env_mgr.update_dot_env(self.cfg.DOT_ENV_PATH, key)
        except OSError as exc:
            log.warning("Could not update .env file: %s", exc)

    def _update_opencode(self, key: str) -> None:
        try:
            updated = self.opencode.update_config(key)
            if not updated:
                log.warning("OpenCode config not found. Skipping auto-update.")
                self.opencode.print_manual_instructions()
        except OpenCodeUpdateError as exc:
            log.warning("OpenCode update failed: %s", exc)
            self.opencode.print_manual_instructions()

    def _validate_key(self, key: str) -> None:
        log.info("Validating newly created key...")
        valid = self.provider.validate_key(key)
        if valid:
            log.info("Key validation: PASSED")
        else:
            log.warning("Key validation returned unexpected result. The key may still work.")
            log.warning("Proceeding -- key was successfully created.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="KeyMaster -- Automatic OpenRouter API Key Management",
        epilog="Because manually copying API keys in 2026 is cyberpunk fax-machine energy.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--management-key", help="OpenRouter Management API key")
    parser.add_argument("--email", help="OpenRouter login email (for browser automation)")
    parser.add_argument("--password", help="OpenRouter login password (for browser automation)")
    parser.add_argument("--interactive", "-i", action="store_true", help="Run interactive setup")
    parser.add_argument("--project", "-p", default=CFG.PROJECT_NAME, help=f"Project name (default: {CFG.PROJECT_NAME})")
    parser.add_argument("--rotate", "-r", action="store_true", help="Force key rotation")
    parser.add_argument("--status", "-s", action="store_true", help="Show current key status")
    parser.add_argument("--list-keys", "-l", action="store_true", help="List all API keys")
    parser.add_argument("--no-env", action="store_false", dest="update_env", help="Skip env var update")
    parser.add_argument("--no-opencode", action="store_false", dest="update_opencode", help="Skip OpenCode update")
    parser.add_argument("--headless", action="store_true", default=True, help="Run browser in headless mode")
    parser.add_argument("--visible", action="store_false", dest="headless", help="Show browser window (for debugging)")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default=CFG.LOG_LEVEL)

    return parser.parse_args(argv)


def interactive_setup() -> Config:
    """Interactive CLI prompts to configure authentication and save to .env."""
    cfg = Config()

    print()
    print("  KeyMaster Interactive Setup")
    print("  " + "-" * 40)
    print()
    print("  Choose authentication method:")
    print()
    print("    1) Management API Key (recommended -- fast & reliable)")
    print("    2) Browser automation (login with email & password)")
    print()

    while True:
        choice = input("  Enter 1 or 2: ").strip()
        if choice in ("1", "2"):
            break
        print("  Invalid choice. Enter 1 or 2.")

    dot_env_path = Path(CFG.DOT_ENV_PATH)
    lines: list[str] = []
    if dot_env_path.exists():
        lines = dot_env_path.read_text(encoding="utf-8").splitlines()

    def upsert_env(key: str, value: str) -> None:
        nonlocal lines
        found = False
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                found = True
                break
        if not found:
            lines.append(f"{key}={value}")

    if choice == "1":
        print()
        print("  Go to https://openrouter.ai/settings/management-keys")
        print("  Create a new Management API Key, then paste it below.")
        print()
        key = input("  Management API Key: ").strip()
        while not key:
            print("  Key cannot be empty.")
            key = input("  Management API Key: ").strip()
        cfg.MANAGEMENT_API_KEY = key
        upsert_env("MANAGEMENT_API_KEY", key)
        print("  [OK] Management API key saved.")

    else:
        print()
        email = input("  OpenRouter email: ").strip()
        while not email:
            print("  Email cannot be empty.")
            email = input("  OpenRouter email: ").strip()
        print()
        password = input("  OpenRouter password: ").strip()
        while not password:
            print("  Password cannot be empty.")
            password = input("  OpenRouter password: ").strip()

        cfg.OPENROUTER_EMAIL = email
        cfg.OPENROUTER_PASSWORD = password
        upsert_env("OPENROUTER_EMAIL", email)
        upsert_env("OPENROUTER_PASSWORD", password)
        print("  [OK] Login credentials saved.")

    dot_env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  [OK] Credentials saved to {dot_env_path.name}")
    print()
    print("  " + "-" * 40)
    print("  Setup complete! Generating your key now...")
    print()

    return cfg


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    log.setLevel(getattr(logging, args.log_level.upper(), logging.INFO))

    CFG.MANAGEMENT_API_KEY = args.management_key or os.environ.get("MANAGEMENT_API_KEY") or CFG.MANAGEMENT_API_KEY
    CFG.OPENROUTER_EMAIL = args.email or os.environ.get("OPENROUTER_EMAIL") or CFG.OPENROUTER_EMAIL
    CFG.OPENROUTER_PASSWORD = args.password or os.environ.get("OPENROUTER_PASSWORD") or CFG.OPENROUTER_PASSWORD
    CFG.PROJECT_NAME = args.project
    CFG.HEADLESS = args.headless
    CFG.AUTO_UPDATE_ENV = args.update_env if hasattr(args, "update_env") else CFG.AUTO_UPDATE_ENV
    CFG.AUTO_UPDATE_OPENCODE = args.update_opencode if hasattr(args, "update_opencode") else CFG.AUTO_UPDATE_OPENCODE

    has_auth = bool(CFG.MANAGEMENT_API_KEY) or bool(CFG.OPENROUTER_EMAIL and CFG.OPENROUTER_PASSWORD)
    has_action = args.status or args.list_keys or args.rotate

    if args.interactive or (not has_auth and not has_action):
        interactive_cfg = interactive_setup()
        CFG.MANAGEMENT_API_KEY = interactive_cfg.MANAGEMENT_API_KEY or CFG.MANAGEMENT_API_KEY
        CFG.OPENROUTER_EMAIL = interactive_cfg.OPENROUTER_EMAIL or CFG.OPENROUTER_EMAIL
        CFG.OPENROUTER_PASSWORD = interactive_cfg.OPENROUTER_PASSWORD or CFG.OPENROUTER_PASSWORD

    try:
        if args.status:
            km = KeyMaster(CFG)
            status = km.show_status()
            print()
            print("  KeyMaster Status")
            print("  " + "-" * 40)
            cur = status["current_env_key"]
            print(f"  Current env var:  {cur[:15] + '****' if cur else 'Not set'}")
            print(f"  Latest stored:    {'Yes' if status['latest_stored_key'] else 'None'}")
            print(f"  History entries:  {status['history_count']}")
            print()
            return 0

        if args.list_keys:
            km = KeyMaster(CFG)
            keys = km.list_keys()
            print()
            print(f"  API Keys ({len(keys)} found)")
            print("  " + "-" * 40)
            for k in keys:
                label = k.get("label", "N/A")
                name = k.get("name", "N/A")
                disabled = " [DISABLED]" if k.get("disabled") else ""
                print(f"  {label:30s}  {name}{disabled}")
            print()
            return 0

        if args.rotate:
            km = KeyMaster(CFG)
            key = km.rotate()
            print(f"  Rotated to new key: sk-or-v1-****{key[-4:]}")
            print()
            return 0

        km = KeyMaster(CFG)
        km.run()

    except KeyMasterError as exc:
        log.error(str(exc))
        return 1
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
        return 130

    return 0


if __name__ == "__main__":
    sys.exit(main())
