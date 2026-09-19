# parking-bot

Polls TableAir for cancellations on dates you want a parking spot and
reserves one automatically the moment it opens up. Two long-running
processes, sharing `state.db`:

- `parking_bot.runner` — polls TableAir every `poll_interval_seconds` and
  reserves a spot for any watched, not-yet-fulfilled date.
- `parking_bot.webapp` — a small local web GUI for adding/removing watched
  dates, so you don't hand-edit config or restart the poller to change what
  it's looking for.

`parking_bot/client.py` is wired up against a real captured HAR (ta-cloud /
`ld.tableair.com`, Angular front-end). See **API notes** below for what's
directly confirmed vs. inferred.

## API notes (from the captured HAR)

Confirmed directly from traffic:
- Auth: `POST /api/auth/` with `{"type":1,"email","password"}`. No token is
  returned in the JSON body — session + CSRF are handled entirely via
  cookies the server sets on the response (Chrome's HAR export redacts
  `Set-Cookie`/`Cookie` values, so the exact cookie names aren't visible,
  but httpx's client-level cookie jar handles this transparently).
- State-changing `POST /api/booking/` requires an `X-CSRFToken` header
  matching a `*csrf*`-named cookie. The `GET .../cancel/` call does **not**
  send that header — consistent with Django only enforcing CSRF on unsafe
  methods.
- Parking spots are `bookable_type=3` inside a venue (`GET
  /api/team/{team_id}/bookables/?...&venue={venue_id}`). Each has a numeric
  `id` and a `prefab` code (e.g. `"PS0000045"`); **`reserve()` needs the
  `prefab` code**, not the numeric id.
- A full-day booking is a fixed local business-hour window — the sample
  reservation was `04:00Z`–`15:00Z` = `07:00`–`18:00` Europe/Vilnius.
- Existing bookings across a whole venue in one request:
  `GET /api/teams/{team_id}/bookings/?venue=<id>&from_date=...&to_date=...&status=1`.
  `status:1` = active, `status:2` = cancelled. This is how `get_availability()`
  finds every occupied spot in a single call.
  **Gotcha:** repeating `bookable=<id>` once per spot to filter by many ids
  in one request does **not** work — Django's filter backend only keeps the
  *last* repeated value, so it silently matches almost nothing. `venue`
  works correctly instead and is what `client.py` uses.

Inferred, not directly observed:
- A double-booking conflict is treated as any `400`/`409` response from
  `POST /api/booking/`. The reference HAR only captured a successful (`201`)
  reservation, never a conflict, so the real error shape is unconfirmed.
  If a real conflict happens during testing, check the logged response body
  and tighten `ReservationConflict` handling in `client.py` if needed.
- The `booking_status_time` field on the bookables endpoint looked like it
  might report future-date availability directly, but in the capture it
  always reflected "right now" regardless of the date requested — so
  `get_availability()` deliberately does *not* use it, and cross-checks
  bookings by date range instead.

## Capturing more real traffic (only needed if something above turns out wrong)

1. Open TableAir in your browser, open DevTools → **Network** tab, filter to
   **Fetch/XHR**, and check **Preserve log**.
2. Log out and back in (captures the login request), then open the
   availability/calendar view for a date. If possible, make a test
   reservation and then cancel it, so the reserve and cancel calls both get
   captured too.
3. Right-click the request list → **Save all as HAR with content**.
4. **Before sharing the HAR file**, redact your password from the login
   request body (find/replace it with a placeholder) — HAR files store
   full request bodies and session cookies in plaintext. Treat the file as
   a secret.
5. Alternative: right-click each key request individually (login,
   availability check, reserve, cancel) → **Copy** → **Copy as cURL**, and
   share those instead of a full HAR.

## Setup

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy config.example.yaml config.yaml
# edit config.yaml with real tableair credentials and telegram bot token/chat id
```

## Running

```
python -m parking_bot.webapp             # GUI at http://localhost:5000 -- add/remove watch dates here
python -m parking_bot.runner --dry-run   # logs what it would reserve, books nothing
python -m parking_bot.runner             # actually reserves
```

Run both `webapp` and `runner` at the same time (two terminals, or two
services once deployed) — they share `state.db`. The GUI lists venues and
spots by calling TableAir directly, so there's nothing to hand-configure
beyond `config.yaml`'s credentials.

## Verification checklist before trusting this with a real date

1. Open the GUI, add a date you know already has some bookings (e.g. one
   you saw taken in the TableAir UI).
2. `--dry-run` the runner and confirm those specific spots are reported
   unavailable and the rest available.
3. `--dry-run` against a fully-booked date — confirm it reports zero free
   spots without erroring.
4. Turn off `--dry-run` for one date you actually want and confirm a real
   reservation lands (check the TableAir UI, the GUI's watch list, and the
   Telegram message).
5. The GUI's "Recent activity" table (or
   `sqlite3 state.db "select * from attempt_log order by id desc limit 20;"`)
   shows the attempt history.

## Deploying to a Raspberry Pi (systemd)

Assumes you've already run the verification checklist above manually on
some machine and trust the logic. On the Pi:

```
git clone <this repo> ~/parking-bot   # or scp/rsync it over
cd ~/parking-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml     # fill in real credentials
cp deploy/parking-bot.env.example parking-bot.env
```

Test both manually in the foreground first, same as on your PC:

```
.venv/bin/python -m parking_bot.webapp
.venv/bin/python -m parking_bot.runner --dry-run
```

Once that looks right, install the services (`deploy/*.service` assume the
repo lives at `/home/pi/parking-bot` run as user `pi` — edit `User=` and the
paths inside both files first if that's not your setup):

```
sudo cp deploy/parking-bot-runner.service deploy/parking-bot-webapp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now parking-bot-webapp
sudo systemctl enable --now parking-bot-runner
```

`parking-bot.env` defaults `RUNNER_ARGS` to `--dry-run`, so the service
starts in test mode. Watch it with:

```
journalctl -u parking-bot-runner -f
```

Once you're confident, flip it to live without touching the unit file:

```
sudo nano parking-bot.env        # set RUNNER_ARGS= (empty)
sudo systemctl restart parking-bot-runner
```

Both services restart automatically on crash or reboot (`Restart=on-failure`,
`WantedBy=multi-user.target`). The GUI has no login and binds to
`0.0.0.0:5000` — fine on a trusted home LAN, but don't port-forward it to
the internet.

## Remote access (phone, away from home)

The GUI has no authentication, so don't expose it directly to the internet
(no router port-forwarding). Use [Tailscale](https://tailscale.com) (free
for personal use) instead — it's a WireGuard mesh VPN, so your phone and Pi
talk to each other directly and encrypted, with no public-facing service or
open router ports at all:

1. Create a Tailscale account (Google/Microsoft/GitHub/email login).
2. On the Pi:
   ```
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```
   Open the printed URL once in any browser to authorize the Pi.
3. Install the Tailscale app on your phone, sign into the same account,
   turn it on.
4. Find the Pi's address with `tailscale ip -4`, or enable **MagicDNS** in
   the [admin console](https://login.tailscale.com/admin/dns) for a stable
   name like `raspberrypi.tailXXXX.ts.net` instead of an IP.
5. From your phone, with Tailscale on, open `http://<that-address>:5000` —
   same webapp as on the home LAN.

Nothing on the router needs changing, and nothing in this repo needs
changing either — the webapp already binds `0.0.0.0`, which is what lets
Tailscale's virtual network interface reach it.
