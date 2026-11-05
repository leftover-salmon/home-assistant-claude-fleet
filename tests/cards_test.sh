#!/usr/bin/env bash
# The timeline and projects cards' logic, without a page: which sessions get a
# lane and where each lane ends, how long sessions waited on you, and how the
# projects card folds, colours and reduces to a sentence. Run: tests/cards_test.sh
# (needs node). Exits non-zero on a failure.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
exec node - "$REPO/homeassistant/www/claude-fleet-flow-card.js" <<'JS'
const fs = require("fs"), vm = require("vm");
const ctx = { console, Promise, Map, Set, Math, Number, String, Date, Intl, Object,
  HTMLElement: class {}, window: { customElements: { whenDefined: () => new Promise(() => {}) } } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8") +
  "\nthis.T = ClaudeFleetTimelineCard; this.P = ClaudeFleetProjectsCard;", ctx);
const { T, P } = ctx;
let fail = 0;
const check = (label, ok, got) => {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}${ok ? "" : ` (got ${JSON.stringify(got)})`}`);
  if (!ok) fail = 1;
};
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// ---------------------------------------------------------------- timeline
// midnight in the house's time zone, not the browser's or UTC's
const now = Date.parse("2026-11-05T15:30:00Z") / 1000;
check("midnight is local midnight in the given zone",
  T.midnight(now, "America/Los_Angeles") === Date.parse("2026-11-05T08:00:00Z") / 1000,
  new Date(T.midnight(now, "America/Los_Angeles") * 1000).toISOString());

const S = 1000, E = 5000;
const rows = [{ s: "idle", lu: 500 }, { s: "working", lu: 1200 }, { s: "working", lu: 1500 },
              { s: "needs_input", lu: 2000 }, { s: "unavailable", lu: 2600 }, { s: "idle", lu: 3000 }];
const segs = T.segments(rows, S, E);
check("rows become segments: clamped to the start, a run of one kind merged, a gap where it was unavailable",
  same(segs, [{ kind: "idle", from: 1000, to: 1200 }, { kind: "working", from: 1200, to: 2000 },
              { kind: "needs_input", from: 2000, to: 2600 }, { kind: "idle", from: 3000, to: 5000 }]), segs);
const cut = T.segments(rows, S, E, 2300);
check("a session archived at 2300 stops there, not at now",
  cut[cut.length - 1].to === 2300 && cut.length === 3, cut);

const states = {
  "sensor.claude_archived_today": { state: "2", attributes: { day: "2026-11-05", sessions: [
    { session_id: "a", entity_id: "sensor.cc_a", label: "Archived A", machine: "M4", archived_at: "1970-01-01T01:00:00Z" },
    { session_id: "b", entity_id: "sensor.cc_b", label: "Resumed B (old)", machine: "M4", archived_at: "1970-01-01T00:30:00Z" },
    { session_id: "c", label: "Before the archive kept entities" }] } },
  "sensor.cc_b": { state: "working", attributes: { claude_session: true, day: "2026-11-05", label: "Resumed B", machine: "M4" } },
  "sensor.cc_y": { state: "idle", attributes: { claude_session: true, day: "2026-11-04", label: "Yesterday's", machine: "M2" } },
};
const w = T.wanted(states);
check("lanes: live sessions of today and archived ones, the live one winning, yesterday's left out",
  same([...w.keys()].sort(), ["sensor.cc_a", "sensor.cc_b"]) && w.get("sensor.cc_b").live === true
  && w.get("sensor.cc_b").label === "Resumed B" && w.get("sensor.cc_a").endAt === 3600, [...w.entries()]);

const hist = {
  "sensor.cc_a": [{ s: "working", lu: 1500 }, { s: "idle", lu: 2500 }],
  "sensor.cc_b": [{ s: "idle", lu: 1000 }, { s: "needs_input", lu: 1100 }, { s: "working", lu: 1400 }],
};
const wantedPlus = new Map([...w, ["sensor.cc_idle", { label: "Idle all day", machine: "M4", live: true }],
                                  ["sensor.cc_old", { label: "Old archive", machine: "M4", live: false }]]);
hist["sensor.cc_idle"] = [{ s: "idle", lu: 900 }];
hist["sensor.cc_old"] = [{ s: "working", lu: 1300 }, { s: "idle", lu: 1350 }];
const lanes = T.lanes(wantedPlus, hist, S, E);
check("a session idle all day gets no lane; the rest are ordered by when they first worked or waited",
  same(lanes.map((l) => l.label), ["Resumed B", "Old archive", "Archived A"]), lanes.map((l) => l.label));
const a = lanes.find((l) => l.label === "Archived A");
check("an archived lane ends where it was archived (3600), not at now (5000)",
  a.segs[a.segs.length - 1].to === 3600, a.segs);
const old = lanes.find((l) => l.label === "Old archive");
check("archived before the archive kept the time: the lane stops at its last state change",
  old.segs[old.segs.length - 1].to === 1350, old.segs);

const waits = T.waits([{ label: "x", segs: [{ kind: "needs_input", from: 0, to: 120 }, { kind: "working", from: 120, to: 900 }] },
                       { label: "y", segs: [{ kind: "needs_input", from: 300, to: 900 }] }]);
check("waits: summed across lanes, counted, the longest named",
  waits.total === 720 && waits.count === 2 && waits.longest.label === "y" && waits.longest.d === 600, waits);

// ---------------------------------------------------------------- projects
const today = [{ name: "marcus", total: 900, output: 10 }, { name: "claude-fleet", total: 100, output: 40 }];
const saved = { "2026-11-03": { "claude-fleet": [50, 5], "notes": [30, 3] },
                "2026-11-04": { "marcus": [500, 9], "(others)": [7, 1] },
                "2026-11-05": { "marcus": [1, 1] } };   // today's saved entry is replaced by the live one
let m = P.model(today, saved, "2026-11-05", 30, "total");
check("today is the live list, not the saved entry for today", m.today.total === 1000, m.today);
check("colours follow the project's rank over all the days shown, not on one day",
  same(m.named, ["marcus", "claude-fleet", "notes"]), m.named);
check("'(others)' from a saved day folds into Other", same(m.rows[1].parts.map((p) => p.name), ["marcus", "Other"]), m.rows[1]);
check("the trend counts saved days, not today", m.saved === 2, m.saved);
m = P.model(today, saved, "2026-11-05", 30, "output");
check("written ranks by output: claude-fleet first", m.named[0] === "claude-fleet", m.named);
m = P.model(today, saved, "2026-11-05", 2, "total");
check("days: only the last N", same(m.keys, ["2026-11-04", "2026-11-05"]), m.keys);
m = P.model([{ name: "notes", total: 5, output: 1 }], { "2026-11-04": { "notes": [9, 2] } }, "2026-11-05", 30, "total");
check("one project across every day shown is a sentence, not a chart", m.single === "notes", m);
m = P.model(today, {}, "2026-11-05", 30, "total");
check("two projects today is a chart", m.single === null && !m.empty, m);
m = P.model([], {}, "2026-11-05", 30, "total");
check("nothing at all is empty", m.empty === true, m);
const many = [];
for (let i = 0; i < 10; i++) many.push({ name: "p" + i, total: 100 - i, output: 1 });
m = P.model(many, {}, "2026-11-05", 30, "total");
check("more projects than colours: seven named, the rest one Other",
  m.named.length === 7 && m.today.parts.length === 8 && m.today.parts[7].name === "Other"
  && m.today.parts[7].value === 93 + 92 + 91, m.today.parts);

process.exit(fail);
JS
