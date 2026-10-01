# End-to-end test

A real test: it builds a throwaway Home Assistant and Mosquitto in Docker, installs Claude
Fleet the README's way, drives six invented sessions through the real hook, and then asserts
on what Home Assistant shows. It fails when the product breaks, and every one of its checks
has a control that proves it can.

It is the demo in [`demo/`](demo/README.md) (the same scripts make the README's screenshots)
plus `check.py`, run by `run.sh`.

## Running it

```bash
tests/e2e/run.sh                # selftest, up, scenario, check, down: about 2 minutes once the images are pulled
tests/e2e/run.sh check          # one phase (or several, in order) against a demo already up
CF_E2E_KEEP=1 tests/e2e/run.sh  # leave the demo running afterwards, to look at it
```

Needs Docker (OrbStack is fine), `jq`, `git`, `sqlite3`, `curl`, `python3` and the Mosquitto
clients: `brew install mosquitto jq` on a Mac, `sudo apt-get install mosquitto-clients jq
sqlite3` on Ubuntu. The scripts run on macOS (its `/bin/bash` 3.2) and on Linux.

The phases:

| Phase | What it runs |
|---|---|
| `selftest` | `check.py --self-test`: every check against synthetic good and bad input. No Home Assistant needed. |
| `up` | `demo/up.sh`: the containers, onboarding, MQTT, the history explorer card, the package, the dashboard and an automation from each alert blueprint. |
| `scenario` | `demo/scenario.sh` with `CF_DEMO_FAST=1`: the invented day, about 90 seconds. |
| `check` | `check.py`: the assertions below. |
| `shots` | `demo/shots.sh`: screenshots into `demo/out/` (needs Chrome). Not in the default run. |
| `logs` | Home Assistant's and the broker's logs into `out/`. |
| `down` | `demo/down.sh`: containers, network and the runtime folder, gone. |

With no phases given, a failure still collects the logs and tears down. Every phase's output
is kept in `tests/e2e/out/` (ignored by git), and `check.py` exits non-zero with a list of
what failed.

**It stays clear of anything else on the computer.** Its defaults are containers named
`cf-e2e-*`, Home Assistant on `127.0.0.1:8124`, the broker on `127.0.0.1:1884` and runtime
state in `$TMPDIR/cf-e2e`, so it runs beside a demo started with `demo/up.sh`'s own defaults
(`cf-demo-*`, 8123, 1883). Change them with `CF_DEMO_NAME`, `CF_DEMO_HA_PORT`,
`CF_DEMO_MQTT_PORT` and `CF_DEMO_DIR`. `CF_DEMO_HA_IMAGE` picks another Home Assistant, for
example `ghcr.io/home-assistant/home-assistant:beta`. Nothing reads or writes a real
`~/.claude`, a real broker or a real Home Assistant; keep it that way.

## What it checks

Each check is a pure function from data to a list of failures. The live run gathers the data
from Home Assistant; the self-test feeds the same functions a good case, which must pass,
and bad cases, each of which must fail.

| Check | Fails when | Its control |
|---|---|---|
| **The package loaded** | a key `sensor.claude_*`, `binary_sensor`, `input_*` entity is missing or unavailable, or there are fewer than 30 `sensor.claude_*` | self-test: `sensor.claude_fleet_status` removed; one entity `unavailable` |
| **Sessions** | not exactly the six sessions, each in its final state (waiting on you, working ×2, stale, idle, ended), with its label, prompts and per-session token counts as `gen.py` generated them | self-test: a session in the wrong state, the ended one missing, a seventh, one session's tokens or prompts off |
| **Counts** | running, working, stale, waiting and idle are not 5, 2, 1, 1, 1; `sensor.claude_fleet_status` is not `needs_input`; nobody waiting; setup needed | self-test: each count off, fleet status wrong, anyone-waiting off, setup needed on |
| **The day's sums** | `sensor.claude_tokens_today` or the per-family sensors differ from the sum of the generated sessions; prompts or PRs today differ (yesterday's PR must not count) | self-test: tokens off by one, yesterday's PR counted, a PR missing |
| **The Today card's sums** | its "N sessions · N prompts · N PRs" line disagrees with the generated day | self-test: wrong prompts, no such line, no card |
| **Plan usage** | the plan source is not `ok`, the weekly pace not `tight` (the scenario puts the weekly gauge in the yellow), or the session pace not `ok` | self-test: a stale source, weekly `ok`, session `unknown` |
| **Every markdown card renders** | any of the dashboard's markdown templates (all three views, phone-only cards included) errors with `strict: true` and `report_errors: true` over the websocket, or fewer than 10 cards are found | **live**: `{{ states.sensor.x.attributes.missing.value }}` goes through the same code path first and must come back as an error, and a trivial template must come back clean; self-test: each kind of error reply, and a card walk that must find nested and phone-only cards |
| **Dashboard config** | a history explorer "by day" / "per day" bar graph lacks `interval: daily` inside `options`; `interval` sits on the graph itself (where the card silently ignores it: a real bug, once); any card, graph, graph option, chart entity, visibility condition, section or view has a key its card does not read | self-test: `interval` at graph level, a by-day bar with no interval, a misspelt markdown key, `line_mode` for `lineMode`, an unknown graph option, a stray visibility key, a stray key on a nested card |
| **Alert blueprints** | either automation made from the two blueprints in `blueprints/` is not `on` (HA could not load it), or the waiting alert did not fire during the scenario naming the session left waiting. `up.sh` installs both with a logbook entry as the action, since the demo has no phone | self-test: an automation unavailable, one missing, no alert sent, an alert naming another session |
| **Update check** | switched on, `update.claude_fleet` doesn't appear within 45 seconds, or lacks the package's installed version, a version as the latest, or a link to this repo's releases; switched off, it is still there. This reaches the real GitHub; if GitHub can't answer, the entity still appears (with the installed version as the latest), and the log check lets a rate limit or outage through, but not a 404, which means the URL is wrong | self-test: never appearing, still there after off, the wrong installed version, a tag as the latest, unavailable; a GitHub 403 in the log passes and a 404 fails |
| **Versions** | the package and the dashboard state different versions, or plugin.json and marketplace.json do, or the newest CHANGELOG entry for either isn't that version; or the package Home Assistant loaded isn't the repo's | self-test: each half-bumped case, a file with no version, the repo's own files |
| **Repairs** | any repair issue from `template` or `mqtt`, or one naming Claude | self-test: a `template` and an `mqtt` issue fail, an unrelated one passes |
| **Home Assistant's log** | any `ERROR` or `CRITICAL`; any `WARNING` from Claude Fleet's templates, automations, scripts or MQTT entities (by logger, or naming Claude). The one allowed warning is "Template loop detected", which HA handles itself | self-test: synthetic lines for each (an ERROR with a traceback, a template error, an MQTT "Erroneous JSON", an automation warning, a CRITICAL, an error in a format nobody parses); HA's normal noise, a Template loop and ANSI colour codes must pass |

The expected numbers come from what `gen.py` generated for this run (`sessions.json` and
`gen.SESSIONS`), not from constants in the checker. The counts are templates over every
sensor, which Home Assistant re-renders at most once a second, so the session checks wait up
to a minute (`CF_CHECK_SETTLE`) for them to settle before reporting.

The burn rate needs five minutes of readings before the session pace is known. The scenario
seeds ten minutes of them into Home Assistant's restore state, as it seeds the records, so the
pace is known on the first minute tick; `check.py` still waits up to seven minutes for it
(`CF_CHECK_RATE_WAIT`) and fails if it never comes. `CF_DEMO_FAST=1` skips the scenario's
own wait for it, which only the screenshots need; the real stale wait (70 seconds) stays.

The key-by-card lists in `check.py` are the keys those cards read, as far as a cheap check
can know them. A card that learns a new key needs it added there, or the check reports it as
ignored: that is the point.

## In CI

`.github/workflows/e2e.yml` runs the same phases on `ubuntu-latest`: the Mosquitto clients,
`jq` and `sqlite3` from apt, then `selftest`, `up`, `scenario`, `check`; the screenshots as an
artifact if everything passed; Home Assistant's and the scenario's logs as an artifact if
anything failed; and always `down`. Thirty minutes at most.

It runs on every push to `main` and every pull request touching `homeassistant/`, `blueprints/`, `hooks/`,
`install.sh`, `scripts/` or `tests/e2e/`, against the pinned Home Assistant; weekly against
`stable` and `beta`, to catch a new Home Assistant release breaking the templates before users
do; and by hand (Actions → e2e → Run workflow), with a choice of the three.

## The starting pieces

From the first fresh install (2026-09-30): an agent that had never seen the project installed
it from the main README alone, on a throwaway Home Assistant, and drove it with invented
sessions. `check.py` and the demo grew out of these; they are kept for poking at an install by
hand.

| File | What it does |
|---|---|
| `make-repos.sh DIR` | Two throwaway git repos, one on `main` and one on a feature branch, with an anonymous identity. |
| `ev.sh` | Sends one hook event to the installed hook. Needs `FAKE_HOME`. The demo drives every session through it. |
| `transcripts/*.jsonl` | Two invented transcripts from that first run. |
| `render.py`, `ws.py` | Render every markdown card; send websocket messages. `check.py` does both now. Need `TOKFILE`; assume port 8123. |
| `cdp.py VIEW...` | Loads views in a Chrome on `--remote-debugging-port=9333`; `demo/shots.py` is its successor. |
| `retained.sh ENV OUT` | Lists the retained `claude/#` and `homeassistant/#` topics on a broker. |
| `reference-shots/` | What the three views looked like on that run. |

Still to come: the uninstall steps, with their broker and registry checks.
