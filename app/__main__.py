import logging
import os
import time
from pathlib import Path

import yaml

from .service import run_cycle


def main() -> None:
    config_path = Path(os.getenv("CONFIG_FILE", "/config/config.yaml"))
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    interval = int(os.getenv("POLL_INTERVAL", config.get("mail", {}).get("poll_interval_seconds", 300)))
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", config.get("logging", {}).get("level", "INFO")),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("archivia-bollette")
    log.info("Servizio avviato. Controllo casella ogni %s secondi.", interval)

    while True:
        try:
            run_cycle(config)
        except Exception:
            log.exception("Errore durante il ciclo di acquisizione")
        time.sleep(max(60, interval))


if __name__ == "__main__":
    main()
