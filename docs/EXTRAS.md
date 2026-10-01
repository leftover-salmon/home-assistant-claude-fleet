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
