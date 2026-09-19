"""TableAir (ta-cloud) HTTP client, reverse-engineered from a captured HAR.
See README.md "API notes" for what's verified vs inferred."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

PARKING_BOOKABLE_TYPE = 3
ACTIVE_BOOKING_STATUS = 1


class AuthError(Exception):
    pass


class ReservationConflict(Exception):
    """Someone else booked the spot before our reserve call landed."""


@dataclass
class Spot:
    id: str  # "prefab" code, e.g. "PS0000045" -- what /api/booking/ expects, not the numeric bookable id
    label: str
    available: bool


class TableAirClient:
    LOGIN_PATH = "/api/auth/"
    VENUES_PATH = "/api/teams/{team_id}/venues/"
    BOOKABLES_PATH = "/api/team/{team_id}/bookables/"
    BOOKINGS_PATH = "/api/teams/{team_id}/bookings/"
    RESERVE_PATH = "/api/booking/"
    CANCEL_PATH = "/api/booking/{reservation_id}/cancel/"
    CSRF_HEADER_NAME = "X-CSRFToken"
    UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timezone_name: str = "Europe/Vilnius",
        work_start: str = "07:00",
        work_end: str = "18:00",
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timezone_name = timezone_name
        self.work_start = work_start
        self.work_end = work_end
        self._client = httpx.Client(base_url=self.base_url, timeout=15.0)
        self.team_id: int | None = None
        self.account_id: int | None = None
        self._bookables_cache: dict[int, list[dict]] = {}

    def login(self) -> None:
        resp = self._client.post(
            self.LOGIN_PATH,
            json={"type": 1, "email": self.username, "password": self.password},
        )
        if resp.status_code != 200:
            raise AuthError(f"login failed: {resp.status_code} {resp.text[:200]}")

        data = resp.json()
        self.account_id = data["id"]
        team_roles = data.get("team_roles") or []
        if not team_roles:
            raise AuthError(f"login succeeded but account {self.account_id} has no team_roles")
        self.team_id = team_roles[0]["team_url"]["id"]
        log.info("logged in as %s (account %s, team %s)", self.username, self.account_id, self.team_id)

    def _csrf_headers(self) -> dict[str, str]:
        # Session + CSRF are cookie-based; the server sets them on login and
        # httpx's client-level cookie jar carries them automatically. We just
        # need to echo the CSRF one back as a header on unsafe requests.
        for cookie in self._client.cookies.jar:
            if "csrf" in cookie.name.lower():
                return {self.CSRF_HEADER_NAME: cookie.value}
        return {}

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = kwargs.pop("headers", {}) or {}
        if method.upper() in self.UNSAFE_METHODS:
            headers = {**headers, **self._csrf_headers()}
        resp = self._client.request(method, path, headers=headers, **kwargs)
        if resp.status_code in (401, 403):
            log.warning("session expired or CSRF rejected (%s), re-authenticating", resp.status_code)
            self.login()
            if method.upper() in self.UNSAFE_METHODS:
                headers = {**headers, **self._csrf_headers()}
            resp = self._client.request(method, path, headers=headers, **kwargs)
        return resp

    def _day_window_utc(self, date: str) -> tuple[str, str]:
        # A full-day booking is a fixed local business-hour window (default
        # 07:00-18:00), not the whole calendar day.
        tz = ZoneInfo(self.timezone_name)
        day = datetime.strptime(date, "%Y-%m-%d")
        start_h, start_m = (int(x) for x in self.work_start.split(":"))
        end_h, end_m = (int(x) for x in self.work_end.split(":"))
        start_local = day.replace(hour=start_h, minute=start_m, tzinfo=tz)
        end_local = day.replace(hour=end_h, minute=end_m, tzinfo=tz)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        return (
            start_local.astimezone(timezone.utc).strftime(fmt),
            end_local.astimezone(timezone.utc).strftime(fmt),
        )

    def list_venues(self) -> list[dict]:
        resp = self._request("GET", self.VENUES_PATH.format(team_id=self.team_id), params={"limit": 10000})
        resp.raise_for_status()
        return resp.json()["results"]

    def list_parking_spots(self, venue_id: int | str) -> list[dict]:
        """[{id, prefab, name}] for permitted parking spots in a venue.
        Cached per venue for the client's lifetime."""
        venue_id = int(venue_id)
        if venue_id in self._bookables_cache:
            return self._bookables_cache[venue_id]

        resp = self._request(
            "GET",
            self.BOOKABLES_PATH.format(team_id=self.team_id),
            params={
                "return_field": ["id", "prefab", "name", "bookable_type", "permitted"],
                "limit": 100000,
                "venue": venue_id,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("next"):
            log.warning("bookables list for venue %s is paginated, only using first page", venue_id)

        spots = [
            b for b in data["results"] if b.get("bookable_type") == PARKING_BOOKABLE_TYPE and b.get("permitted")
        ]
        self._bookables_cache[venue_id] = spots
        return spots

    def _occupied_bookable_ids(self, venue_id: str | int, start: str, end: str) -> set[int]:
        # Filtering by a repeated `bookable=<id>` per spot does NOT work --
        # Django's filter backend only keeps the last repeated value, so it
        # silently matches almost nothing. `venue` returns every occupied
        # spot in the venue in one request instead (verified against real
        # bookings -- see README "API notes").
        resp = self._request(
            "GET",
            self.BOOKINGS_PATH.format(team_id=self.team_id),
            params={
                "venue": venue_id,
                "from_date": start,
                "to_date": end,
                "status": ACTIVE_BOOKING_STATUS,
                "limit": 10000,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("next"):
            log.warning("bookings list for window %s-%s is paginated, results may be incomplete", start, end)
        return {b["bookable"] for b in data["results"]}

    def get_availability(self, date: str, location_id: str | int) -> list[Spot]:
        spots = self.list_parking_spots(location_id)
        start, end = self._day_window_utc(date)
        occupied = self._occupied_bookable_ids(location_id, start, end)
        return [Spot(id=s["prefab"], label=s["name"], available=s["id"] not in occupied) for s in spots]

    def reserve(self, spot_id: str, date: str) -> str:
        """Book `spot_id` (a prefab code, e.g. "PS0000045") for `date`.
        Returns the booking id on success.

        The exact shape of a "someone else already booked this" conflict is
        unconfirmed -- the reference HAR only captured a successful booking.
        Treating any 400/409 as a conflict is a best guess.
        """
        start, end = self._day_window_utc(date)
        resp = self._request(
            "POST",
            self.RESERVE_PATH,
            json={
                "account": self.account_id,
                "bookable": spot_id,
                "start": start,
                "end": end,
                "cancellation_disabled": False,
                "notify_host": True,
            },
        )
        if resp.status_code in (400, 409):
            raise ReservationConflict(f"{spot_id} on {date}: {resp.status_code} {resp.text[:300]}")
        resp.raise_for_status()
        return str(resp.json()["id"])

    def cancel(self, reservation_id: str) -> None:
        resp = self._request(
            "GET", self.CANCEL_PATH.format(reservation_id=reservation_id), params={"remove_single": "true"}
        )
        resp.raise_for_status()

    def close(self) -> None:
        self._client.close()
