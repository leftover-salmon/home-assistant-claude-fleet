# Releasing

For whoever maintains this. Users want the [changelog](../CHANGELOG.md).

There are two version numbers, and each moves only when its half changes:

| Changed | Bump | Where |
|---|---|---|
| `hooks/`, or anything the plugin ships | the hook | `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` |
| `homeassistant/packages/` or `homeassistant/dashboards/` | the Home Assistant files | the `Claude Fleet package version` sensor in `claude_fleet.yaml`, and `dash_v` on the dashboard's Setup tab |
| `blueprints/`, docs, tests | nothing | blueprints are fetched from `main` on import |

Bumping the hook is what makes Claude Code offer plugin users an update, so never bump it
for a change that leaves the hook alone: they would "update" to the same hook.

Then:

1. Add the CHANGELOG entry at the top: `## Hook X.Y.Z (date)` or
   `## Home Assistant files X.Y.Z (date)`, starting with what to do to update. The e2e test
   fails until the two files of a pair and their newest entry agree.
2. Run `tests/e2e/run.sh`, then commit and push.
3. Tag it and make the GitHub release from the entry, so watchers hear about it:

   ```bash
   git tag ha-files-v0.2.0 && git push origin ha-files-v0.2.0      # or hook-v0.1.13
   gh release create ha-files-v0.2.0 --title "Home Assistant files 0.2.0" --notes "..."
   ```
