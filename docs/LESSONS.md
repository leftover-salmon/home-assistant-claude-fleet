# Things that were learned the hard way

Building Claude Fleet meant getting Home Assistant templates, MQTT, Claude Code hooks and a
lot of counting right, usually after getting them wrong first. Each of these cost real time.
They are here so they cost it once, and most of them apply well beyond this project.

## Home Assistant templates and YAML

- **HA renders template entities with STRICT undefined.** `foo.bar | default(x)` **raises at the
  dot-access** (the filter never runs) and the entity goes `unavailable`, which reads like a
  broken integration rather than a template bug. Any attribute that might be absent must use
  `.get()`. This has bitten three times.
- **`selectattr`/`rejectattr` are dot-access in disguise.** `rejectattr('attributes.x',
  ...)` raises on any entity lacking `x`, and under strict undefined that takes the
  ENTIRE card with it: the sessions table rendered as an empty box, which is a much
  worse failure than the missing footer it was computing. Use an explicit loop with
  `.get()`.
- **A record table must survive a bad render.** The records live in the attributes of a
  trigger-based template sensor, because that kind restores its state AND attributes across
  a restart. The risk is strict undefined again. If one render raises, the entity goes
  unavailable, the next render finds no prior table, and every record is back to zero with
  nothing in the log to say why. So every read of the previous table checks that it is a
  mapping and uses `.get()`. A garbage table then starts over instead of breaking the entity.
- **Never name anything `on`, `off`, `yes` or `no` in YAML.** A variable called `on` is read
  as the boolean *true*, the key stops being a string, and Home Assistant rejects the whole
  automation at load. The only trace is a repair notice; the automation simply never runs.
  Template tests that pass variables in by hand cannot catch it, because they never parse
  the key.
- **A markdown table needs a blank line to end.** The per-machine CLI version line
  printed straight after the last row rendered as a sixth column of a final table row,
  not as text under the table. Jinja's `{%- ... %}` trimming eats the blank line you
  thought you left.
- **`first` of nothing raises.** `expand(entity_id) | first` is the obvious way to get one
  entity's state object, and it works until the entity goes away: an integration removed, an
  entity renamed. Then `expand` returns an empty list and `first` raises "No first item,
  sequence was empty" under strict undefined. The plan-source sensor filled the log with
  that instead of reporting `unavailable`. `(expand(x) | list + [none]) | first` gives
  `none` to test for.

## Numbers that mean something

- **A line chart over daily buckets draws data that does not exist.** The two
  `statistics-graph` cards use `period: day`, and with `chart_type: line` the second day
  of use rendered as a smooth all-day slide from 10 concurrent sessions to 3. Both points
  were midnight stamps; everything between them was interpolation. Daily aggregates are
  discrete: use `chart_type: bar`.
- **"How full" is the wrong question for a pool that resets.** The plan gauges turned yellow at
  75% used, which is alarming ten minutes before a reset and reassuring four hours before it.
  They now take their colour from where the pool is headed at reset (`sensor.claude_session_pace`,
  `sensor.claude_weekly_pace`), computed with the same arithmetic the "At this rate" card
  prints, so the colour cannot disagree with the sentence beside it. A gauge's severity bands
  are fixed per card, so each gauge exists three times with `visibility` choosing one.
- **On pace is not the same as safe.** 90% used at 90% of the week is exactly on pace, with
  10 points left and one heavy day able to burn more than that before the reset. Pace alone
  kept the weekly gauge green there. It now also turns yellow when the headroom is less than a
  heavy day would use before the reset: the busiest day of the last seven, never less than
  1.5x an average day, capped at one day's worth so early in the week a normal amount used
  doesn't turn it yellow.
- **A burn rate must forget the pool that reset.** A 30-minute derivative of session usage
  carries a reset's negative jump for half an hour, and clamping it to zero only hides it: the
  first half hour of a fresh pool reads as no burn. The derived rate keeps its own readings and
  discards everything before a drop in usage, because those readings belong to the old pool.
- **A projection must be bounded by whatever resets the thing it projects.** The session
  pool refills every 5 hours, so an ETA past the next reset is a burn rate extrapolated
  against a pool that will have refilled several times before it arrives. It renders as a
  confident clock time, "Sun 7:29 AM", at which nothing happens. Project usage forward to
  the reset instead, which is bounded by construction.
- **A plan integration that stops fails silently.** Its sensors keep their last value, so the
  gauges go on showing an old number as if it were current. `sensor.claude_plan_source`
  reports the source's health (ok / stale / unavailable / none), and a banner says so. Judge
  freshness by `last_reported`, not `last_updated`: the second moves only when the number
  changes, so it would call a quiet Sunday stale.
- **A warning on one failed poll teaches you to ignore it.** The plan-source health went
  `unavailable` the moment Clawdmeter missed a single poll, and the banner said "it most
  often needs you to sign in again". Clawdmeter misses one now and then and recovers by
  itself: on the day it was noticed, ten minutes without a reading had someone checking a
  sign-in that was fine. The banner now waits until the source has stayed unavailable for
  15 minutes (from its `last_changed`), and the plan values hold their last reading
  meanwhile, so the gauges don't vanish either. Real outages still show within a quarter
  of an hour. Decide what a warning is for before choosing when it fires: this one is for
  "go and fix something", and one missed poll isn't that.
- **A record that grows is not a record that falls.** A solo run in progress beats its own
  record every minute, and a busy day beats its own prompt count with every prompt. Announcing
  on "value went up" would have had the agent congratulating one session a hundred times an
  hour. Each record stores the id of the run that set it, a day or one session's stretch in one
  state, and only a *different* run overtaking it counts. A record held at zero cannot fall,
  or the first day would announce all five at once.
- **The context percentage has an ASSUMED denominator.** The transcript names the model
  but never its context limit, and `claude-opus-5` is the same string for the 200k and
  the 1M variants. The hook assumes 200k and latches a session to 1M once it is observed
  above its assumed limit, so the figure is right for every 200k session and for a
  long-context one from the point it passes 200k. `CLAUDE_HA_CONTEXT_LIMIT` fixes it
  sooner. There is no marker to read: `context-1m` looked like one until the grep for it
  turned out to be matching the grep itself, recorded in the transcript as a tool call.
- **A session pins its CLI version at start**, so two sessions on the SAME machine
  routinely run different builds: one begun before an upgrade, one after. Grouping the
  version per machine was wrong; it belongs per session. Compare versions NUMERICALLY:
  `2.1.9` sorts above `2.1.278` as a string.
- **"Old" needs something to compare with.** A session's Claude Code build was flagged only
  against the newest build another *session* had started on, so an update installed while every
  session was open flagged nothing. Each computer now reports its installed build too.
- **A setting in the wrong place is silently ignored.** Every "by day" chart set
  `interval: daily` on the graph, and every one drew hourly bars for weeks, with its own
  selector reading "Hourly" in plain sight. The card reads a graph's interval from
  `options`. Nothing warned: an unknown key in a card's config is simply not read. Found by
  a demo with a month of invented history, where hourly bars across 30 days were obvious;
  on a real dashboard filling up a day at a time they had looked like data.
- **Two rules for one state will disagree.** The status sensors called a session stale on
  seconds of silence; the sessions table rounded silence down to whole minutes first. For up
  to a minute the headline counted a stale session while its row was still blue. The same
  test is now in seconds in both places.

## Counting tokens

- **A token counter must dedupe by message id.** The transcript writes each API response
  once per content block (thinking, text, tool call) with identical usage. Summing records
  counts a three-block turn three times.
- **RTK's "tokens saved" counts output the model was never going to see.** RTK measures
  each saving against the command's full raw output, but Claude Code hands the model at most
  30,000 characters of it. Shrinking a 2 MB log is logged as ~500k tokens saved; the model
  would have been shown ~7.5k. Over one measured week RTK claimed 2.69M and the capped figure
  was 0.53M, a fifth. On one afternoon a single grep over a 26 MB transcript claimed 6.4M on
  its own. The dashboard shows the capped figure and prints RTK's own count beside it. Set
  `BASH_MAX_OUTPUT_LENGTH` in `ha-status.env` if you raised Claude Code's limit.
- **RTK's ledger is SQLite in WAL mode, so it can't be opened read-only.** A read-only
  connection can't create the shared-memory file a WAL reader needs, and fails with
  "unable to open database file", which looks like a wrong path.

## The hook and MQTT

- **A failed publish must never be recorded as success.** Discovery used to set
  `discovered_label` whether or not the publish landed, so one transient failure hid a session
  *permanently*: it kept publishing state into topics HA had no entity for, while the hook
  exited 0 and looked healthy at both ends.
- **A failed publish has to leave a trace.** The hook runs detached with its output discarded,
  and Claude Code only sees that it exited 0. A plugin install whose saved password was wrong
  wrote state files for every session and published nothing, with nothing anywhere saying
  why. Failed publishes now go to `~/.claude/ha-status/errors.log` (reason, host, which
  settings came from the plugin, never the password), and `scripts/diagnose.sh` shows them.
- **A retained discovery entry is not guaranteed to stay.** The hook re-announced a session only
  after two hours of its own silence, so an entry removed any other way (cleanup racing the
  session, someone clearing test topics) stayed gone. One Mac's session published for hours
  with no sensor in HA to show it. Discovery is now re-sent every 30 minutes as well; it's
  retained and identical, so HA ignores it unless it had been lost.
- **Aggressive cleanup needs a way back.** Because HA now reaps non-working sessions after two
  hours, a session merely left idle over lunch could return, publish forever and stay invisible.
  Hence `REANNOUNCE_AFTER`: if we have been quiet long enough to have been reaped, forget that
  we announced.
- **A password on the command line is on show to the whole computer.** The hook passed it to
  `mosquitto_pub` as `-P`, and every process's arguments can be read by any user with `ps`.
  A hook that publishes several times a minute keeps it there all day. It now goes in
  `mosquitto_pub`'s own options file (`$XDG_CONFIG_HOME/mosquitto_pub`), written to a private
  directory that lasts one call. That route works in every version; the newer `-o` flag does
  not exist in the 2.0 that Debian and Ubuntu ship. Sampling `ps` to prove the fix proves
  nothing, because each publish lasts milliseconds and a sampler misses the old version's
  leak too. A stand-in `mosquitto_pub` that records its own arguments caught it: 12 of 12
  publishes before, 0 after.
- **Whatever gets pasted into a prompt was being published.** The last-prompt line went
  to MQTT verbatim, which means HA's recorder database and any wall display showing the
  dashboard: an appointment, an email, a log with a key in it. Pasted blocks are now
  redacted to `[pasted]`. Note the closing tag carries the same id as the opening one
  (`</pasted_content id="0b1c">`), so a pattern ending `</pasted_content>` silently
  matches nothing and the content sails through; and an *unclosed* block has to redact to
  end of string, or a truncated prompt leaks everything after it.
- **The hook payload's `cwd` is where the session STARTED, and it never moves.** Under a
  worktree workflow (one checkout per ticket, each on its own branch) `cwd` is permanently
  the shared checkout on `main`, so the branch guard short-circuits and the PR lookup never
  runs once. Every session reported `branch=main` and `PRs 0` for as long as the feature had
  existed, and nothing looked broken: a zero is exactly what an honest "no PRs yet" looks
  like. The working directory is now tracked from the last file the session actually touched.
- **The transcript already knows things we were inferring.** Claude Code writes a
  `pr-link` record for every PR a session opens, and every assistant turn carries the
  model and the CLI version. That replaced a branch-detection + `gh pr view` path that
  needed a git repo, a feature branch and an authenticated `gh` to be right. Two traps:
  `pr-link` is a LATCH, re-emitted every turn for the life of the session, so "in the
  transcript" does not mean "opened today"; and the file reaches tens of MB, so grep for
  the record type before handing anything to `jq`.
- **"Not seen before" is not "new today".** The first fix for that latch counted a PR as
  today's if the session's state had never recorded it. That holds only after the state is
  filled in. The first read of an older session had recorded nothing, so it credited three
  weeks of PRs to today, ten of them from one session. Date each PR by its *earliest*
  `pr-link` record, in local time, and let that date decide on every read. A wrong entry
  then heals on the next Stop, and nothing depends on when the hook was installed.
- **A PR is opened in the last seconds of a session**, so a lookup cached for five minutes
  can come due only after nobody is left to ask. The interval is shorter while no PR is
  known, and zero on `SessionEnd`.
- **macOS runs bash 3.2.** No `${var,,}`, no `$EPOCHREALTIME`, no associative arrays, and no
  `case` inside `$( )` (the pattern's `)` ends the substitution). `bash -n` catches none of
  these, because they fail at run time, so run anything new under `/bin/bash`.
- **Remove an MQTT entity before emptying its topics.** Cleanup cleared a session's
  attributes and state topics and then its discovery entry, so for a moment the still-live
  entity received an empty attributes message, and HA logged "Erroneous JSON" once for every
  session it removed. Clearing the discovery topic first removes the entity, and nothing is
  listening when the rest go.

## Claude Code

- **An exit hook gets very little time.** Claude Code cancels a `SessionEnd` hook that runs
  long ("hook cancelled"), and the session never shows as ended. The full routine took 1.1–1.7s
  (a transcript scan, a `gh` lookup, RTK's ledger), and on a laptop with a PR to look up it ran
  out. `SessionEnd` now only republishes what was last sent, marked ended: ~0.15s. And a `Stop`
  still running in the background must check before publishing, or it lands "idle" on top of
  "ended". But only a run that *began* before the end: `claude --continue` resumes with the same
  session id and state file, and a guard that checked "ended" alone silenced the resumed session
  for good, showing it ended while it worked.
- **Run `claude -p` bare when it's a utility.** Started in the session's directory, the summary
  call loaded that project's `CLAUDE.md`, the user's memory and every tool: ~52k tokens and $0.07
  per one-line summary in a large repo, plus a whole conversation's context to *reply* to
  instead of describe. It once put "**You're right, he wouldn't see it…**" on the dashboard as
  what a session was doing. Now it runs from the state directory with its own system prompt,
  `--tools ""`, `--strict-mcp-config`, `--setting-sources ""` and `--no-session-persistence`:
  ~400 tokens, $0.0013, and only a `SUMMARY:` line is accepted.
- **`claude -p` fires its own hooks.** The summariser sets `CLAUDE_HA_SUMMARIZING=1`, which the
  nested run sees and exits on. Without it this fork-bombs. `--bare` looks like the fix (it does
  skip hooks) but it also drops OAuth and demands an API key.
- **The summariser runs after the publish and outside the lock.** The call takes ~13s; status
  publishing and the next hook event must never wait on it.
- **`</dev/null` on `claude -p`**, or it waits 3s for stdin and warns into the captured output.

## Installing and updating

- **Two routes to install the same hook means two copies firing.** The plugin and the script
  both register it, and with both present every event would publish twice. One has to yield,
  and it should be the one that can detect the other: the plugin checks `settings.json` for
  the script's hook and steps aside.
- **A plugin only updates when its version changes.** With `version` set in `plugin.json`,
  Claude Code treats the same version as the same plugin, however much the hook changed. Any
  change to the hook bumps `version` in both `.claude-plugin/plugin.json` and
  `marketplace.json`, or plugin users never get it.
- **`cmd > tmp && check tmp && mv tmp file` fails silently.** If the first step fails, nothing
  is moved, the file is simply unchanged, and whatever runs next reads that as "nothing to
  do". The installer's `settings.json` merge said *already wired, no change* over a file
  jq could not parse. Test the failure branch and say it out loud.
- **A diagnostic that tests the fallback reassures you when it shouldn't.** On one Mac the
  plugin's saved host was `homeassitant.local` (one letter missing). Real sessions used it and
  published nothing, while every by-hand test used the env file's correct host and passed.
  `diagnose.sh` now tests the plugin's saved settings separately, since they are what real
  sessions use.
- **Instructions have to match what the obvious command does.** The README cloned into a
  folder of its own choosing, `git clone` without that argument makes a different one, and
  two of three computers ended up with the other name, so update steps written for the first
  failed on both. The only way to find this kind of gap is someone installing it who has never
  seen it: a fresh install from the README alone found ten more.

## Testing it

- **A sensor that sums other sensors lags the last event by a second or more.** Templates
  over every `states.sensor` are re-rendered at most once a second, and not at all while HA
  has detected a loop. The first end-to-end check read the counts within a second of the
  scenario's last event and saw six sessions running where there were five: the ended one
  was still `idle` to the counter. An assertion on a derived sensor has to wait for it to
  settle, then report what is still wrong, not read once.
- **Restarting Home Assistant while an integration is still setting up is logged as an
  ERROR**: "Setup of config entry ... cancelled". `default_config`'s Radio Browser looks up
  its servers online, so a restart straight after onboarding sometimes lands in the middle of
  it. A test that forbids ERROR lines then fails at random for a reason that is not the
  product's. The demo now waits for config entries to settle before its restart.
