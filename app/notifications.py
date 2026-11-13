from __future__ import annotations

import logging
from typing import Any

import requests

log = logging.getLogger(__name__)


class HomeAssistantNotifier:
    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url.strip()

    def send(self, title: str, message: str, **extra: Any) -> bool:
        if not self.webhook_url:
            log.error("Notifica HA non inviata: HA_WEBHOOK_URL non configurato (%s)", title)
            return False
        payload = {"title": title, "message": message, **extra}
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=15)
            response.raise_for_status()
            log.info("Notifica Home Assistant inviata: %s", title)
            return True
        except requests.RequestException as exc:
            # Do not print the webhook URL: its identifier is a bearer secret.
            log.error("Invio notifica Home Assistant non riuscito (%s)", type(exc).__name__)
            return False
