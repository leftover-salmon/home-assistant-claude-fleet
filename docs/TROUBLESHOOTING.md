# Troubleshooting and uninstalling

## Troubleshooting

- **A computer isn't reporting:** run `./scripts/diagnose.sh` there. Failed publishes are
  logged to `~/.claude/ha-status/errors.log` (the reason, the host, and which settings came
  from the plugin, never the password), and `diagnose.sh` shows the end of it.
- **A laptop reports at home but not away (`Lookup error` in `errors.log`):** the broker is
  only reachable on your home network; see [Away from home](../README.md#away-from-home).
- **`Bad file descriptor` on a Mac:** Local Network permission; see
  [On each computer](../README.md#2-on-each-computer).
- **A session is missing:** only sessions started after installing report. If one vanished
  later, see [When a session disappears](HOW-IT-WORKS.md#when-a-session-disappears-from-the-dashboard).
- **A card shows an error:** usually the history explorer card isn't installed, or HA wasn't
  restarted after adding the package.
- **New or changed sensors missing after an update:** the package's `sensor:` and
  `utility_meter:` parts need a full restart; a YAML reload covers only its automations and
  templates. A changed dashboard file needs only a page refresh.

## Uninstalling

- **On each computer:** with the plugin, `/plugin` → **Installed** → claude-fleet →
  **Uninstall**. With the script, `./install.sh --uninstall` removes the hook from
  `settings.json` (with a backup) and leaves every other hook. Then delete
  `~/.claude/hooks/claude-ha-status.sh`, `~/.claude/ha-status.env` and `~/.claude/ha-status/`
  if you want them gone.
- **In Home Assistant:** turn off the update check first (the **Update check** switch on the
  Setup tab): that removes `update.claude_fleet` and its retained message on the broker, which
  nothing else would. Then delete the `claude_fleet*.yaml` packages and the dashboard, remove the
  `claude-fleet` dashboard entry from `configuration.yaml` (and the `lovelace:` and
  `dashboards:` keys above it, if nothing else is under them), and restart. The session
  sensors come from MQTT discovery: delete the **Claude Code · …** devices under Settings →
  Devices & services → MQTT, which clears their discovery messages from the broker.
- **On the broker:** the last session states, and any `diagnose.sh` report, stay retained under
  `claude/`. From any computer with the Mosquitto clients, clear them with
  `mosquitto_sub -h <broker> -u <user> -P <password> -t 'claude/#' --remove-retained -W 3`.
  It stops after 3 seconds, which is how it ends; nothing else on the broker is touched.
