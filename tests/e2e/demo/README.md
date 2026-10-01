# Demo scenario: README screenshots with invented data

A repeatable demo of Claude Fleet on a throwaway Home Assistant and Mosquitto in Docker,
loaded with an invented but plausible working day, and screenshotted in headless Chrome.
Everything in it is made up: the computers, sessions, repos, PRs, token counts, plan usage
and history. Nothing touches a real Home Assistant, a real broker or a real `~/.claude`.

## Running it

```bash
export CF_DEMO_DIR=/some/scratch/folder     # where all runtime state goes (default: $TMPDIR/claude-fleet-demo)
tests/e2e/demo/up.sh          # containers, onboarding, MQTT, the card, the package: installed and empty
tests/e2e/demo/scenario.sh    # the invented day (about 2 minutes, mostly waiting for a stale session)
tests/e2e/demo/shots.sh       # screenshots into tests/e2e/demo/out/ (ignored by git): three tabs at 1600px, the whole Sessions tab at 390px
tests/e2e/demo/down.sh        # containers, network and CF_DEMO_DIR, all gone
```

Run `shots.sh` within **10 minutes** of `scenario.sh`: the ended session is removed by the
package's cleanup after that, as on a real install. Re-running `scenario.sh` resets the day
(new "now", same invented story). `up.sh` is safe to re-run; it checks every step.

Needs: Docker (OrbStack is fine), `jq`, `git`, `sqlite3`, `curl`, the Mosquitto clients,
`python3` (up.sh makes a venv with `websockets` and `pyyaml` in `CF_DEMO_DIR`), and Google
Chrome or Chromium (`CHROME=` for another path). Scripts run under macOS's `/bin/bash` 3.2
and on Linux.

Settings, all optional: `CF_DEMO_NAME` (container prefix, default `cf-demo`),
`CF_DEMO_HA_PORT` (8123), `CF_DEMO_MQTT_PORT` (1883), `CF_DEMO_HA_IMAGE` (the pinned
2026.9.4) and `CF_DEMO_FAST=1` (skip the wait for the burn rate, which only the screenshots
need). A second demo beside a running one needs its own name, ports and `CF_DEMO_DIR`; the
end-to-end test (`../run.sh`) runs as `cf-e2e` on 8124 and 1884 for that reason.

Open the demo at <http://localhost:8123/claude-fleet/sessions> (or your `CF_DEMO_HA_PORT`). The login is `demo`, with the
password in `$CF_DEMO_DIR/secrets/ha_password`. Every credential is generated at run time
and kept in `$CF_DEMO_DIR/secrets/` (mode 600).

## What `up.sh` does

1. `eclipse-mosquitto:2` as `cf-demo-mqtt` (`$CF_DEMO_NAME-mqtt`), password logins only: `homeassistant` and
   `claude-mqtt`. The password file is hashed in place with `mosquitto_passwd -U`, so no
   password is ever a process argument. Published on `127.0.0.1:1883` (`CF_DEMO_MQTT_PORT`).
2. `configuration.yaml` with HA's defaults plus the README's `packages:` and `lovelace:`
   keys; `claude_fleet.yaml` and `claude_fleet_quip.yaml` in `packages/`, the dashboard in
   `dashboards/`. History explorer card v1.0.51 in `/config/www`, checksum-verified.
3. Home Assistant **2026.9.4** (pinned; `CF_DEMO_HA_IMAGE` to change) as `cf-demo-ha` on
   `127.0.0.1:8123` (`CF_DEMO_HA_PORT`).
4. Onboarding through its API: owner `demo`, the remaining steps, a refresh token (for the
   browser) and a long-lived token (for the scripts). Time zone America/Los_Angeles.
5. The MQTT integration through its config flow; the card registered as a module resource.
6. HA's config check, a restart, and a check that the package's sensors exist.

## The scenario

Three computers, each a fake home in `$CF_DEMO_DIR/homes/`, with the hook installed by
`HOME=<fake home> ./install.sh` and its own `ha-status.env` pointed at the demo broker:

| Computer | Stale limit | RTK |
|---|---|---|
| studio | 20 min (always on) | on, with a ledger |
| laptop | 0 (never stale) | installed, hook off |
| mini | 1 min (so one session goes stale for real) | on, with a ledger |

Six sessions, each played through the **real hook** with `../ev.sh`:

| Session | Computer | State | Model | Notes |
|---|---|---|---|---|
| Migrate billing webhooks to v2 | studio | 🟠 waiting on you (`AskUserQuestion`) | Opus 5.5 | began yesterday; its PR #88 is yesterday's |
| Add offline mode to the recipe app | studio | 🔵 working | Opus 5.5 (+ a Haiku subagent) | PR #214; context past 200k, so on the 1M scale |
| Backfill search index for archived orders | mini | 🔴 stale (working, silent past 1 min) | Opus 5.5 | one auto compaction |
| Fix flaky login test | laptop | 🔵 working | Sonnet 5.5 | PR #1042; context at 88% |
| Tidy up the CI cache keys | mini | 🟢 idle | Haiku 4.5 | |
| Bump dependencies in the CLI | laptop | ⚪ ended | Sonnet 5.5 | PR #57 |

About 67M tokens today, 97% of them cache reads. The transcripts are generated at run time
(`gen.py`): each response written once per content block with the same id, as Claude Code
does, `pr-link` records re-emitted every turn, one `compact_boundary`, CLI version 2.1.284.

**How each thing gets there:**

- **States** are real hook events. The stale one is a genuinely silent session on the
  computer with a 1-minute limit; `scenario.sh` waits for it.
  The plan burn rate needs five minutes of readings before the session gauge's pace is
  known; ten are seeded into the restore state, so it is known on HA's first minute tick,
  and `scenario.sh` waits for it (not with `CF_DEMO_FAST=1`). It then plays the ended session last,
  so that session is still inside the cleanup's 10-minute grace for the screenshots.
- **The day's counters** (minutes in the current state, prompts today, minutes waited) are
  seeded into the hook's own state file before each session's last event, so the "For"
  column reads like a day in progress rather than two minutes old.
- **"Working on"** comes from a fake `claude` (`fake-claude`, first on the hook's PATH): it
  answers the hook's `claude -p` with a `SUMMARY:` line picked by the session's last
  prompt, and `--version` with the transcripts' version. `SUMMARY_ENABLED=1`. Throttling is
  the hook's default `SUMMARY_THROTTLE` (600 s): one summary per session, on its first Stop.
  `fake-rtk` stands in for `rtk --version` on hosts without RTK.
- **Plan usage** is four template helpers made through HA's config flow and retitled
  `clawdmeter`. The package finds its source with `integration_entities('clawdmeter')`, which
  matches a config entry's title, so a YAML template sensor (no config entry) could not be
  found. The helpers are templates on the clock, so the gauges, the burn rate and "At this
  rate" stay live. The 5-hour window climbs from 8% by 0.19 points a minute. It was 40%
  when the scenario ran and projects about 65% at its reset, so the **session gauge is
  green**. The week reads 66% with 60% of it gone and 2 days 20 hours to go. That is 6
  points over pace, the middle of `sensor.claude_weekly_pace`'s 3–9 band, so the **weekly
  gauge is yellow** and "At this rate" says "6 over pace". It stays 6–7 over for several
  hours after the scenario runs.
- **History**: 30 days of invented hourly figures (daytime peaks, quiet nights and weekends,
  a few busy evenings) for every entity the charts draw, written as recorded states and,
  except for today, imported with `recorder/import_statistics` (see below). Today's figures
  end on the live values. The weekly quota climbs through each week and drops to 0 at each
  reset. The resets fall on the live source's reset time, and earlier weeks peak at 90, 73,
  61 and 84%. The 5-hour line keeps its own sawtooth.
- **Records, today's peak and the weekly points by day** are seeded into HA's restore
  state, derived from the same history, so the records card shows past dates.
- **RTK**: a `history.db` in two fake homes, with a week of commands whose capped savings
  follow the history.
- **The aside**: `input_text.claude_fleet_quip` set directly to one invented line. No LLM.
- Invented PR links, remotes and paths all use `example.invalid`; the git identity is
  anonymous.

## Why the history is recorded states as well as statistics

The history explorer card draws recorded states wherever it finds any. It falls back to
long-term statistics only in some loading orders. With statistics alone, the days before
the demo started stayed blank, even with statistics under them. With the entities excluded
from the recorder, every chart stayed blank. A real install has ten days of states (the
recorder's default) with statistics behind them, so the demo gets the same.

`gen.py inject` writes the states while HA is stopped, from a throwaway container running
the HA image's own Python. `gen.py restore` runs there too: on Linux, HA runs as root and
owns what it wrote in `/config`, so the host user could not write the restore state. **Don't point the host's sqlite at the database.** Across
Docker's shared folder its locking doesn't carry over, and it corrupted the database once.

Today's daily counters get only their midnight zero. The live figure arrives when HA
starts, so today's bar is exactly that rise.

Statistics are imported for every day **except today**. HA compiles each hour it was
running for itself, from the recorded states. An imported row for the same hour makes that
compile fail: "UNIQUE constraint failed: statistics.metadata_id, statistics.start_ts",
logged at every hour and every restart.

## Files

| File | What it does |
|---|---|
| `up.sh` | Containers up, to an installed and empty Claude Fleet. |
| `scenario.sh` | Loads the invented day. |
| `shots.sh` | Headless Chrome, its own profile; the three tabs at 1600px, the whole Sessions tab at 390px. |
| `down.sh` | Removes the containers, network and `CF_DEMO_DIR`, and only a folder `up.sh` made. |
| `lib.sh` | Settings and helpers for the scripts. |
| `ha.py` | HA API: onboarding, the MQTT flow, resources, restart, websocket calls. |
| `gen.py` | The scenario's data and its loading: transcripts, history, restore state, plan helpers, sessions. |
| `shots.py` | The screenshot driver (cdp.py's approach): logs in, sizes to the page, reports error cards. |
| `fake-claude`, `fake-rtk` | The stand-ins described above. |

## Known limits

- The **Waiting on you** tile counts only since the scenario ran (it comes from recorded
  history of `binary_sensor.claude_anyone_waiting`). The **Today** card's minutes come from
  the seeded counters.
- Idle and waiting sessions are cleaned up after two hours of silence, and the ended one
  after ten minutes, as on a real install. Re-run `scenario.sh` for a fresh day.
