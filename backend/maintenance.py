from __future__ import annotations

import signal
import threading

from .app.config import settings
from .app.costs import seed_default_rates
from .app.database import SessionLocal, init_database
from .app.media_lifecycle import meter_all_media, purge_expired_media


class MaintenanceService:
    def __init__(self) -> None:
        self.stop_requested = threading.Event()

    def stop(self, *_args) -> None:
        self.stop_requested.set()

    def run_cycle(self) -> tuple[int, int]:
        with SessionLocal() as db:
            metered_units = meter_all_media(db)
            removed_objects = purge_expired_media(db)
        return metered_units, removed_objects

    def run(self) -> None:
        init_database()
        with SessionLocal() as db:
            seed_default_rates(db)
        while not self.stop_requested.is_set():
            self.run_cycle()
            self.stop_requested.wait(settings.maintenance_interval_seconds)


def main() -> None:
    service = MaintenanceService()
    signal.signal(signal.SIGTERM, service.stop)
    signal.signal(signal.SIGINT, service.stop)
    service.run()


if __name__ == "__main__":
    main()
