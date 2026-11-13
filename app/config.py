from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config() -> dict[str, Any]:
    config_path = Path(os.getenv("CONFIG_FILE", "/config/config.yaml"))
    if not config_path.exists():
        raise FileNotFoundError(f"File di configurazione non trovato: {config_path}")
    with config_path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}
    config["paths"] = {
        "data": Path(os.getenv("DATA_DIR", "/data")),
        "archive": Path(os.getenv("ARCHIVE_DIR", "/archive")),
    }
    config["runtime"] = {
        "imap_host": os.getenv("IMAP_HOST", config.get("gmail", {}).get("host", "imap.gmail.com")),
        "imap_port": int(os.getenv("IMAP_PORT", config.get("gmail", {}).get("port", 993))),
        "imap_username": os.getenv("IMAP_USERNAME", "").strip(),
        "imap_password": os.getenv("IMAP_APP_PASSWORD", "").replace(" ", ""),
        "imap_folder": os.getenv("IMAP_FOLDER", config.get("mail", {}).get("folder", "INBOX")),
        "poll_interval": int(os.getenv("POLL_INTERVAL", config.get("mail", {}).get("poll_interval_seconds", 300))),
        "ha_webhook_url": os.getenv("HA_WEBHOOK_URL", "").strip(),
        "test_mode": os.getenv("TEST_MODE", "off").strip().lower(),
        "test_provider": os.getenv("TEST_PROVIDER", "all").strip().lower(),
        "test_uid": os.getenv("TEST_UID", "").strip(),
        "test_force_reprocess": env_bool("TEST_FORCE_REPROCESS", False),
        "iren_enabled": env_bool("IREN_AUTOMATION_ENABLED", False),
        "iren_username": os.getenv("IREN_USERNAME", "").strip(),
        "iren_password": os.getenv("IREN_PASSWORD", ""),
        "iren_login_url": os.getenv("IREN_LOGIN_URL", config.get("iren", {}).get("login_url", "https://clienti.irenyou.gruppoiren.it/")),
        "iren_username_selector": os.getenv("IREN_USERNAME_SELECTOR", "").strip(),
        "iren_password_selector": os.getenv("IREN_PASSWORD_SELECTOR", 'input[type="password"]'),
        "iren_submit_selector": os.getenv("IREN_SUBMIT_SELECTOR", "").strip(),
        "iren_bills_url": os.getenv("IREN_BILLS_URL", "").strip(),
        "iren_download_selector": os.getenv("IREN_DOWNLOAD_SELECTOR", "").strip(),
        "iren_debug_screenshot": env_bool("IREN_DEBUG_SCREENSHOT", False),
        "iren_retry_seconds": int(os.getenv("IREN_RETRY_SECONDS", 21600)),
    }
    return config
