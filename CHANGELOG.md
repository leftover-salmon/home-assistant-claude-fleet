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

To hear about new versions, **Watch → Custom → Releases** on the
[GitHub repo](https://github.com/leftover-salmon/home-assistant-claude-fleet): each version
below is also a release there.

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
