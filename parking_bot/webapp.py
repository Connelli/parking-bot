from __future__ import annotations

import argparse
import logging

from flask import Flask, redirect, render_template, request, url_for

from .client import AuthError, TableAirClient
from .config import Config
from .state import StateStore

log = logging.getLogger(__name__)


def create_app(config_path: str = "config.yaml") -> Flask:
    app = Flask(__name__)

    config = Config.load(config_path)
    client = TableAirClient(
        config.tableair.base_url,
        config.tableair.username,
        config.tableair.password,
        timezone_name=config.timezone,
        work_start=config.work_start,
        work_end=config.work_end,
    )
    client.login()
    state = StateStore()

    def venues_with_spots():
        venues = []
        for venue in client.list_venues():
            spots = client.list_parking_spots(venue["id"])
            if spots:
                venues.append({"id": venue["id"], "name": venue["name"], "spots": spots})
        return venues

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            venues=venues_with_spots(),
            watches=state.list_watches(),
            attempts=state.recent_attempts(),
        )

    @app.post("/watches")
    def add_watch():
        state.add_watch(
            date=request.form["date"],
            location_id=request.form["location_id"],
            preferred_spot_ids=request.form.getlist("preferred_spot_ids"),
        )
        return redirect(url_for("index"))

    @app.post("/watches/<date>/delete")
    def delete_watch(date: str):
        state.remove_watch(date)
        return redirect(url_for("index"))

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Web GUI for managing parking-bot watch dates.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        app = create_app(args.config)
    except AuthError:
        log.exception("initial login failed, check tableair credentials in config.yaml")
        return

    # LAN-only tool -- debug=True would expose a remote code-execution console, never enable it.
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
