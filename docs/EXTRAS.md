# Optional extras

Each of these is independent: add any of them, and its part of the dashboard appears. The
[Setup tab](../README.md#3-check-it) shows which are working. The desk display and the aside
are packages of their own: copy them to `/config/packages/` beside `claude_fleet.yaml` and
restart.

## PR numbers

Install `gh` on each computer and run `gh auth login`. The hook reads PRs from the session's
transcript and uses `gh` to fill in the rest; they appear in the sessions table and the day's
totals once a session opens one.

## RTK savings

[RTK](https://github.com/rtk-ai/rtk) filters what shell commands print before Claude reads it.
With RTK and `sqlite3` on a computer, and RTK's Claude Code hook turned on (`rtk init -g`), the
fleet hook finds RTK's ledger by itself and reports what it saved there, per computer and by
day. Without RTK, the Tokens tab shows a short card saying what it would show. The figure is
capped at what Claude Code would actually have shown the model; see
[Counting tokens](LESSONS.md#counting-tokens) for why. If you raised Claude Code's
`BASH_MAX_OUTPUT_LENGTH`, set the same in `ha-status.env`.

## Plan usage

It comes from any Home Assistant integration that reports your Claude plan limits.
[Clawdmeter](https://github.com/corgan2222/ha-clawdmeter) is found on its own. For another,
set four entity ids on `sensor.claude_plan_source` in the package's `PLAN USAGE SOURCE` block:
session and weekly usage (%) and their reset times. That sensor's state is the source's
health, and the Sessions tab warns when it stops reporting. The burn rate, time to limit and
today's peak are derived from those four, so every source gets the same arithmetic. Without
one, the plan cards hide themselves.

## Alerts

Claude Fleet sends nothing to your phone by itself: what deserves a buzz, and where, is
yours to decide, as usual in Home Assistant. Two blueprints make the common ones a couple of
clicks. Import one with its button (or Settings → Automations & scenes → Blueprints →
Import blueprint, with the file's GitHub link), then **Create automation** from it and pick
your phone. Each sends to phones with the Home Assistant Companion app, or runs any other
actions you choose (another notifier, a speaker, a light), and a tap on it opens the
dashboard.

**A session is waiting on you.** When a session has waited a couple of minutes (you choose
how long), it names the waiting sessions: "Fix flaky login test (laptop)". You get a new
alert only when someone new starts waiting, and it disappears from the phone once nobody is.

[![Open your Home Assistant instance and show the blueprint import dialog with this blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fleftover-salmon%2Fhome-assistant-claude-fleet%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fclaude_fleet%2Fsession_waiting.yaml)

**Plan limits running hot.** When the session or weekly limit is on course to run out before
it resets, using the same pace that colours the gauges: tight (yellow) or over (red), your
choice. "21% used, on course for 96% by the reset in 3h 53m." It alerts only when things
get worse, never on the way back down or after a restart, and a limit hovering around the
line stays quiet for an hour (you choose) before alerting again. Needs [plan usage](#plan-usage).

[![Open your Home Assistant instance and show the blueprint import dialog with this blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fleftover-salmon%2Fhome-assistant-claude-fleet%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fclaude_fleet%2Fplan_limits.yaml)

Both are plain Home Assistant automations once created: change them, or copy the triggers
into your own. The sensors they read are `sensor.claude_sessions_waiting`,
`binary_sensor.claude_anyone_waiting`, `sensor.claude_session_pace` and
`sensor.claude_weekly_pace`.

## Update check

Turn on **Update check** on the Setup tab and a new version of the Home Assistant files
appears in **Settings → Updates**, beside Home Assistant's own, with its release notes and
a link to it. Once a day (and at startup), Home Assistant asks GitHub's public API for this
project's newest "Home Assistant files" release. Nothing is sent but that request: no
token, no account, and nothing about your setup. It is the only part of Claude Fleet that
reaches the internet, which is why it's off until you turn it on.

There is no Install button. Updating means copying files into `/config`, which only code
running inside Home Assistant could do, and Claude Fleet runs none: copy the files the
release names, then restart. **Skip** works as for any update. Hook versions aren't
included: Claude Code offers those itself, and the Setup tab flags a computer that is
behind. Turning the switch off removes the entity.

## The status light

For a plain smart bulb, set `STATUS LIGHT` in `claude_fleet.yaml` to the bulb's entity id and
turn on the **Status light (bulb)** switch on the Setup tab. An AWTRIX pixel clock needs nothing here;
it has its own package, below.

## The desk display

`packages/claude_fleet_awtrix.yaml` puts the fleet on an **AWTRIX NG** pixel clock (a Ulanzi
TC001 works): the top-right corner light in the fleet's colour (amber blinks, red breathes),
and the name of any session that starts waiting on you. Session and weekly usage, each with a
progress bar in its pace colour, join the clock's own screens in rotation. (The aside was
tried there and dropped: a sentence scrolling across 32 pixels is not readable at a glance.)
It finds the clock itself, as the first device with model "AWTRIX NG", so there is nothing to
configure beyond pointing the clock at your MQTT broker with Home Assistant discovery on. Turn
it on with the **Desk display** switch on the Setup tab.

A notification is named after its session and withdrawn when you answer it, so a queued one
never scrolls past after the fact. The corner colour is published retained, so a clock that
reboots gets it back at once; notifications are not, so a reconnect never replays one.

The clock is yours, not the fleet's: this package uses only indicator 1 and pushed apps named
`fleet_*`, and leaves everything else alone. Put anything else you show on it in your own
package, under your own app names, and the two never collide or depend on each other.

## The aside

`packages/claude_fleet_quip.yaml` puts one dry line on the dashboard, written fresh each
time by an LLM reacting to the real numbers. Who writes it, first match wins:

1. **`script.claude_fleet_aside_author`**, if you define one: it receives `prompt` and
   returns `{"text": ...}`. `examples/aside_author_ai_task.yaml` is one built on Home
   Assistant's AI Task. This is the path to prefer, because the request never enters a
   conversation.
2. Otherwise **`agent`** in that file, any conversation entity, via `conversation.process`.
3. Neither: the automation does nothing and the card stays hidden.

Delete the file and the feature is gone: the card hides itself when the entity is absent.

It runs every half hour while sessions are running, and immediately on notable states:
over pace, either pool above 80%, an unusual number of sessions, one gone quiet, a personal
record falling. The activity gate is what stops it narrating an empty fleet overnight.

Four decisions in it are worth keeping if you adapt it:

- **The LLM authors the line.** No template, no fallback string. A canned joke reads as
  canned the second time you see it, and the second time is a week later.
- **Keep it out of your assistant's conversation.** Through a conversation agent that keeps
  memory, every request is a real turn recorded as you talking to it. In one install the
  pings filled 13 of the last 20 turns it could see and pushed real conversations out of its
  memory within two days, and its conversational habits leaked onto the wall. That is what
  the author script is for.
- **If you do use an agent, the prompt says what is asking** in its first line, so its
  history does not fill with what look like you sending statistics at odd hours.
- **Frequency is a cost decision, not a taste one.** A large system prompt is not a free
  background call. If you want this more often than half-hourly, use something cheap.
