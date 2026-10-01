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
