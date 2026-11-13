from __future__ import annotations

import logging
from typing import Any

import requests

log = logging.getLogger(__name__)


class HomeAssistantNotifier:
    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url.strip()

    def send(self, title: str, message: str, **extra: Any) -> None:
        if not self.webhook_url:
            log.info("Notifica HA non configurata: %s — %s", title, message)
            return
        payload = {"title": title, "message": message, **extra}
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            # Do not print the webhook URL: its identifier is a bearer secret.
            log.error("Invio notifica Home Assistant non riuscito (%s)", type(exc).__name__)
