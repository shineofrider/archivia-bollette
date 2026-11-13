from __future__ import annotations

import logging
import time

from .config import load_config
from .service import process_cycle


def main() -> None:
    config = load_config()
    runtime = config["runtime"]
    logging.basicConfig(
        level=config.get("logging", {}).get("level", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("archivia-bollette")
    interval = max(60, int(runtime.get("poll_interval", 300)))
    log.info("Servizio avviato; controllo Gmail ogni %s secondi", interval)
    log.info("Archivio configurato in %s", config["paths"]["archive"])
    while True:
        try:
            process_cycle(config)
        except Exception:  # noqa: BLE001
            log.exception("Errore nel ciclo di controllo; nuovo tentativo al prossimo intervallo")
        time.sleep(interval)


if __name__ == "__main__":
    main()
