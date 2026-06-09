#!/usr/bin/env python3
"""
KeyMaster — OpenRouter API Key Auto-Rotation & Self-Healing Utility.

Uses OpenRouter's official Management API to programmatically
create, store, rotate, and inject API keys so you never see
"API key is missing" again.
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
from pathlib import Path
from typing import Any

import argparse
import requests

# ---------------------------------------------------------------------------
# Configuration — all user-editable values live here
# ---------------------------------------------------------------------------

@dataclass
class Config:
    # ── OpenRouter Management API ──────────────────────────────────────────
    # Create one at https://openrouter.ai/settings/management-api-keys
    MANAGEMENT_API_KEY: str = ""

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
        return re.sub(
            r"(sk-or-v[12]-)[a-f0-9]{32,}([a-f0-9]{4})?",
            r"\1****\2",
            text,
        )


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
    """Raised when the Management API key is invalid or missing."""


class KeyCreationError(KeyMasterError):
    """Raised when API key creation fails."""


class KeyValidationError(KeyMasterError):
    """Raised when a created key fails validation."""


class ConfigurationError(KeyMasterError):
    """Raised when the configuration is incomplete or invalid."""


class OpenCodeUpdateError(KeyMasterError):
    """Raised when OpenCode configuration cannot be updated."""


class EnvUpdateError(KeyMasterError):
    """Raised when environment variable update fails."""


# ---------------------------------------------------------------------------
# Key management — Provider abstraction
# ---------------------------------------------------------------------------

class KeyProvider(ABC):
    """Abstract base for API key providers."""

    @abstractmethod
    def create_key(self, name: str, **kwargs: Any) -> str:
        """Create a new API key and return the key string."""
        ...

    @abstractmethod
    def validate_key(self, key: str) -> bool:
        """Validate that a key is usable (e.g. by making a lightweight call)."""
        ...

    @abstractmethod
    def list_keys(self) -> list[dict[str, Any]]:
        """List existing API keys (for rotation / expiry checks)."""
        ...

    @abstractmethod
    def delete_key(self, key_hash: str) -> bool:
        """Delete an API key by its hash."""
        ...


class OpenRouterKeyProvider(KeyProvider):
    """Key provider that uses OpenRouter's official Management API.

    Docs: https://openrouter.ai/docs/guides/overview/auth/management-api-keys
    """

    BASE_URL = "https://openrouter.ai/api/v1/keys"

    def __init__(self, management_api_key: str) -> None:
        if not management_api_key:
            raise ConfigurationError(
                "OpenRouter Management API key is required. "
                "Create one at https://openrouter.ai/settings/management-api-keys"
            )
        self._api_key = management_api_key
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
        )

    # ── API helpers ────────────────────────────────────────────────────────

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
                    method,
                    url,
                    timeout=CFG.REQUEST_TIMEOUT,
                    **kwargs,
                )
            except requests.RequestException as exc:
                last_exc = exc
                log.warning("HTTP error (attempt %d/%d): %s", attempt, CFG.MAX_RETRIES, exc)
                time.sleep(CFG.RETRY_BACKOFF * attempt)
                continue

            if resp.status_code == 401:
                raise AuthenticationError(
                    "Management API key is invalid or expired. "
                    "Regenerate one at https://openrouter.ai/settings/management-api-keys"
                )
            if resp.status_code == 429:
                log.warning("Rate limited (attempt %d/%d), backing off…", attempt, CFG.MAX_RETRIES)
                time.sleep(CFG.RETRY_BACKOFF * attempt * 2)
                continue
            if resp.status_code >= 500:
                log.warning("Server error %d (attempt %d/%d)", resp.status_code, attempt, CFG.MAX_RETRIES)
                time.sleep(CFG.RETRY_BACKOFF * attempt)
                continue

            if not resp.ok:
                raise KeyCreationError(
                    f"API returned {resp.status_code}: {resp.text}"
                )

            return resp.json()

        raise KeyCreationError(
            f"Request failed after {CFG.MAX_RETRIES} attempts"
        ) from last_exc

    # ── Key operations ─────────────────────────────────────────────────────

    def create_key(
        self,
        name: str,
        limit: float | None = None,
        limit_reset: str | None = None,
        expires_at: str | None = None,
    ) -> str:
        log.info("Creating API key: '%s'", name)

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
                "Response did not contain a 'data.key' field. "
                f"Raw response: {json.dumps(data, default=str)}"
            )

        log.info("API key created successfully (hash: %s)", data.get("data", {}).get("hash", "unknown"))
        return raw_key

    def validate_key(self, key: str) -> bool:
        try:
            resp = requests.get(
                f"{self.BASE_URL}/",
                headers={"Authorization": f"Bearer {key}"},
                timeout=CFG.REQUEST_TIMEOUT,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def list_keys(self) -> list[dict[str, Any]]:
        data = self._request("GET")
        return data.get("data", [])

    def delete_key(self, key_hash: str) -> bool:
        data = self._request("DELETE", endpoint=key_hash)
        return data.get("success", False)

    def get_current_key_metadata(self, key: str) -> dict[str, Any] | None:
        try:
            resp = requests.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {key}"},
                timeout=CFG.REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp.json().get("data")
            return None
        except requests.RequestException:
            return None


# ---------------------------------------------------------------------------
# Key generator
# ---------------------------------------------------------------------------

def generate_key_name(project: str = "") -> str:
    """Generate a timestamped key name: YYYYMMDD_HHMMSS_project."""
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
    """Updates OPENROUTER_API_KEY in the current process, .env file, etc."""

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
    """Locates and updates OpenCode configuration files."""

    # The API key for OpenRouter in OpenCode config is typically stored
    # in the provider config or as an env var. We update both.

    def __init__(self, config_paths: list[str]) -> None:
        self._config_paths = [Path(p) for p in config_paths]

    def find_config(self) -> Path | None:
        for p in self._config_paths:
            if p.exists():
                log.info("Found OpenCode config at %s", p)
                return p
        return None

    def update_config(self, key: str, env_var: str = "OPENROUTER_API_KEY") -> bool:
        config_path = self.find_config()
        if not config_path:
            log.warning("No OpenCode configuration file found.")
            return False

        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise OpenCodeUpdateError(
                f"Failed to read {config_path}: {exc}"
            ) from exc

        if "provider" not in data:
            data["provider"] = {}
        if "openrouter" not in data["provider"]:
            data["provider"]["openrouter"] = {}

        data["provider"]["openrouter"]["apiKey"] = key

        config_path.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )
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
            print(f'    Edit: {config_path}')
            print('    Add: "provider"."openrouter"."apiKey": "<your-key>"')
        print("=" * 60)
        print()


# ---------------------------------------------------------------------------
# Key rotation / expiry
# ---------------------------------------------------------------------------

class KeyRotationManager:
    """Handles automatic key rotation and expiration checks."""

    def __init__(self, provider: KeyProvider, store: KeyStore) -> None:
        self._provider = provider
        self._store = store

    def check_expiry(self, current_key: str) -> bool:
        metadata = self._provider.get_current_key_metadata(current_key)
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
        log.info("Performing key rotation…")
        name = generate_key_name(project)
        new_key = self._provider.create_key(name)
        return new_key


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

class KeyMaster:
    """Top-level orchestrator for key creation, storage, and integration."""

    def __init__(self, config: Config | None = None) -> None:
        self.cfg = config or CFG
        self._provider: OpenRouterKeyProvider | None = None
        self.store = KeyStore(self.cfg.API_KEYS_FILE)
        self.env_mgr = EnvManager(self.cfg.ENV_VAR_NAME)
        self.opencode = OpenCodeIntegrator(self.cfg.OPENCODE_CONFIG_PATHS)

    @property
    def provider(self) -> OpenRouterKeyProvider:
        if self._provider is None:
            self._provider = OpenRouterKeyProvider(self.cfg.MANAGEMENT_API_KEY)
        return self._provider

    @property
    def rotator(self) -> KeyRotationManager:
        return KeyRotationManager(self.provider, self.store)

    def run(self) -> str:
        """Main workflow: create key, store it, update env + OpenCode."""
        log.info("=" * 50)
        log.info("KeyMaster v1.0 — starting key generation workflow")
        log.info("=" * 50)

        self._validate_config()

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
        """Force a key rotation (create new, keep old in history)."""
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

    # ── Internal helpers ───────────────────────────────────────────────────

    def _validate_config(self) -> None:
        if not self.cfg.MANAGEMENT_API_KEY:
            raise ConfigurationError(
                "MANAGEMENT_API_KEY is empty.\n\n"
                "  1. Go to https://openrouter.ai/settings/management-api-keys\n"
                "  2. Create a new Management API Key\n"
                "  3. Set it in the Config class or via the MANAGEMENT_API_KEY env var.\n"
            )

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
        log.info("Validating newly created key…")
        valid = self.provider.validate_key(key)
        if valid:
            log.info("Key validation: PASSED")
        else:
            log.warning("Key validation returned unexpected result. The key may still work.")
            log.warning("Proceeding — key was successfully created by the API.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="KeyMaster — Automatic OpenRouter API Key Management",
        epilog="Because manually copying API keys in 2026 is cyberpunk fax-machine energy.",
    )

    parser.add_argument(
        "--management-key",
        help="OpenRouter Management API key (overrides config / env var)",
    )
    parser.add_argument(
        "--project", "-p",
        default=CFG.PROJECT_NAME,
        help=f"Project name for key naming (default: {CFG.PROJECT_NAME})",
    )
    parser.add_argument(
        "--rotate", "-r",
        action="store_true",
        help="Force key rotation (create new key immediately)",
    )
    parser.add_argument(
        "--status", "-s",
        action="store_true",
        help="Show current key status and history",
    )
    parser.add_argument(
        "--list-keys", "-l",
        action="store_true",
        help="List all API keys on the account",
    )
    parser.add_argument(
        "--no-env",
        action="store_false",
        dest="update_env",
        help="Skip environment variable updates",
    )
    parser.add_argument(
        "--no-opencode",
        action="store_false",
        dest="update_opencode",
        help="Skip OpenCode configuration updates",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="No-op; retained for forward-compat with browser automation",
    )
    parser.add_argument(
        "--log-level",
        default=CFG.LOG_LEVEL,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging verbosity",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    log.setLevel(getattr(logging, args.log_level.upper(), logging.INFO))

    # Merge CLI-override management key
    management_key = args.management_key or os.environ.get("MANAGEMENT_API_KEY") or CFG.MANAGEMENT_API_KEY
    if management_key:
        CFG.MANAGEMENT_API_KEY = management_key

    CFG.PROJECT_NAME = args.project
    CFG.AUTO_UPDATE_ENV = args.update_env if hasattr(args, "update_env") else CFG.AUTO_UPDATE_ENV
    CFG.AUTO_UPDATE_OPENCODE = args.update_opencode if hasattr(args, "update_opencode") else CFG.AUTO_UPDATE_OPENCODE

    km = KeyMaster(CFG)

    try:
        if args.status:
            status = km.show_status()
            print()
            print("  KeyMaster Status")
            print("  " + "-" * 40)
            print(f"  Current env var:  {status['current_env_key'][:15] + '****' if status['current_env_key'] else 'Not set'}")
            print(f"  Latest stored:    {'Yes' if status['latest_stored_key'] else 'None'}")
            print(f"  History entries:  {status['history_count']}")
            print()

        elif args.list_keys:
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

        elif args.rotate:
            key = km.rotate()
            print(f"  Rotated to new key: sk-or-v1-****{key[-4:]}")
            print()

        else:
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
