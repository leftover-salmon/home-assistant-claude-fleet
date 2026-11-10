# Changelog

Claude Fleet has two version numbers, because its two halves update separately:

- **Hook**: what runs on each computer. The plugin updates when this number goes up; with
  the script, `git pull && ./install.sh`. The Setup tab's machine table shows each
  computer's version and flags any behind the others.
- **Home Assistant files**: the package and the dashboard, which always carry the same
  number. Update by copying them again (each entry says which) and restarting. The Setup tab
  shows the version you have, and warns when the package and the dashboard don't match.

The blueprints have no version: Home Assistant fetches them from GitHub when you import one,
and **Re-import blueprint** (in the blueprint's menu) gets the latest.

To hear about new versions, Home Assistant posts a notification under the sidebar bell
when one is out (unless you turn off the **Update check** on the Setup tab), or **Watch → Custom → Releases** on the
[GitHub repo](https://github.com/leftover-salmon/home-assistant-claude-fleet): each version
below is also a release there.

## Unreleased

Changes on `main` that are not in a release yet.

**To update:** copy the package, the dashboard and `www/claude-fleet-flow-card.js` again, and
change the `extra_module_url` line in `configuration.yaml` to
`/local/claude-fleet-flow-card.js?v=4`, or browsers keep the old file. Update the hook too
(below): the cards work without it, but projects worked on in removed worktrees are misnamed.

- **A timeline of the day** on the Sessions tab: one lane per session, blue while it worked,
  amber while it waited on you, green while idle, with how long sessions waited on you in all
  and the longest wait. Sessions the cleanup already removed stay on it.
- **Tokens by project** on the Tokens tab: where today's tokens went, by the repository each
  session worked in (or its folder, when it is not in one), and the same day by day. Switches
  between all tokens and the tokens written. Everything in one project reads as a sentence,
  not a chart of one colour. Each day is saved just before midnight, so the trend starts on
  the first night after you update.
- **Hook: a session keeps its project when its worktree is removed.** The project was worked
  out again from the folder on every event, and once a worktree was removed after its merge,
  that folder was all there was: a session that had worked in `~/shop-42` reported the
  project `shop-42` instead of `shop`. A folder that no longer exists now keeps the name it had.

- **Today's totals no longer drop when a session is archived twice.** A session that idled
  out, was resumed and idled out again fell back to its first archived counts, so tokens,
  prompts, PRs and wait minutes for the day dropped, then jumped back when it resumed, and
  the token rate (flow card, desk clock) showed the jump as a burst of writing. The later
  archive now replaces the earlier one.
- **Tokens by day no longer counts a day twice across a restart.** At every shutdown the
  per-model token sensors read 0 for an instant, and the chart takes a drop for the start of
  a new count, so the day's tokens were added again when they came back (one day drew 1.06
  billion tokens as 2.2). They now read unavailable instead, which the chart skips. Bars
  already drawn twice correct themselves once Home Assistant's 10 days of raw history have
  passed.
- **A gauge for a model with its own weekly limit** (on Max, Fable): how much of that limit
  is used, coloured by pace like the plan gauges, with its share of the whole week, and a
  chart of it on the Tokens tab. It counts the model everywhere, the Claude app included.
  Needs Clawdmeter 0.1 or later; without such a limit nothing appears.
- **Where this week went**, on the Sessions tab: the week's usage by surface (Claude Code,
  chat, Cowork, other), each as its share of the week and in points of the weekly limit. Use
  in the Claude apps and on claude.ai shows here and nowhere else, since they report no
  tokens. Needs Clawdmeter 0.2.2 or later; without it the card is hidden.

## Hook 0.1.15 (2026-11-03)

**To update:** with the plugin, `/plugin` → **Marketplaces** → claude-fleet → **Update**,
then update the plugin; with the script, `git pull && ./install.sh`. Sessions already
running keep the old hook until they are restarted. Update the dashboard at the same time
([Home Assistant files 0.6.0](#home-assistant-files-060-2026-11-03)): with only the new hook,
headless calls disappear from the Sessions table instead of folding into one row.

- **Headless calls fold into one row.** One-shot `claude -p` and Agent SDK calls used to appear
  as a session each, so a script running a batch filled the Sessions table with rows that were
  over in under a minute. The hook now counts them on one sensor per computer
  (`sensor.claude_<machine>_headless`: calls today, how many are running, the last one's folder
  and prompt) instead of announcing each as a session. They are not counted as sessions
  anywhere: not in Running, the status light, the records or the day's tally.

## Home Assistant files 0.6.0 (2026-11-03)

**To update:** copy `packages/claude_fleet.yaml` and `dashboards/claude_fleet.yaml` again,
and restart. Update the hook on each computer as well ([Hook 0.1.15](#hook-0115-2026-11-03)).

- **One row for headless calls.** Both Sessions tables show one row per computer for its
  `claude -p` and Agent SDK calls, while a call is running or for two hours after the last,
  read from the hook's new `sensor.claude_<machine>_headless`.
- **The setup banner clears when sessions come back.** After the cleanup removed the last
  session, `binary_sensor.claude_fleet_setup_needed` could stay on after new sessions
  reported, and the Sessions tab then warned that a Mac was running an out-of-date hook when
  none was. With no sessions, the sensor re-checked only when another sensor changed; it now
  re-checks every minute as well.

## Home Assistant files 0.5.1 (2026-10-28)

**To update:** copy `www/claude-fleet-flow-card.js` to `/config/www/` again, change the
line under `frontend:` in `configuration.yaml` to

```yaml
    - /local/claude-fleet-flow-card.js?v=3
```

and restart. Copy `packages/claude_fleet.yaml` and `dashboards/claude_fleet.yaml` again
too: only their version number changed, and the Setup tab warns while they disagree.

- **A dashboard left open no longer floods.** A page that had been asleep (a background
  tab, a closed laptop) counted everything a computer wrote while it slept as the last five
  minutes, so on waking a computer writing a few hundred tokens a minute showed tens of
  thousands, and the stream and the big number went into flood with it. A page that has
  heard nothing from Home Assistant for a minute and a half now forgets its readings and
  asks the recorder for the last five minutes again, as a freshly opened page does.

## Hook 0.1.14 (2026-10-27)

**To update:** with the plugin, `/plugin` → **Marketplaces** → claude-fleet → **Update**,
then update the plugin; with the script, `git pull && ./install.sh`. Sessions already
running keep the old hook until they are restarted. Pair it with [Home Assistant files
0.5.0](#home-assistant-files-050-2026-10-27), which is what reads `/away` and `/back`.

- **`/away` and `/back`.** Type either into any session and the hook tells Home Assistant,
  published retained on `claude/presence`. The command never reaches the model, so it
  costs no turn. The script install adds the two command files to `~/.claude/commands`; the
  plugin carries them.
- **Back without saying so:** after `/away`, any other prompt typed at that computer counts
  as back. A prompt sent from your phone through Remote Control reaches the same session,
  so on a Mac it counts only if the keyboard or mouse was used in the last two minutes
  (`CLAUDE_HA_BACK_IDLE` in `ha-status.env`, in seconds).
- **Your own `/away` or `/back` is left alone.** If a project or your own setup already has
  a command or skill by either name, the plain name runs yours and Home Assistant isn't
  told; `/claude-fleet:away` and `/claude-fleet:back` always reach Claude Fleet.
- **Each session's Remote Control link,** `remote_url`, while Remote Control is on.

## Home Assistant files 0.5.0 (2026-10-27)

**To update:** copy `packages/claude_fleet.yaml` and `dashboards/claude_fleet.yaml` again,
and restart. With the desk display, copy `packages/claude_fleet_awtrix.yaml` again too.
Re-import the **"a session is waiting on you"** blueprint (its menu → **Re-import
blueprint**). Update the hook on each computer as well ([Hook
0.1.14](#hook-0114-2026-10-27)), or `/away` does nothing there.

- **`binary_sensor.claude_away`,** from `/away` and `/back`, with who said it and where as
  attributes. Nothing in Claude Fleet acts on it: it is there for your own automations.
- **The stream leads the dashboard.** The burn-rate stream moves to the top, under the
  headline. On a wide screen it shares one row with the two plan gauges and **At this
  rate**; on a phone it stays full width. The **Last hour** chart now redraws with each new
  reading and every minute, instead of only when the page loads.
- **The stream on the desk display:** output tokens a minute over the clock's waves, faster
  as the fleet writes faster, joining the rotation only while something is being written.
- **Blueprint "a session is waiting on you":** tapping the notification opens the waiting
  session in Remote Control when it has a link, instead of the dashboard.

## Home Assistant files 0.4.0 (2026-10-13)

**To update:** copy `packages/claude_fleet.yaml` and `dashboards/claude_fleet.yaml` again,
and copy the **new file** `www/claude-fleet-flow-card.js` to `/config/www/`. Then add two
lines under `frontend:` in `configuration.yaml` (the README shows where), and restart:

```yaml
frontend:
  extra_module_url:
    - /local/claude-fleet-flow-card.js?v=2
```

Without those two lines the burn-rate card shows an error. Update the hook on each computer
too ([Hook 0.1.13](#hook-0113-2026-10-13)): a computer on 0.1.12 still counts toward the
fleet, but as one spike a turn, and it gets no stream or bar of its own.

- **Burn rate, on the Sessions tab:** a stream that runs with the fleet's output tokens a
  minute over the last five minutes. It is still at zero, a trickle when one session is
  thinking, and fast, full and bright when many are, with the last hour charted beside it.
  Each computer has a stream of its own running into it, as wide and as fast as that
  computer is writing. It counts output tokens rather than all tokens because about 98% of
  the total is the same context re-read from cache every turn.
- **Output tokens by machine, on the Tokens tab:** ten-minute bars stacked by computer. A
  new computer appears in the chart by itself once it runs hook 0.1.13.
- **PRs opened by day, on the Tokens tab:** a bar a day, from `sensor.claude_prs_today`.
  Only PRs the sessions themselves opened; nothing is asked of GitHub.
- **At this rate, on the Sessions tab:** a session "limit at" warning now says it comes from
  the last 30 minutes' rate. "45% used against 47% of the window — ⚠️ limit at 12:29 PM"
  read as a contradiction: the percentages are the window's average, the warning only the
  last half hour. It now reads "— but the last 30 min ran hot: at that pace, ⚠️ limit at
  12:29 PM" (or "at the last 30 min's pace" when the recent rate isn't above average), and
  "What do these mean?" explains the two bases.
- **Documentation:** a README section on why a laptop stops reporting when it leaves the
  house (Home Assistant Cloud doesn't carry MQTT) and how to keep it reporting with
  Tailscale, with a matching troubleshooting entry; and links to the two write-ups on the
  author's blog.

## Hook 0.1.13 (2026-10-13)

**To update:** with the plugin, `/plugin` → **Marketplaces** → claude-fleet → **Update**,
then update the plugin; with the script, `git pull && ./install.sh`. Sessions already
running keep the old hook until they are restarted. Pair it with [Home Assistant files
0.4.0](#home-assistant-files-040-2026-10-13), which is what shows the new numbers.

- **Tokens arrive while a turn runs.** The hook now counts a session's tokens every two
  minutes during a long turn, instead of all at once when the turn ends, so the burn rate
  moves while Claude is working. `TOKENS_RECHECK=0` in `ha-status.env` turns it off.
- **Each computer reports its own output today,** as
  `sensor.claude_machine_<name>_output_tokens`. This is what gives a computer its own
  stream on the burn-rate card and its own colour in the by-machine chart.

## Home Assistant files 0.3.2 (2026-10-01)

**To update:** copy `packages/claude_fleet.yaml` and `dashboards/claude_fleet.yaml` again,
then restart. No hook update.

- **A new version now gets a notification** under the sidebar bell, once, with its release
  notes and what to copy. It isn't repeated, skips a version you **Skip**, and clears
  itself once you've updated.
- **A correction to 0.3.0 and 0.3.1,** which said new versions would show at the top of
  Settings, with a badge. They don't: Home Assistant only counts updates it can install
  there, and Claude Fleet's can't be installed from Home Assistant (that means copying
  files). They are listed under **Settings → System → Updates**, as "not installable",
  which is easy to miss; hence the notification.

## Home Assistant files 0.3.1 (2026-10-01)

**To update:** copy `packages/claude_fleet.yaml` and `dashboards/claude_fleet.yaml` again,
then restart. No hook update.

- **The update check is on by default.** New versions now show in Settings → Updates
  without anyone having to find the switch. This update turns it on once, including if you
  had it off; turn it off on the Setup tab and it stays off from then on.
- The automation behind it is renamed **"Claude · check GitHub for new versions"**, so it
  isn't mistaken for the switch. The switch on the Setup tab is what turns checking on and
  off; leave the automation on.

## Home Assistant files 0.3.0 (2026-10-01)

**To update:** copy `packages/claude_fleet.yaml` and `dashboards/claude_fleet.yaml` again,
then restart. No hook update.

- **Update check, opt-in.** Switch on **Update check** on the Setup tab, and new versions
  of these files appear in **Settings → Updates** like any other update, with their
  release notes and a link. Once a day Home Assistant asks GitHub for the newest release:
  the one part of Claude Fleet that reaches the internet, which is why it's off until you
  turn it on. There is no Install button: updating still means copying the files, as
  each release says. See [Update check](docs/EXTRAS.md#update-check).

## Home Assistant files 0.2.0 (2026-10-01)

**To update:** copy `packages/claude_fleet.yaml`, `packages/claude_fleet_quip.yaml` (if you
use the aside) and `dashboards/claude_fleet.yaml` again, then restart. No hook update.

- **Phone alerts**, as two blueprints you import with a click: a session has been
  waiting on you for a few minutes (named, and withdrawn once you answer), and a plan
  limit is on course to run out before it resets. See
  [Alerts](docs/EXTRAS.md#alerts).
- **"Most output in a day"**, a new personal record beside "Most tokens in a day", which
  is mostly cache reads. Output is the part Claude actually wrote.
- **Versions on the Setup tab**: the Home Assistant files' version, and a warning when the
  package and the dashboard don't match.

## Hook 0.1.12, first public version (2026-09-30)

The hook at 0.1.12, and the Home Assistant files before they had a version number (the
Setup tab of those shows none).
