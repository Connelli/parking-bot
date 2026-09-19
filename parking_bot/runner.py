from __future__ import annotations

import argparse
import logging
import random
import sys
import time

from .client import AuthError, ReservationConflict, TableAirClient
from .config import Config
from .notifier import TelegramNotifier
from .state import StateStore

log = logging.getLogger(__name__)

MAX_CONSECUTIVE_ERRORS_BEFORE_ALERT = 5


def run_cycle(
    client: TableAirClient,
    state: StateStore,
    notifier: TelegramNotifier,
    error_counts: dict[str, int],
    dry_run: bool,
) -> None:
    for target in state.active_watches():
        date = target.date
        try:
            spots = client.get_availability(date, target.location_id)
        except Exception:
            error_counts[date] = error_counts.get(date, 0) + 1
            log.exception("availability check failed for %s (%d in a row)", date, error_counts[date])
            if error_counts[date] == MAX_CONSECUTIVE_ERRORS_BEFORE_ALERT:
                notifier.send(f"parking-bot: {date} has failed {error_counts[date]} checks in a row, see logs")
            continue

        error_counts[date] = 0
        free = [s for s in spots if s.available]
        log.info("%s: %d/%d spots free", date, len(free), len(spots))
        if target.preferred_spot_ids:
            free = [s for spot_id in target.preferred_spot_ids for s in free if s.id == spot_id]
        if not free:
            continue

        chosen = free[0]
        if dry_run:
            log.info("[dry-run] would reserve %s for %s", chosen.id, date)
            state.log_attempt(date, chosen.id, "dry-run-would-reserve")
            continue

        try:
            reservation_id = client.reserve(chosen.id, date)
        except ReservationConflict as exc:
            log.info("conflict reserving %s for %s: %s", chosen.id, date, exc)
            state.log_attempt(date, chosen.id, "conflict", str(exc))
            continue
        except Exception as exc:
            log.exception("reserve failed for %s on %s", chosen.id, date)
            state.log_attempt(date, chosen.id, "error", str(exc))
            continue

        state.mark_fulfilled(date, reservation_id)
        state.log_attempt(date, chosen.id, "reserved", reservation_id)
        notifier.send(f"parking-bot: reserved spot {chosen.id} for {date} (reservation {reservation_id})")
        log.info("reserved %s for %s -> reservation %s", chosen.id, date, reservation_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll TableAir and grab a parking spot the moment one opens up.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="log what would be reserved without actually booking")
    args = parser.parse_args()

    # Windows consoles default to cp1252, which can't encode the Lithuanian
    # spot names (ė, š, etc.) this API returns.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config = Config.load(args.config)
    client = TableAirClient(
        config.tableair.base_url,
        config.tableair.username,
        config.tableair.password,
        timezone_name=config.timezone,
        work_start=config.work_start,
        work_end=config.work_end,
    )
    notifier = TelegramNotifier(config.telegram.bot_token, config.telegram.chat_id)
    state = StateStore()

    try:
        client.login()
    except AuthError:
        log.exception("initial login failed, check tableair credentials in config.yaml")
        client.close()
        state.close()
        return

    # Runs forever, idling when there's nothing to watch, since watches are
    # added/removed live through the web GUI rather than fixed at startup.
    error_counts: dict[str, int] = {}
    try:
        while True:
            run_cycle(client, state, notifier, error_counts, args.dry_run)
            sleep_for = config.poll_interval_seconds + random.uniform(-config.jitter_seconds, config.jitter_seconds)
            time.sleep(max(1.0, sleep_for))
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        client.close()
        state.close()


if __name__ == "__main__":
    main()
