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
