"""End-to-end assertions for Claude Fleet, against the demo Home Assistant.

    python check.py --self-test     every check against synthetic good and bad
                                    input; needs no Home Assistant
    python check.py                 the checks against the demo that up.sh and
                                    scenario.sh loaded (run with the demo's venv:
                                    $CF_DEMO_DIR/venv/bin/python)

Exits 0 when everything passed, 1 with a list of failures otherwise.

Every check is a pure function from data to a list of failure strings. The live
run gathers the data from Home Assistant; the self-test feeds the same functions
a good case, which must pass, and bad cases, each of which must fail. A check
that cannot fail proves nothing, so the self-test runs first, in CI too.

The live run needs the settings lib.sh exports: CF_DEMO_DIR, SECRETS, BASE_URL
and C_HA (the Home Assistant container, whose log is read with `docker logs`).
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEMO = os.path.join(HERE, "demo")
DASHBOARD = os.path.join(REPO, "homeassistant", "dashboards", "claude_fleet.yaml")


# ============================================================ the package loaded
# Entities the package defines, and those the dashboard leans on most. Missing =
# the package did not load, or a rename broke the dashboard's references.
KEY_ENTITIES = [
    "sensor.claude_fleet_status", "sensor.claude_sessions_running", "sensor.claude_sessions_working",
    "sensor.claude_sessions_stale", "sensor.claude_sessions_waiting", "sensor.claude_sessions_idle",
    "sensor.claude_tokens_today", "sensor.claude_cache_hit_today", "sensor.claude_peak_today",
    "sensor.claude_plan_source", "sensor.claude_plan_session_usage", "sensor.claude_plan_weekly_usage",
    "sensor.claude_plan_usage_rate", "sensor.claude_plan_session_resets_in", "sensor.claude_session_pace",
    "sensor.claude_weekly_pace", "sensor.claude_fleet_records", "sensor.claude_archived_today",
    "sensor.claude_weekly_points_by_day", "sensor.claude_rtk_saved_today", "sensor.claude_rtk_share_today",
    "sensor.claude_tokens_today_opus", "sensor.claude_tokens_today_sonnet", "sensor.claude_tokens_today_haiku",
    "sensor.claude_output_tokens_today_opus", "binary_sensor.claude_anyone_waiting",
    "binary_sensor.claude_fleet_setup_needed", "input_text.claude_fleet_quip",
    "input_boolean.claude_lamp_enabled", "input_datetime.claude_recap_time",
]


def check_entities(states):
    by_id = {s["entity_id"]: s for s in states}
    out = []
    for e in KEY_ENTITIES:
        if e not in by_id:
            out.append("entity missing: %s (did the package load?)" % e)
        elif by_id[e]["state"] == "unavailable":
            out.append("entity unavailable: %s (its template raised?)" % e)
    n = sum(1 for s in states if s["entity_id"].startswith("sensor.claude_"))
    if n < 30:
        out.append("only %d sensor.claude_* entities; the package defines far more" % n)
    return out


# ================================================================== HA's log
HEADER = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+) (DEBUG|INFO|WARNING|ERROR|CRITICAL) "
                    r"\(([^)]*)\) \[([^\]]+)\] ?(.*)$")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
LOOSE_LEVEL = re.compile(r"\b(ERROR|CRITICAL)\b")

# Loggers whose warnings are Claude Fleet's: its template entities, its
# automations and scripts, its MQTT entities, and the helpers they run through.
FLEET_LOGGERS = (
    "homeassistant.components.template", "homeassistant.helpers.template",
    "homeassistant.helpers.event", "homeassistant.helpers.script",
    "homeassistant.components.automation", "homeassistant.components.script",
    "homeassistant.components.mqtt", "homeassistant.components.input_",
    "homeassistant.components.lovelace", "homeassistant.components.sensor",
    "homeassistant.components.binary_sensor", "homeassistant.helpers.entity",
)
# The one allowed warning. HA stops the loop itself and logs it; the package's
# sensors read each other, as their comments explain.
ALLOWED_WARNINGS = ("Template loop detected while processing event",)


def parse_log(text):
    """[{time, level, thread, logger, msg, lines}], continuation lines (tracebacks,
    template source) folded into the entry they belong to."""
    entries = []
    for raw in text.splitlines():
        line = ANSI.sub("", raw).rstrip()
        m = HEADER.match(line)
        if m:
            entries.append({"time": m.group(1), "level": m.group(2), "thread": m.group(3),
                            "logger": m.group(4), "msg": m.group(5), "lines": [line]})
        elif entries and line:
            entries[-1]["lines"].append(line)
    return entries


def is_fleet(e):
    text = "\n".join(e["lines"]).lower()
    return e["logger"].startswith(FLEET_LOGGERS) or "claude" in text


def check_log(text):
    out = []
    entries = parse_log(text)
    if not entries and text.strip():
        out.append("the log has %d lines but none parsed as a log entry; has HA's format changed?"
                   % len(text.splitlines()))
    # an error that is not in the format above would otherwise pass unseen,
    # folded into the entry before it as if it were a traceback line
    for raw in text.splitlines():
        line = ANSI.sub("", raw).strip()
        if not HEADER.match(line) and LOOSE_LEVEL.search(line):
            out.append("log: a line that reads as an error but not in HA's log format: %s" % line[:300])
    seen = {}   # the same message repeats every render: one line, with a count
    for e in entries:
        head = "%s [%s] %s" % (e["level"], e["logger"], e["msg"][:300])
        if e["level"] in ("ERROR", "CRITICAL"):
            detail = [l for l in e["lines"][1:] if l.strip()][-1:] if len(e["lines"]) > 1 else []
            head += ("  ... " + detail[0][:200]) if detail else ""
        elif not (e["level"] == "WARNING" and is_fleet(e)) or any(a in e["msg"] for a in ALLOWED_WARNINGS):
            continue
        if head in seen:
            seen[head][1] += 1
        else:
            seen[head] = [e["time"], 1]
    for head, (first, n) in seen.items():
        out.append("log: %s %s%s" % (first, head, "  (x%d)" % n if n > 1 else ""))
    return out


def log_summary(text):
    """The warnings check_log lets through, for the report: not failures, but
    worth a glance when a new HA release starts saying something."""
    return ["%s [%s] %s" % (e["level"], e["logger"], e["msg"][:160]) for e in parse_log(text)
            if e["level"] == "WARNING" and not is_fleet(e)]


# ============================================================ repair issues
def check_repairs(issues):
    out = []
    for i in issues:
        dom = i.get("domain") or i.get("issue_domain") or ""
        blob = json.dumps(i).lower()
        if dom in ("template", "mqtt") or i.get("issue_domain") in ("template", "mqtt") or "claude" in blob:
            out.append("repair issue: %s / %s %s" % (dom, i.get("issue_id"),
                                                       json.dumps(i.get("translation_placeholders") or {})[:200]))
    return out


# ================================================================== sessions
def expected_from(sessions, gen_sessions):
    """What HA should show, from what gen.py generated: sessions.json (per-session
    tokens as the hook counts them, the seeded counters, the final state) and
    gen.SESSIONS (the PRs, and whether each was opened today)."""
    exp = {"sessions": {}, "tokens": 0, "output": 0, "prompts": 0, "prs": set()}
    for p in sessions:
        k = p["tokens"]
        exp["sessions"][p["sid"]] = {"label": p["label"], "final": p["final"], "stale": p["stale"],
                                     "prompts": p["prompts"], "tokens": k}
        exp["tokens"] += k["input"] + k["output"] + k["cache_read"] + k["cache_write"]
        exp["output"] += k["output"]
        exp["prompts"] += p["prompts"]
    for s in gen_sessions:
        for num, ago in s.get("prs", []):
            if ago >= 0:          # a negative age is a PR opened yesterday
                exp["prs"].add("#%d" % num)
    finals = [v["final"] for v in exp["sessions"].values()]
    stale = sum(1 for v in exp["sessions"].values() if v["stale"])
    exp["counts"] = {"running": sum(1 for f in finals if f != "ended"),
                     "working": finals.count("working") - stale, "stale": stale,
                     "waiting": finals.count("needs_input"), "idle": finals.count("idle")}
    worst = ("needs_input" if exp["counts"]["waiting"] else "stale" if stale else
             "working" if exp["counts"]["working"] else "idle" if exp["counts"]["idle"] else "none")
    exp["fleet_status"] = worst
    return exp


def check_records(states, exp, today):
    """The output record: the demo seeds a past one BELOW today's output, so today
    must overtake it with exactly the sessions' summed output. That proves the record
    is fed from today's output, not merely present."""
    rec = next((s for s in states if s["entity_id"] == "sensor.claude_fleet_records"), None)
    r = (rec or {}).get("attributes", {}).get("records")
    if not isinstance(r, dict):
        return ["sensor.claude_fleet_records has no records table"]
    x = r.get("output_day")
    if not isinstance(x, dict):
        return ["no 'output_day' record (most output in a day)"]
    out = []
    if int(x.get("value", -1)) != exp["output"]:
        out.append("output record = %s, expected today's output %d" % (x.get("value"), exp["output"]))
    if x.get("id") != today:
        out.append("output record set on %s, expected today (%s) to have overtaken the seeded one" % (x.get("id"), today))
    return out


def check_sessions(states, exp):
    out = []
    by_id = {s["entity_id"]: s for s in states}
    ss = [s for s in states if s["attributes"].get("claude_session")]
    live = {s["attributes"].get("session_id"): s for s in ss}
    if len(ss) != len(exp["sessions"]):
        out.append("%d session entities, expected %d: %s" % (
            len(ss), len(exp["sessions"]), sorted(s["attributes"].get("label", "?") for s in ss)))
    for sid, e in exp["sessions"].items():
        s = live.get(sid)
        if s is None:
            out.append("session missing: %s (%s)" % (e["label"], e["final"]))
            continue
        a = s["attributes"]
        if s["state"] != e["final"]:
            out.append("session %r is %s, expected %s" % (e["label"], s["state"], e["final"]))
        if a.get("label") != e["label"]:
            out.append("session %s labelled %r, expected %r" % (sid[:8], a.get("label"), e["label"]))
        if int(a.get("prompts_today", -1)) != e["prompts"]:
            out.append("session %r: prompts_today %s, expected %d" % (e["label"], a.get("prompts_today"), e["prompts"]))
        k = a.get("tokens_today") or {}
        for f in ("input", "output", "cache_read", "cache_write", "calls"):
            if k.get(f) != e["tokens"][f]:
                out.append("session %r: tokens_today.%s %s, expected %s" % (e["label"], f, k.get(f), e["tokens"][f]))
    for sid, s in live.items():
        if sid not in exp["sessions"]:
            out.append("unexpected session: %r" % s["attributes"].get("label"))

    def st(e):
        return by_id.get(e, {}).get("state")
    for name, want in exp["counts"].items():
        got = st("sensor.claude_sessions_" + name)
        if got != str(want):
            out.append("sensor.claude_sessions_%s = %s, expected %s" % (name, got, want))
    if st("sensor.claude_fleet_status") != exp["fleet_status"]:
        out.append("sensor.claude_fleet_status = %s, expected %s" % (st("sensor.claude_fleet_status"), exp["fleet_status"]))
    want_on = "on" if exp["counts"]["waiting"] else "off"
    if st("binary_sensor.claude_anyone_waiting") != want_on:
        out.append("binary_sensor.claude_anyone_waiting = %s, expected %s" % (st("binary_sensor.claude_anyone_waiting"), want_on))
    if st("binary_sensor.claude_fleet_setup_needed") != "off":
        out.append("binary_sensor.claude_fleet_setup_needed = %s, expected off (every session reports tokens)"
                   % st("binary_sensor.claude_fleet_setup_needed"))
    # ---- the day's sums
    if st("sensor.claude_tokens_today") != str(exp["tokens"]):
        out.append("sensor.claude_tokens_today = %s, expected %d (the sum of every session's tokens)"
                   % (st("sensor.claude_tokens_today"), exp["tokens"]))
    fam = sum(int(float(st("sensor.claude_tokens_today_" + f) or 0)) for f in ("opus", "sonnet", "haiku", "fable", "other"))
    if fam != exp["tokens"]:
        out.append("tokens by family sum to %d, expected %d" % (fam, exp["tokens"]))
    prompts = sum(int(s["attributes"].get("prompts_today", 0)) for s in ss)
    if prompts != exp["prompts"]:
        out.append("prompts today sum to %d, expected %d" % (prompts, exp["prompts"]))
    prs = set()
    for s in ss:
        prs.update(s["attributes"].get("prs_today") or [])
    if prs != exp["prs"]:
        out.append("PRs today %s, expected %s" % (sorted(prs), sorted(exp["prs"])))
    return out


TODAY_LINE = re.compile(r"\*\*(\d+)\*\* sessions · \*\*(\d+)\*\* prompts · \*\*(\d+)\*\* PRs")


def check_today_card(rendered, exp):
    """The Today card does its own summing, in the dashboard: its first line must
    agree with what the scenario generated."""
    outs = [r for r in rendered if r["title"] == "Today" and r.get("result") is not None]
    if not outs:
        return ["no rendered Today card to check the day's sums against"]
    out = []
    for r in outs:
        m = TODAY_LINE.search(r["result"])
        if not m:
            out.append("Today card (%s): no 'N sessions · N prompts · N PRs' line in %r" % (r["path"], r["result"][:200]))
            continue
        got = tuple(int(x) for x in m.groups())
        want = (len(exp["sessions"]), exp["prompts"], len(exp["prs"]))
        if got != want:
            out.append("Today card (%s) says %d sessions, %d prompts, %d PRs; expected %d, %d, %d"
                       % ((r["path"],) + got + want))
    return out


# ================================================================ plan usage
def check_plan(states):
    by_id = {s["entity_id"]: s["state"] for s in states}
    want = {"sensor.claude_plan_source": "ok", "sensor.claude_weekly_pace": "tight",
            "sensor.claude_session_pace": "ok"}
    return ["%s = %s, expected %s" % (e, by_id.get(e), v) for e, v in want.items() if by_id.get(e) != v]


# ================================================================ alert blueprints
# up.sh installs both blueprints and an automation from each, with a logbook entry
# as the "other action" (the demo has no phone; the phone path was verified by hand,
# AUTO-58). The scenario leaves one session waiting, so the waiting alert must have
# fired and named it. The limits alert never fires here (its pace starts unknown,
# which by design is not news), so for it, loading is the check.
ALERT_AUTOMATIONS = ["automation.claude_fleet_alert_waiting", "automation.claude_fleet_alert_limits"]
ALERT_LOG_NAME = "Claude Fleet alert"


def check_alerts(states, logbook, exp):
    out = []
    by_id = {s["entity_id"]: s["state"] for s in states}
    for a in ALERT_AUTOMATIONS:
        if by_id.get(a) != "on":
            out.append("%s = %s, expected on (unavailable means HA could not load the blueprint)"
                       % (a, by_id.get(a)))
    waiting = [v["label"] for v in exp["sessions"].values() if v["final"] == "needs_input"]
    sent = [e.get("message", "") for e in logbook if e.get("name") == ALERT_LOG_NAME]
    for label in waiting:
        if not any("Claude is waiting on you" in m and label in m for m in sent):
            out.append("no waiting alert named %r; alerts sent: %s" % (label, sent[-5:] or "none"))
    return out


# ======================================================= markdown templates
def markdown_cards(config):
    """Every markdown card in a dashboard config, visible or not (phone-only
    cards included), with a path saying where it is."""
    found = []

    def walk(o, path):
        if isinstance(o, dict):
            if o.get("type") == "markdown":
                found.append({"path": path, "title": o.get("title") or "", "content": o.get("content", "")})
            for k, v in o.items():
                walk(v, "%s/%s" % (path, o.get("title") if k == "cards" and o.get("title") else k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                t = v.get("title") or v.get("path") if isinstance(v, dict) else None
                walk(v, "%s[%s]" % (path, t or i))
    walk(config, "")
    return found


def classify_render(card, reply):
    """One card's render_template reply -> {..., result} or {..., error}. `reply`
    is (result message, first event or None)."""
    res, ev = reply
    r = dict(card)
    if res is not None and not res.get("success", False):
        r["error"] = "render refused: %s" % (res.get("error"),)
    elif ev is None:
        r["error"] = "no render result arrived"
    elif "error" in ev:
        r["error"] = "%s: %s" % (ev.get("level", "ERROR"), ev["error"])
    elif "result" not in ev:
        r["error"] = "unexpected render event: %s" % json.dumps(ev)[:200]
    else:
        r["result"] = str(ev["result"])
    return r


def check_render(rendered, min_cards=1):
    out = ["template: %s (%s): %s" % (r["path"], r["title"] or "untitled", r["error"])
           for r in rendered if "error" in r]
    if len(rendered) < min_cards:
        out.append("only %d markdown cards rendered, expected at least %d" % (len(rendered), min_cards))
    return out


async def render_all(ws_url, token, cards, timeout=30):
    import websockets
    rendered = []
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        a = json.loads(await ws.recv())
        if a.get("type") != "auth_ok":
            raise SystemExit("websocket auth failed: %s" % a)
        n = 0
        for c in cards:
            n += 1
            await ws.send(json.dumps({"id": n, "type": "render_template", "template": c["content"],
                                      "strict": True, "report_errors": True}))
            res = ev = None
            end = time.time() + timeout
            while time.time() < end:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - time.time())))
                except asyncio.TimeoutError:
                    break
                if m.get("id") != n:
                    continue
                if m.get("type") == "result":
                    res = m
                    if not m.get("success"):
                        break
                elif m.get("type") == "event":
                    ev = m.get("event")
                    break
            if res is not None and res.get("success"):
                n += 1
                await ws.send(json.dumps({"id": n, "type": "unsubscribe_events", "subscription": n - 1}))
            rendered.append(classify_render(c, (res, ev)))
    return rendered


# ================================================== static dashboard checks
# The keys each card reads. A key outside its card's list is ignored by the card:
# a typo, or a key at the wrong level, the way `interval` on a history explorer
# graph was (it belongs in `options`; on the graph it silently did nothing).
COMMON = {"type", "visibility", "grid_options", "layout_options", "view_layout"}
CARD_KEYS = {
    "markdown": COMMON | {"content", "title", "text_only", "card_size", "entity_id", "theme", "show_empty"},
    "grid": COMMON | {"cards", "columns", "square", "title"},
    "tile": COMMON | {"entity", "name", "icon", "color", "show_entity_picture", "vertical", "hide_state",
                      "state_content", "content_layout", "tap_action", "hold_action", "double_tap_action",
                      "icon_tap_action", "icon_hold_action", "icon_double_tap_action", "features",
                      "features_position"},
    "gauge": COMMON | {"entity", "name", "unit", "min", "max", "needle", "severity", "segments", "theme",
                       "attribute", "tap_action", "hold_action", "double_tap_action"},
    "custom:history-explorer-card": COMMON | {
        "header", "graphs", "defaultTimeRange", "uiLayout", "statistics", "labelAreaWidth", "cardName",
        "showUnavailable", "decimation", "rounding", "timeTicks", "lineGraphHeight", "barGraphHeight",
        "stateColors", "combineSameUnits", "recordedEntitiesOnly", "filterEntities", "entityOptions",
        "tooltip", "legendVisible", "refresh", "stateTextMode", "showCurrentValues", "axisAddMarginMin",
        "axisAddMarginMax", "numericStateColors", "stateTextMap", "showTooltipColors"},
}
HX_GRAPH_KEYS = {"type", "title", "entities", "options"}
HX_OPTION_KEYS = {"ymin", "ymax", "interval", "stacked", "timeline"}
HX_ENTITY_KEYS = {"entity", "name", "color", "fill", "lineMode", "width", "hidden", "dashMode",
                  "scale", "process", "entityOptions"}
HX_STATISTICS_KEYS = {"enabled", "mode", "period", "force"}
HX_UILAYOUT_KEYS = {"toolbar", "selector", "interval", "sticky"}
VIEW_KEYS = {"title", "path", "icon", "type", "max_columns", "sections", "cards", "badges", "theme",
             "subview", "back_path", "visible", "dense_section_placement", "top_margin", "header"}
SECTION_KEYS = {"type", "cards", "column_span", "row_span", "title", "visibility"}
CONDITION_KEYS = {"condition", "entity", "state", "state_not", "media_query", "above", "below",
                  "conditions", "users", "attribute"}


def _unknown(where, d, allowed):
    return ["%s: key %r is not one this card reads (ignored)" % (where, k) for k in d if k not in allowed]


def _check_card(c, where, out):
    t = c.get("type")
    if t in CARD_KEYS:
        out += _unknown("%s %s" % (where, t), c, CARD_KEYS[t])
    for i, cond in enumerate(c.get("visibility") or []):
        if isinstance(cond, dict):
            out += _unknown("%s visibility[%d]" % (where, i), cond, CONDITION_KEYS)
    if t == "custom:history-explorer-card":
        _check_history(c, where, out)
    for i, sub in enumerate(c.get("cards") or []):
        if isinstance(sub, dict):
            _check_card(sub, "%s/cards[%d]" % (where, i), out)


def _check_history(c, where, out):
    header = str(c.get("header", ""))
    name = "%s (%r)" % (where, header)
    daily = "by day" in header.lower() or "per day" in header.lower()
    out += _unknown(name + " statistics", c.get("statistics") or {}, HX_STATISTICS_KEYS)
    out += _unknown(name + " uiLayout", c.get("uiLayout") or {}, HX_UILAYOUT_KEYS)
    for i, g in enumerate(c.get("graphs") or []):
        gw = "%s graphs[%d]" % (name, i)
        if "interval" in g:
            out.append("%s: `interval` set on the graph, where the card ignores it; it belongs in `options`" % gw)
        # (interval is reported above, in words that say where it belongs)
        out += _unknown(gw, {k: v for k, v in g.items() if k != "interval"}, HX_GRAPH_KEYS)
        opts = g.get("options") or {}
        out += _unknown(gw + " options", opts, HX_OPTION_KEYS)
        if daily and g.get("type") == "bar" and opts.get("interval") != "daily":
            out.append("%s: a by-day bar graph without `options: {interval: daily}` draws hourly bars" % gw)
        for j, e in enumerate(g.get("entities") or []):
            if isinstance(e, dict):
                out += _unknown("%s entities[%d]" % (gw, j), e, HX_ENTITY_KEYS)


def check_dashboard(config):
    out = []
    views = config.get("views") or []
    if not views:
        return ["the dashboard has no views"]
    for v in views:
        vw = "view %r" % v.get("path")
        out += _unknown(vw, v, VIEW_KEYS)
        for i, s in enumerate(v.get("sections") or []):
            out += _unknown("%s section[%d]" % (vw, i), s, SECTION_KEYS)
            for j, c in enumerate(s.get("cards") or []):
                _check_card(c, "%s section[%d]/cards[%d]" % (vw, i, j), out)
        for j, c in enumerate(v.get("cards") or []):
            _check_card(c, "%s cards[%d]" % (vw, j), out)
    return out


def load_dashboard(path=DASHBOARD):
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)


# =================================================================== self-test
BROKEN_TEMPLATE = "{{ states.sensor.x.attributes.missing.value }}"
SELFTEST_DAY = "2026-09-30"


def _synthetic():
    """A small scenario and the HA states it should produce."""
    sessions = []
    specs = [("a", "needs_input", False, 3), ("b", "working", False, 4), ("c", "working", True, 5),
             ("d", "idle", False, 1), ("e", "ended", False, 2), ("f", "working", False, 6)]
    for i, (key, final, stale, prompts) in enumerate(specs):
        sessions.append({"sid": "sid-" + key, "label": "Session " + key, "final": final, "stale": stale,
                         "prompts": prompts, "tokens": {"input": 10 + i, "output": 100 + i, "cache_read": 1000 * (i + 1),
                                                        "cache_write": 50 + i, "calls": 5 + i}})
    gen_sessions = [{"prs": [(7, 30)]}, {"prs": [(8, -1)]}, {"prs": [(9, 5), (10, 1)]}]
    exp = expected_from(sessions, gen_sessions)
    states = []
    for p in sessions:
        states.append({"entity_id": "sensor.claude_code_" + p["sid"], "state": p["final"],
                       "attributes": {"claude_session": True, "session_id": p["sid"], "label": p["label"],
                                      "prompts_today": p["prompts"], "tokens_today": dict(p["tokens"]),
                                      "prs_today": []}})
    states[0]["attributes"]["prs_today"] = ["#7"]
    states[2]["attributes"]["prs_today"] = ["#9", "#10"]
    fixed = {"sensor.claude_sessions_running": "5", "sensor.claude_sessions_working": "2",
             "sensor.claude_sessions_stale": "1", "sensor.claude_sessions_waiting": "1",
             "sensor.claude_sessions_idle": "1", "sensor.claude_fleet_status": "needs_input",
             "binary_sensor.claude_anyone_waiting": "on", "binary_sensor.claude_fleet_setup_needed": "off",
             "sensor.claude_tokens_today": str(exp["tokens"]), "sensor.claude_tokens_today_opus": str(exp["tokens"] - 100),
             "sensor.claude_tokens_today_haiku": "100", "sensor.claude_plan_source": "ok",
             "sensor.claude_weekly_pace": "tight", "sensor.claude_session_pace": "ok"}
    for e in KEY_ENTITIES:
        fixed.setdefault(e, "0")
    for i in range(30):
        fixed.setdefault("sensor.claude_extra_%d" % i, "0")
    states += [{"entity_id": k, "state": v, "attributes": {}} for k, v in fixed.items()]
    states = [s for s in states if s["entity_id"] != "sensor.claude_fleet_records"]
    states.append({"entity_id": "sensor.claude_fleet_records", "state": "1", "attributes": {"records": {
        "output_day": {"value": exp["output"], "label": "", "id": SELFTEST_DAY, "at": SELFTEST_DAY + "T12:00:00"}}}})
    return sessions, gen_sessions, exp, states


def _set(states, eid, state=None, **attrs):
    out = json.loads(json.dumps(states))
    for s in out:
        if s["entity_id"] == eid:
            if state is not None:
                s["state"] = state
            s["attributes"].update(attrs)
    return out


def _drop(states, eid):
    return [s for s in states if s["entity_id"] != eid]


GOOD_LOG = """\
2026-09-30 12:49:12.436 WARNING (ImportExecutor_0) [py.warnings] /usr/local/lib/python3.14/site-packages/rich/segment.py:547: SyntaxWarning: 'return' in a 'finally' block
\x1b[33m2026-09-30 12:49:35.998 WARNING (MainThread) [homeassistant.components.template.template_entity] Template loop detected while processing event: <Event state_changed[L]: entity_id=sensor.claude_fleet_status>, skipping template render for Template[{{ states.sensor | selectattr('attributes.claude_session', 'defined') }}]\x1b[0m
     | selectattr('state', 'in', ['working']) | list | count }}]
2026-09-30 12:49:20.290 WARNING (MainThread) [homeassistant.components.http.ban] Login attempt or request with invalid authentication from 192.168.97.1 (192.168.97.1). Requested URL: '/api/websocket'.
2026-09-30 12:50:00.000 INFO (MainThread) [homeassistant.core] Starting Home Assistant
"""
BAD_LOGS = {
    "an ERROR from any logger": "2026-09-30 13:00:10.260 ERROR (Recorder) [homeassistant.helpers.recorder] Error executing query\nTraceback (most recent call last):\n  File \"x.py\", line 1\nsqlite3.IntegrityError: UNIQUE constraint failed\n",
    "a template error": "2026-09-30 13:00:00.100 ERROR (MainThread) [homeassistant.helpers.template] Template variable error: 'dict object' has no attribute 'missing' when rendering '{{ states.sensor.x.attributes.missing.value }}'\n",
    "a warning from a Claude Fleet template": "2026-09-30 13:00:00.100 WARNING (MainThread) [homeassistant.components.template.sensor] Template variable warning: 'None' has no attribute 'last_reported' when rendering '{{ st.last_reported }}'\n",
    "an MQTT entity warning": "\x1b[33m2026-09-30 13:00:00.388 WARNING (MainThread) [homeassistant.components.mqtt.entity] Erroneous JSON: \x1b[0m\n",
    "an automation warning": "2026-09-30 13:00:00.100 WARNING (MainThread) [homeassistant.components.automation.claude_nightly_cleanup] Claude · remove ended/abandoned sessions: Already running\n",
    "any warning that names claude": "2026-09-30 13:00:00.100 WARNING (MainThread) [homeassistant.components.recorder.core] Entity sensor.claude_tokens_today changed unit\n",
    "a CRITICAL": "2026-09-30 13:00:00.100 CRITICAL (MainThread) [homeassistant.bootstrap] Something went badly wrong\n",
    "a log in a format nobody parses": "Sep 30 13:00:00 ha homeassistant: ERROR something\nand more\n",
}

GOOD_DASHBOARD = """
views:
  - title: Tokens
    path: tokens
    type: sections
    sections:
      - type: grid
        cards:
          - type: markdown
            title: Today
            content: "{{ 1 }}"
            visibility:
              - condition: screen
                media_query: "(max-width: 767px)"
          - type: grid
            columns: 2
            cards:
              - type: markdown
                content: nested
          - type: custom:history-explorer-card
            header: "Tokens by day"
            graphs:
              - type: bar
                options: { stacked: true, interval: daily }
                entities:
                  - entity: sensor.claude_tokens_today_opus
                    color: "#7e57c2"
              - type: line
                options: { ymin: 0 }
                entities:
                  - entity: sensor.claude_rtk_share_today
                    lineMode: stepped
"""
BAD_DASHBOARDS = {
    "interval at graph level (the real bug)": ("options: { stacked: true, interval: daily }", "interval: daily\n                options: { stacked: true }"),
    "a by-day bar graph with no daily interval": ("options: { stacked: true, interval: daily }", "options: { stacked: true }"),
    "a misspelt markdown key": ("content: \"{{ 1 }}\"", "contents: \"{{ 1 }}\""),
    "a key on a graph entity the card ignores": ("lineMode: stepped", "line_mode: stepped"),
    "an unknown graph option": ("options: { ymin: 0 }", "options: { ymin: 0, y_min: 0 }"),
    "a visibility condition key at the wrong level": ("media_query: \"(max-width: 767px)\"", "media_query: \"(max-width: 767px)\"\n                mediaquery: x"),
    "a nested card with a stray key": ("content: nested", "content: nested\n                entities: [sensor.x]"),
}


def self_test():
    import yaml
    results = []

    def expect(name, failures, should_fail):
        ok = bool(failures) == should_fail
        results.append((ok, name, failures))

    # ---- log
    expect("log: HA's normal noise, a Template loop and ANSI colour pass", check_log(GOOD_LOG), False)
    for name, bad in BAD_LOGS.items():
        expect("log: %s fails" % name, check_log(GOOD_LOG + bad), True)
    folded = parse_log(BAD_LOGS["an ERROR from any logger"])
    expect("log: a traceback folds into its entry", [] if len(folded) == 1 and len(folded[0]["lines"]) == 4 else ["folded %s" % folded], False)

    # ---- repairs
    expect("repairs: an unrelated issue passes", check_repairs([{"domain": "homeassistant", "issue_id": "x"}]), False)
    expect("repairs: a template issue fails", check_repairs([{"domain": "template", "issue_id": "x"}]), True)
    expect("repairs: an MQTT issue fails", check_repairs([{"domain": "mqtt", "issue_id": "y"}]), True)

    # ---- entities, sessions, plan
    sessions, gen_sessions, exp, states = _synthetic()
    expect("entities: all present passes", check_entities(states), False)
    expect("entities: a missing sensor.claude_fleet_status fails", check_entities(_drop(states, "sensor.claude_fleet_status")), True)
    expect("entities: an unavailable one fails", check_entities(_set(states, "sensor.claude_session_pace", "unavailable")), True)
    expect("sessions: the synthetic scenario passes", check_sessions(states, exp), False)
    bad = {
        "a session in the wrong state": _set(states, "sensor.claude_code_sid-b", "idle"),
        "the ended session missing": _drop(states, "sensor.claude_code_sid-e"),
        "a seventh session": states + [{"entity_id": "sensor.claude_code_z", "state": "idle",
                                        "attributes": {"claude_session": True, "session_id": "z", "label": "Z"}}],
        "stale count 0": _set(states, "sensor.claude_sessions_stale", "0"),
        "running count off": _set(states, "sensor.claude_sessions_running", "6"),
        "waiting count off": _set(states, "sensor.claude_sessions_waiting", "0"),
        "fleet status wrong": _set(states, "sensor.claude_fleet_status", "stale"),
        "anyone waiting off": _set(states, "binary_sensor.claude_anyone_waiting", "off"),
        "tokens today off by one": _set(states, "sensor.claude_tokens_today", str(exp["tokens"] + 1)),
        "a session's tokens off": _set(states, "sensor.claude_code_sid-a",
                                       tokens_today=dict(sessions[0]["tokens"], cache_read=1)),
        "a session's prompts off": _set(states, "sensor.claude_code_sid-d", prompts_today=9),
        "yesterday's PR counted today": _set(states, "sensor.claude_code_sid-d", prs_today=["#8"]),
        "a PR missing": _set(states, "sensor.claude_code_sid-c", prs_today=["#9"]),
        "setup needed on": _set(states, "binary_sensor.claude_fleet_setup_needed", "on"),
    }
    for name, st in bad.items():
        expect("sessions: %s fails" % name, check_sessions(st, exp), True)
    expect("records: today's output overtaking the seed passes", check_records(states, exp, SELFTEST_DAY), False)
    rset = lambda **o: _set(states, "sensor.claude_fleet_records", records={"output_day": dict(
        {"value": exp["output"], "label": "", "id": SELFTEST_DAY}, **o)})
    expect("records: the output record off by one fails", check_records(rset(value=exp["output"] + 1), exp, SELFTEST_DAY), True)
    expect("records: the seed still standing (not overtaken) fails", check_records(rset(id="2026-09-12"), exp, SELFTEST_DAY), True)
    expect("records: no output record fails", check_records(_set(states, "sensor.claude_fleet_records", records={}), exp, SELFTEST_DAY), True)
    expect("records: no records table fails", check_records(_drop(states, "sensor.claude_fleet_records"), exp, SELFTEST_DAY), True)
    expect("expected: yesterday's PR is not today's", [] if exp["prs"] == {"#7", "#9", "#10"} else [exp["prs"]], False)
    expect("plan: ok / tight / ok passes", check_plan(states), False)
    expect("plan: a stale source fails", check_plan(_set(states, "sensor.claude_plan_source", "stale")), True)
    expect("plan: weekly pace ok fails", check_plan(_set(states, "sensor.claude_weekly_pace", "ok")), True)
    expect("plan: session pace unknown fails", check_plan(_set(states, "sensor.claude_session_pace", "unknown")), True)

    # ---- alert blueprints
    al_states = states + [{"entity_id": a, "state": "on", "attributes": {}} for a in ALERT_AUTOMATIONS]
    al_log = [{"name": ALERT_LOG_NAME, "message": "Claude is waiting on you: Session a"},
              {"name": "Something else", "message": "Claude is waiting on you: Session b"}]
    expect("alerts: both loaded and the waiting session named passes", check_alerts(al_states, al_log, exp), False)
    expect("alerts: a blueprint automation unavailable fails",
           check_alerts(_set(al_states, ALERT_AUTOMATIONS[1], "unavailable"), al_log, exp), True)
    expect("alerts: a blueprint automation missing fails",
           check_alerts(_drop(al_states, ALERT_AUTOMATIONS[0]), al_log, exp), True)
    expect("alerts: no alert sent fails", check_alerts(al_states, al_log[1:], exp), True)
    expect("alerts: an alert naming another session fails",
           check_alerts(al_states, [{"name": ALERT_LOG_NAME, "message": "Claude is waiting on you: Session b"}], exp), True)

    # ---- Today card
    line = "**6** sessions · **%d** prompts · **3** PRs · **28** min waiting on you" % exp["prompts"]
    good = [{"path": "/x", "title": "Today", "result": line}]
    expect("today card: right sums pass", check_today_card(good, exp), False)
    expect("today card: wrong prompts fail", check_today_card([dict(good[0], result=line.replace("**%d** prompts" % exp["prompts"], "**1** prompts"))], exp), True)
    expect("today card: an unrecognisable line fails", check_today_card([dict(good[0], result="nothing")], exp), True)
    expect("today card: no card fails", check_today_card([], exp), True)

    # ---- render: the classification of HA's replies, and the card walk
    card = {"path": "/p", "title": "T", "content": BROKEN_TEMPLATE}
    ok_reply = ({"id": 1, "type": "result", "success": True}, {"result": "fine", "listeners": {}})
    expect("render: a clean render passes", check_render([classify_render(card, ok_reply)]), False)
    expect("render: an error event fails", check_render([classify_render(card, ({"success": True}, {"error": "UndefinedError: 'dict object' has no attribute 'missing'", "level": "ERROR"}))]), True)
    expect("render: a refused template fails", check_render([classify_render(card, ({"success": False, "error": {"code": "template_error"}}, None))]), True)
    expect("render: no reply at all fails", check_render([classify_render(card, ({"success": True}, None))]), True)
    expect("render: zero cards found fails", check_render([], min_cards=1), True)
    cfg = yaml.safe_load(GOOD_DASHBOARD)
    n = len(markdown_cards(cfg))
    expect("render: the walk finds nested and phone-only cards (2)", [] if n == 2 else ["found %d" % n], False)

    # ---- static dashboard checks
    expect("dashboard: a clean snippet passes", check_dashboard(cfg), False)
    for name, (a, b) in BAD_DASHBOARDS.items():
        assert a in GOOD_DASHBOARD, name
        expect("dashboard: %s fails" % name, check_dashboard(yaml.safe_load(GOOD_DASHBOARD.replace(a, b, 1))), True)
    expect("dashboard: the real claude_fleet.yaml passes", check_dashboard(load_dashboard()), False)
    real = len(markdown_cards(load_dashboard()))
    expect("dashboard: the real one has markdown cards to render (%d)" % real, [] if real >= 10 else ["only %d" % real], False)

    bad_n = 0
    for ok, name, failures in results:
        print("  %s  %s" % ("ok  " if ok else "FAIL", name))
        if not ok:
            bad_n += 1
            print("        got: %s" % (failures[:3] if failures else "no failures (the check could not fail)"))
    print("\nself-test: %d checks, %d wrong" % (len(results), bad_n))
    return bad_n == 0


# =================================================================== live run
def live():
    sys.path.insert(0, DEMO)
    import ha          # the demo's API helper: BASE_URL, SECRETS, tokens
    import gen         # the scenario's own data: what HA should show

    failures = {}

    def record(section, fails):
        failures[section] = fails
        print("  %s  %s%s" % ("ok  " if not fails else "FAIL", section, "" if not fails else " (%d)" % len(fails)))
        for f in fails[:40]:
            print("        " + f)
        if len(fails) > 40:
            print("        ... and %d more" % (len(fails) - 40))

    def states():
        st, r = ha.http("GET", "/api/states", token=ha.token())
        if st != 200:
            raise SystemExit("GET /api/states: %s %s" % (st, r))
        return r

    with open(os.path.join(os.environ["CF_DEMO_DIR"], "scenario", "sessions.json")) as f:
        exp = expected_from(json.load(f), gen.SESSIONS)
    print("expected: %d sessions, %d tokens, %d prompts, PRs %s"
          % (len(exp["sessions"]), exp["tokens"], exp["prompts"], sorted(exp["prs"])))

    # ---- states: sessions first, while the ended one is inside its grace. The
    # counts are templates over every sensor, which HA re-renders at most once
    # a second and skips while a loop is detected, so right after the scenario's
    # last event they can lag it by a few seconds: wait for them to settle, then
    # report whatever is still wrong.
    # The records sensor re-renders once a minute, so it gets up to 75 s more.
    end = time.time() + float(os.environ.get("CF_CHECK_SETTLE", "60"))
    while True:
        s0 = states()
        f_ent, f_ses = check_entities(s0), check_sessions(s0, exp)
        if not (f_ent or f_ses) or time.time() > end:
            break
        time.sleep(3)
    record("package loaded (key entities)", f_ent)
    record("sessions, counts and the day's sums", f_ses)
    today = next((s["attributes"].get("day") for s in s0 if s["attributes"].get("claude_session")), "")
    end = time.time() + 75
    while True:
        f_rec = check_records(states(), exp, today)
        if not f_rec or time.time() > end:
            break
        time.sleep(5)
    record("personal records: today's output overtook the seeded output record", f_rec)

    # ---- markdown templates, strict, with the live controls first: a broken
    # template must come back as an error through this exact path, a trivial one
    # must come back clean
    ws_url = ha.BASE.replace("http", "ws", 1) + "/api/websocket"
    config = ha.ws([{"type": "lovelace/config", "url_path": "claude-fleet"}])[0]["result"]
    controls = asyncio.run(render_all(ws_url, ha.token(), [
        {"path": "control", "title": "broken", "content": BROKEN_TEMPLATE},
        {"path": "control", "title": "clean", "content": "{{ states('sensor.claude_fleet_status') }}"}]))
    ctl = []
    if "error" not in controls[0]:
        ctl.append("the deliberately broken template rendered without an error: %r" % controls[0].get("result"))
    if "error" in controls[1]:
        ctl.append("a trivial template failed to render: %s" % controls[1]["error"])
    record("render controls (a broken template fails, a clean one passes)", ctl)
    cards = markdown_cards(config)
    rendered = asyncio.run(render_all(ws_url, ha.token(), cards))
    record("markdown cards render strictly (%d cards, 3 views, phone-only included)" % len(cards),
           check_render(rendered, min_cards=10))
    record("Today card's sums", check_today_card(rendered, exp))

    # ---- static checks on the dashboard, as HA served it and as the repo has it
    record("dashboard config keys and daily intervals (as HA loaded it)", check_dashboard(config))
    record("dashboard config keys and daily intervals (the repo file)", check_dashboard(load_dashboard()))

    # ---- plan usage: the burn rate needs five minutes of readings; the scenario
    # seeds them, so this should not wait, but it may after a slow restart
    wait = float(os.environ.get("CF_CHECK_RATE_WAIT", "420"))
    end = time.time() + wait
    while True:
        s1 = states()
        rate = {s["entity_id"]: s["state"] for s in s1}.get("sensor.claude_plan_usage_rate")
        if re.match(r"^-?[0-9.]+$", rate or "") or time.time() > end:
            break
        time.sleep(10)
    record("plan source ok, weekly pace tight, session pace ok", check_plan(s1))

    # ---- the alert blueprints: loaded, and the waiting one fired during the scenario
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 6 * 3600)) + "+00:00"
    st, lb = ha.http("GET", "/api/logbook/" + since, token=ha.token())
    record("alert blueprints loaded; the waiting alert named the waiting session",
           check_alerts(states(), lb if st == 200 else [], exp)
           + ([] if st == 200 else ["GET /api/logbook: %s %s" % (st, lb)]))

    # ---- repairs and the log, last, so they cover everything above
    issues = ha.ws([{"type": "repairs/list_issues"}])[0]["result"]["issues"]
    record("repair issues (template, mqtt)", check_repairs(issues))
    log = subprocess.run(["docker", "logs", os.environ["C_HA"]], capture_output=True, text=True)
    text = log.stdout + log.stderr
    if log.returncode != 0 or not text.strip():
        record("Home Assistant log", ["could not read `docker logs %s`: %s" % (os.environ["C_HA"], log.stderr[:200])])
    else:
        record("Home Assistant log (no ERROR; no Claude Fleet WARNING but Template loop)", check_log(text))
        other = log_summary(text)
        if other:
            print("        (other warnings, not failures: %d)" % len(other))
            for o in sorted(set(other))[:10]:
                print("          " + o)

    bad = {k: v for k, v in failures.items() if v}
    print("")
    if bad:
        print("FAILED: %d of %d checks" % (len(bad), len(failures)))
        for k, v in bad.items():
            print("  - %s: %s" % (k, v[0][:300]))
        return False
    print("PASSED: all %d checks" % len(failures))
    return True


def main():
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(0 if self_test() else 1)
    if sys.argv[1:]:
        print(__doc__)
        sys.exit(2)
    for k in ("CF_DEMO_DIR", "SECRETS", "BASE_URL", "C_HA"):
        if not os.environ.get(k):
            print("%s is not set; run through tests/e2e/run.sh, or source demo/lib.sh first" % k)
            sys.exit(2)
    sys.exit(0 if live() else 1)


if __name__ == "__main__":
    main()
