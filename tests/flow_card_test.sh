#!/usr/bin/env bash
# The flow card's streams: a page that wakes after a long sleep must not count
# everything written while it slept as the last five minutes (40,752 a minute
# on 2026-10-28, when the real rate was 283). Run: tests/flow_card_test.sh
# (needs node). Exits non-zero on a failure.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
exec node - "$REPO/homeassistant/www/claude-fleet-flow-card.js" <<'JS'
const fs = require("fs"), vm = require("vm");
class FakeDate extends Date { static now() { return clock * 1000; } }
const ctx = { console, Promise, Map, Math, Number, String, Date: FakeDate,
  HTMLElement: class {}, window: { customElements: { whenDefined: () => new Promise(() => {}) } } };
let clock = 0;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8") + "\nthis.C = ClaudeFleetFlowCard;", ctx);
const C = ctx.C;
let fail = 0;
const check = (label, ok, got) => { console.log(`${ok ? "ok  " : "FAIL"} ${label}${ok ? "" : ` (got ${got})`}`); if (!ok) fail = 1; };

const ID = "sensor.claude_machine_m4_output_tokens";
const hass = (count, history) => ({
  states: { [ID]: { state: String(count), attributes: { claude_machine_tokens: true, machine: "M4" } } },
  callWS: () => Promise.resolve({ [ID]: history }),
});
const card = () => { const c = Object.create(C.prototype); c._machines = new Map(); return c; };
const rate = (c) => C.machineRate(c._machines.get("M4").readings, clock);
const settle = () => new Promise((r) => setImmediate(r));

(async () => {
  // awake all along: readings every few seconds, about 500 a minute
  let c = card();
  clock = 1000;
  c._readMachines(hass(412000, [{ lu: 1000 - 330, s: "409250" }]));
  await settle();
  for (let t = 1005; t <= 1330; t += 5) { clock = t; c._readMachines(hass(409250 + (t - 1000) * 8, [])); }
  check("an awake page reads about 500 a minute", Math.abs(rate(c) - 480) < 40, rate(c));

  // asleep for four hours, then woken: the recorder has the real five minutes
  c = card();
  clock = 1000;
  c._readMachines(hass(412000, [{ lu: 1000 - 330, s: "412000" }]));
  await settle();
  clock = 1000 + 4 * 3600;
  c._readMachines(hass(636422, [{ lu: clock - 330, s: "633880" }, { lu: clock - 10, s: "636422" }]));
  await settle();
  check("a woken page reads the last five minutes, not the four hours", rate(c) < 1000, rate(c));

  // the same wake without the reset is the flood the card used to show
  const stale = [[1000 - 330, 412000], [1000 + 4 * 3600, 636422]];
  check("without it the sleep is counted as five minutes", C.machineRate(stale, 1000 + 4 * 3600) > 40000,
        C.machineRate(stale, 1000 + 4 * 3600));

  check("a page that has heard nothing yet did not sleep", C.slept(undefined, 5000) === false, "slept");
  check("ten seconds of quiet is not sleep", C.slept(1000, 1010) === false, "slept");
  check("five minutes of quiet is", C.slept(1000, 1300) === true, "awake");
  console.log(fail ? "FAILED" : "all passed");
  process.exit(fail);
})();
JS
