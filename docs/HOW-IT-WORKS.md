# How it works

The computers push; Home Assistant needs no custom integration. MQTT discovery creates the
session sensors, and everything else is template sensors and YAML. The messages themselves
don't need Home Assistant at all: [`docs/PROTOCOL.md`](PROTOCOL.md) documents every topic
and field, for anyone building a different view.

```
hooks/claude-ha-status.sh                         Claude Code hook -> MQTT
.claude-plugin/, hooks/hooks.json                 the Claude Code plugin and its marketplace
install.sh                                        installs the hook without the plugin
ha-status.env.example                             per-computer settings template
scripts/diagnose.sh                               checks a computer's setup, with a test publish
homeassistant/packages/claude_fleet.yaml          sensors, plan usage, records, tokens, recap, cleanup
homeassistant/dashboards/claude_fleet.yaml        the dashboard (YAML mode), three tabs
homeassistant/packages/claude_fleet_awtrix.yaml   optional: the desk display
homeassistant/packages/claude_fleet_quip.yaml     optional: the aside
examples/aside_author_ai_task.yaml                an aside author on HA's AI Task
```

## Two ways to install the hook

- **The plugin** stores its settings in Claude Code, with the MQTT password as a sensitive
  value in Claude Code's secure storage. Rows left blank fall back to `~/.claude/ha-status.env`,
  and anything beyond the basics goes there too. Updates arrive through `/plugin`, and only
  new sessions use them.
- **The script** copies the hook to `~/.claude/hooks/` and adds it to `settings.json`; its
  settings, password included, live in `~/.claude/ha-status.env`, readable only by you.
  Sessions already running pick up an updated hook at their next event, since it is read from
  disk each time.
- **Not both:** with the script's hook in `settings.json`, the plugin steps aside rather than
  report every event twice. To move from the script to the plugin, run
  `./install.sh --uninstall` (it removes only this hook, with a backup), then install the
  plugin.

Claude Code reads its hooks when a session starts, so after a first install only new
sessions report. Resuming one with `claude --resume` counts as a start.

**The MQTT password stays off the command line.** Anyone logged in to a computer can list
every running process with its arguments, so the hook never passes the password as one. It
hands it to `mosquitto_pub` in a private file that exists only for the moment of each
publish.

## The flow

Claude Code fires hooks → `claude-ha-status.sh` publishes MQTT discovery, state and
attributes → Home Assistant creates one sensor per session → template sensors add them up →
the dashboard, the status light and the recap. Nothing flows back to the computers; they only
report.

## What you see

Tracking is per **session**, not per PR: a session can touch several PRs in its life.

Session states: `working` · `needs_input` · `idle` · `ended`. A `working` session silent past
its computer's `CLAUDE_HA_STALE_MINUTES` shows as **stale**.

**The daily recap** is a Home Assistant notification (the bell in the sidebar, not a phone
alert) at 6 PM: sessions, prompts, PRs and time spent waiting on you, one line per session.
Each day's replaces the last, and days with no sessions are skipped. Switch it off, or change
the time, on the dashboard's **Setup** tab.

**Personal records** (`sensor.claude_fleet_records`) keep six bests forever: most sessions
at once, most PRs, prompts and tokens in a day, the longest solo run (one session working
without needing you), and the longest you kept one waiting. A solo run only counts while
the session is still reporting, so a laptop asleep mid-task cannot hold the record.

## When a session disappears from the dashboard

Cleanup runs every 5 minutes and applies three rules, each for a different way a session stops
mattering:

| rule | knob | catches |
|---|---|---|
| ended cleanly | `ended_grace_min: 10` | a normal exit: the row lingers ten minutes |
| abandoned | `abandoned_silent_hours: 2` | **not `working`** and silent: app quit, crash, closed lid |
| backstop | `silent_max_hours: 24` | anything else that goes quiet |

The `!= working` guard on the middle rule is deliberate: a session genuinely mid-task can be
silent for hours on a long tool run. That is what the **stale** indicator is for, and it must
not be deleted. Before removal, a session's counters are archived to
`sensor.claude_archived_today`, so the daily recap and the Today card still include it, marked
`_(closed)_`.

A session killed without a `SessionEnd` never reaches `ended`, which is the whole reason the
middle rule exists.
