# Releasing

For whoever maintains this. Users want the [changelog](../CHANGELOG.md).

## Committing is not releasing

A commit to `main` changes the code and tells nobody. A **release** (a tag, plus a page on
GitHub) is what reaches people: watchers get an email, Claude Code offers plugin users a
hook update, and every Home Assistant with the update check on posts a notification. So
commit as often as needed, and release rarely:

- **Changes collect on `main`** under `## Unreleased` at the top of the changelog, with no
  version bump. Bigger work happens on a local branch first and is squash-merged.
- **Release when there's something worth an alert:** a feature, or a fix to something users
  actually hit. Small things ride along with the next one. A broken install or a security
  problem goes out straight away.
- **Don't release the same day's fixes one by one.** 2026-10-01 shipped 0.3.0, 0.3.1 and
  0.3.2 within three hours, two of them fixes to the first. With notifications on, that is
  three alerts for one feature. Test it on the demo, then release once.

People who install by copying from `main` between releases get the unreleased changes under
the previous version's number. The changelog's Unreleased section says what they are.

## The two version numbers

Each moves only when its half changes:

| Changed | Bump | Where |
|---|---|---|
| `hooks/`, or anything the plugin ships | the hook | `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` |
| `homeassistant/packages/` or `homeassistant/dashboards/` | the Home Assistant files | the `Claude Fleet package version` sensor in `claude_fleet.yaml`, and `dash_v` on the dashboard's Setup tab |
| `blueprints/`, docs, tests | nothing | blueprints are fetched from `main` on import |

Bumping the hook is what makes Claude Code offer plugin users an update, so never bump it
for a change that leaves the hook alone: they would "update" to the same hook.

## Making a release

1. Turn `## Unreleased` into `## Hook X.Y.Z (date)` or `## Home Assistant files X.Y.Z (date)`,
   starting with what to do to update, and put an empty `## Unreleased` back above it. Bump
   the version in both files of its pair. The e2e test fails until the pair and its newest
   entry agree.
2. Run `tests/e2e/run.sh`, then commit, push, and wait for CI on the public repo.
3. Tag it and make the GitHub release from the entry:

   ```bash
   git tag ha-files-v0.4.0 && git push origin ha-files-v0.4.0      # or hook-v0.1.13
   gh release create ha-files-v0.4.0 --title "Home Assistant files 0.4.0" --notes-file notes.md
   ```
