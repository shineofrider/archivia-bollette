"""Mail ingestion scaffold.

The IMAP and provider-specific flows are deliberately fail-closed until implemented
and tested with the account's actual messages and IrenYou portal.
"""
import logging
import os

log = logging.getLogger(__name__)


def run_cycle(config: dict) -> None:
    required = ("IMAP_USERNAME", "IMAP_APP_PASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        log.warning("Configurazione Gmail incompleta; manca: %s", ", ".join(missing))
        return

    # TODO: implement IMAP TLS connection, UID-based incremental search, MIME parsing,
    # PDF validation, SHA-256 de-duplication, persistent transaction state, and HA events.
    # Alperia messages should be downloaded directly from the matching email.
    # Iren messages should trigger Playwright only after sender+subject match.
    log.warning("Connessione IMAP non ancora abilitata: completare l'integrazione prima dell'uso.")
