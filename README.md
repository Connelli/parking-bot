# parking-bot

Polls TableAir for cancellations on dates you want a parking spot and
reserves one automatically the moment it opens up. Two long-running
processes, sharing `state.db`:

- `parking_bot.runner` — polls TableAir every `poll_interval_seconds` and
  reserves a spot for any watched, not-yet-fulfilled date.
- `parking_bot.webapp` — a small local web GUI for adding/removing watched
  dates, so you don't hand-edit config or restart the poller to change what
  it's looking for.

`parking_bot/client.py` talks to TableAir's undocumented internal API
(reverse-engineered from real traffic). If TableAir changes that API and
something breaks, the notable gotchas and assumptions are documented as
comments right next to the code they affect in `client.py`.

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
