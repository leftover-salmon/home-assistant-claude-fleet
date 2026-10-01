"""The demo scenario's data: invented sessions, transcripts, history and plan usage.

    python gen.py prepare        repos, transcripts, RTK ledgers, the fake claude's
                                 answers, and everything below, into $CF_DEMO_DIR/scenario
    python gen.py restore        seed HA's restore state (HA must be STOPPED)
    python gen.py import-stats   import 30 days of hourly long-term statistics
    python gen.py plan           the fake plan-usage source (four template helpers)
    python gen.py inject         the invented history as recorded states (HA must be STOPPED)
    python gen.py run            drive the sessions through the installed hook (ev.sh)
    python gen.py check          print what HA shows for each session

Every name, repo, number and line of text here is invented. Times are relative to
the moment `prepare` runs, so the scenario is always "today".
"""
import datetime as dt
import json
import math
import os
import random
import sqlite3
import subprocess
import sys
import time
import uuid
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DIR = os.environ["CF_DEMO_DIR"]
OUT = os.path.join(DIR, "scenario")
HOMES = os.path.join(DIR, "homes")
REPOS = os.path.join(DIR, "repos")
BIN = os.path.join(DIR, "bin")
TZ = ZoneInfo(os.environ.get("TZ_NAME", "America/Los_Angeles"))
CLI_VERSION = "2.1.284"          # every transcript's, and the fake claude's
HISTORY_DAYS = 30   # the Tokens tab's charts open on one month

MODELS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5",
          "haiku": "claude-haiku-4-5-20251001"}

# ---- the three computers ----------------------------------------------------
# stale: CLAUDE_HA_STALE_MINUTES. mini gets 1 so its silent "working" session
# turns stale after a minute of real waiting, rather than a faked timestamp.
# rtk: "on" = a ledger and RTK's hook in settings.json; "off" = RTK installed
# (on PATH) but its hook not wired, which the dashboard shows as "off".
MACHINES = {
    "studio": {"stale": 20, "rtk": "on", "rtk_share": 0.6},
    "laptop": {"stale": 0, "rtk": "off"},
    "mini": {"stale": 1, "rtk": "on", "rtk_share": 0.4},
}

# ---- the six sessions, in the order they are played ------------------------
# started / for_min / pr at: minutes before `prepare`. ctx: the context of the
# first and last request. calls: model calls today. prompts / wait_min: the day's
# counters so far, seeded into the hook's own state before the last event.
SESSIONS = [
    dict(key="bump-deps", machine="laptop", model="sonnet", final="ended",
         label="Bump dependencies in the CLI", repo="trail-cli", branch="deps/september-bumps",
         started=170, for_min=6, calls=58, ctx=(24000, 92000), prompts=7, wait_min=2,
         prs=[(57, 25)],
         summary="Updated the lockfile and fixed two deprecation warnings",
         asks=["Bump the CLI's dependencies to their latest minor versions",
               "The tests pass locally. Fix the two deprecation warnings too",
               "Looks good, open the PR"]),
    dict(key="backfill", machine="mini", model="opus", final="working", stale=True,
         label="Backfill search index for archived orders", repo="order-search", branch="main",
         started=236, for_min=94, calls=150, ctx=(31000, 64000), prompts=8, wait_min=0,
         compact_at=0.55, prs=[],
         summary="Re-running the backfill from batch 14 after a timeout",
         asks=["The search index is missing archived orders from before March. Plan a backfill",
               "Use batches of 5,000 and log progress per batch",
               "Run the backfill for 2023 and 2024, then report the counts"]),
    dict(key="webhooks", machine="studio", model="opus", final="needs_input",
         label="Migrate billing webhooks to v2", repo="billing-service", branch="feature/webhooks-v2",
         started=140, for_min=6, calls=110, ctx=(42000, 146000), prompts=12, wait_min=14,
         yesterday=True, prs=[(88, -1)],
         summary="Deciding how failed webhook deliveries should be retried",
         asks=["Carry on with the webhook migration from yesterday: the v2 payloads next",
               "Sign the payloads with the new key, and keep v1 running in parallel",
               "Go with signed payloads. What retry schedule would you use?"]),
    dict(key="ci-cache", machine="mini", model="haiku", final="idle",
         label="Tidy up the CI cache keys", repo="infra-ci", branch="chore/cache-keys",
         started=52, for_min=22, calls=30, ctx=(18000, 38000), prompts=4, wait_min=0, prs=[],
         summary="Proposed shorter cache keys for the build matrix",
         asks=["Our CI cache keys are enormous. Suggest something shorter that still invalidates correctly",
               "Write the new keys into the workflow, but don't push yet"]),
    dict(key="flaky-login", machine="laptop", model="sonnet", final="working",
         label="Fix flaky login test", repo="storefront-web", branch="fix/flaky-login-test",
         started=100, for_min=12, calls=92, ctx=(36000, 176000), prompts=15, wait_min=3,
         prs=[(1042, 22)],
         summary="Replacing a fixed sleep with a wait on the session cookie",
         asks=["The login test fails about one run in ten on CI. Find out why",
               "Don't just raise the timeout. What is it actually waiting for?",
               "It failed again on CI. Can you check whether the cookie is set before the redirect?"]),
    dict(key="offline", machine="studio", model="opus", final="working",
         label="Add offline mode to the recipe app", repo="recipe-app", branch="feature/offline-mode",
         started=190, for_min=38, calls=172, ctx=(28000, 312000), prompts=21, wait_min=9,
         prs=[(214, 64)], subagent=("haiku", 26),
         summary="Wiring the sync queue into the offline cache layer",
         asks=["Add an offline mode to the recipe app: saved recipes should open without a connection",
               "Store them in IndexedDB, not localStorage; some have photos",
               "Now make the sync queue retry with backoff when the connection comes back"]),
]

QUIP = "Five sessions running, and the one waiting on you has the best manners of the lot."


def sid_of(s):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "cf-demo/" + s["key"]))


def iso_z(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (t.microsecond // 1000)


def family(model):
    for f in ("opus", "sonnet", "haiku", "fable"):
        if f in model:
            return f
    return "other"


def dump(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1)


def load(name):
    with open(os.path.join(OUT, name)) as f:
        return json.load(f)


# =============================================================== prepare
def make_repo(name, branch):
    d = os.path.join(REPOS, name)
    g = ["git", "-c", "user.name=fleet-demo", "-c", "user.email=fleet-demo@example.invalid", "-C", d]
    if not os.path.isdir(os.path.join(d, ".git")):
        os.makedirs(d, exist_ok=True)
        subprocess.run(g + ["init", "-q", "-b", "main"], check=True)
        subprocess.run(g + ["remote", "add", "origin", "https://example.invalid/acme/%s.git" % name], check=True)
        subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "init"], check=True)
    if branch != "main":
        subprocess.run(g + ["checkout", "-q", "-B", branch], check=True)
    return d


def usage_records(s, rng, times, ctx0, ctx1, compact_at=None):
    """One call per timestamp: context grows from ctx0 to ctx1, mostly cache reads."""
    out = []
    n = len(times)
    for i, t in enumerate(times):
        frac = i / max(n - 1, 1)
        if compact_at is not None:
            # context builds to ~190k, compacts, rebuilds to ctx1
            if frac < compact_at:
                ctx = ctx0 + (190000 - ctx0) * (frac / compact_at)
            else:
                ctx = 22000 + (ctx1 - 22000) * ((frac - compact_at) / (1 - compact_at))
        else:
            ctx = ctx0 + (ctx1 - ctx0) * frac
        ctx = int(ctx * rng.uniform(0.97, 1.03)) if i < n - 1 else ctx1
        write = rng.randint(800, 4400)
        inp = rng.choice([3, 5, 8, 12, 20, 40]) if rng.random() > 0.04 else rng.randint(800, 3000)
        read = max(ctx - write - inp, 0)
        outp = int(rng.uniform(150, 2600) * (1.3 if "opus" in s["model_id"] else 1.0))
        out.append((t, {"input_tokens": inp, "output_tokens": outp,
                        "cache_read_input_tokens": read, "cache_creation_input_tokens": write}))
    return out


def spread(rng, start, end, n):
    span = (end - start).total_seconds()
    pts = sorted(rng.uniform(0, span) for _ in range(n))
    return [start + dt.timedelta(seconds=p) for p in pts]


def write_transcript(s, now, midnight, rng):
    sid = s["sid"]
    cwd = s["repo_dir"]
    model = s["model_id"]
    base = {"sessionId": sid, "cwd": cwd, "gitBranch": s["branch"], "version": CLI_VERSION}
    # the session's span today, clipped to after midnight
    start = max(now - dt.timedelta(minutes=s["started"]), midnight + dt.timedelta(minutes=5))
    last = now - dt.timedelta(minutes=(s["for_min"] if s["final"] in ("idle", "needs_input", "ended") else 1))
    if s["final"] == "ended":
        last = now - dt.timedelta(minutes=4)
    last = max(last, start + dt.timedelta(minutes=2))
    recs = []
    t_first = start
    if s.get("yesterday"):
        # begun yesterday evening: those calls do not count today, and its PR
        # was opened then, so it is in prs_all but not prs_today
        y0 = (midnight - dt.timedelta(days=1)).replace(hour=17, minute=10)
        t_first = y0
    recs.append(dict(base, type="ai-title", aiTitle=s["label"], timestamp=iso_z(t_first)))

    times = spread(rng, start, last, s["calls"])
    if s.get("yesterday"):
        times = spread(rng, t_first, t_first + dt.timedelta(minutes=70), 14) + times
    calls = usage_records(s, rng, times, s["ctx"][0], s["ctx"][1], s.get("compact_at"))
    asks = s["asks"]
    # prompts at the start of evenly sized stretches; the last ask is the last prompt
    per = max(len(calls) // len(asks), 1)
    pr_times = []
    for num, ago in s["prs"]:
        # a negative age: opened yesterday, during the part of the session run then
        pr_times.append((num, (t_first + dt.timedelta(minutes=35)) if ago < 0
                         else now - dt.timedelta(minutes=ago)))
    compact_done = False
    opened = set()
    for i, (t, u) in enumerate(calls):
        k = i // per
        for num, pt in pr_times:
            if pt <= t and num not in opened:
                opened.add(num)
                recs.append(dict(base, type="pr-link", prNumber=num,
                                 prUrl="https://example.invalid/acme/%s/pull/%d" % (s["repo"], num),
                                 prRepository="acme/" + s["repo"], timestamp=iso_z(t)))
        if i % per == 0 and k < len(asks):
            recs.append(dict(base, type="user", timestamp=iso_z(t - dt.timedelta(seconds=20)),
                             message={"role": "user", "content": asks[k]}))
            # pr-link is a latch: re-emitted every turn once the PR exists
            for num, pt in pr_times:
                if pt <= t:
                    recs.append(dict(base, type="pr-link", prNumber=num,
                                     prUrl="https://example.invalid/acme/%s/pull/%d" % (s["repo"], num),
                                     prRepository="acme/" + s["repo"], timestamp=iso_z(t)))
        if s.get("compact_at") and not compact_done and i >= int(len(calls) * s["compact_at"]):
            recs.append(dict(base, type="system", subtype="compact_boundary",
                             content="Conversation compacted", timestamp=iso_z(t - dt.timedelta(seconds=5)),
                             compactMetadata={"trigger": "auto", "preTokens": 190000}))
            compact_done = True
        mid = "msg_" + uuid.UUID(int=rng.getrandbits(128)).hex[:24]
        text = ("Done. " + asks[min(k, len(asks) - 1)].split(".")[0].lower()[:60] + ": next step ready.") \
            if (i + 1) % per == 0 else ""
        blocks = [[{"type": "thinking", "thinking": ""}], [{"type": "tool_use", "name": "Read", "input": {}}]]
        if text:
            blocks.append([{"type": "text", "text": text}])
        # Claude Code writes a response once per content block, same id, same usage
        for b in blocks:
            recs.append(dict(base, type="assistant", timestamp=iso_z(t),
                             message={"id": mid, "model": model, "role": "assistant",
                                      "content": b, "usage": u}))
    # latch once more after the last turn, as a live session does
    for num, pt in pr_times:
        recs.append(dict(base, type="pr-link", prNumber=num,
                         prUrl="https://example.invalid/acme/%s/pull/%d" % (s["repo"], num),
                         prRepository="acme/" + s["repo"], timestamp=iso_z(last)))
    path = os.path.join(OUT, "transcripts", sid + ".jsonl")
    with open(path, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    subs = []
    if s.get("subagent"):
        fam, n = s["subagent"]
        sub = dict(s, model_id=MODELS[fam])
        st = spread(rng, start + dt.timedelta(minutes=10), last, n)
        sd = os.path.join(OUT, "transcripts", sid, "subagents")
        os.makedirs(sd, exist_ok=True)
        with open(os.path.join(sd, "agent-a1.jsonl"), "w") as f:
            for t, u in usage_records(sub, rng, st, 9000, 26000):
                f.write(json.dumps(dict(base, type="assistant", isSidechain=True, timestamp=iso_z(t),
                                        message={"id": "msg_" + uuid.UUID(int=rng.getrandbits(128)).hex[:24],
                                                 "model": sub["model_id"], "role": "assistant",
                                                 "content": [], "usage": u})) + "\n")
                subs.append((t, u, sub["model_id"]))
    return path, [(t, u, model) for t, u in calls] + subs


def tokens_today(calls, today):
    """What the hook will count: today's calls (local date), each response once."""
    tot = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "calls": 0}
    fam = {}
    for t, u, model in calls:
        if t.astimezone(TZ).date() != today:
            continue
        k = {"input": u["input_tokens"], "output": u["output_tokens"],
             "cache_read": u["cache_read_input_tokens"], "cache_write": u["cache_creation_input_tokens"]}
        f = fam.setdefault(family(model), {"total": 0, "output": 0})
        f["total"] += sum(k.values())
        f["output"] += k["output"]
        for x in k:
            tot[x] += k[x]
        tot["calls"] += 1
    return tot, fam


# ---- history ------------------------------------------------------------------
WEEKDAY = [0, 0, 0, 0, 0, 0, 0, .04, .35, .8, .95, .9, .55, .82, .95, 1, .92, .78, .5, .28, .3, .26, .12, .03]
SATURDAY = [0, 0, 0, 0, 0, 0, 0, 0, 0, .05, .25, .35, .3, .3, .35, .3, .2, .1, 0, 0, .1, .1, 0, 0]
SUNDAY = [0] * 10 + [.08, .15, .1, 0, 0, .12, .15, .1] + [0] * 6


def day_rng(d, what):
    return random.Random("cf-demo/%s/%s" % (d.isoformat(), what))


def day_traits(d):
    r = day_rng(d, "day")
    return {"f": r.uniform(0.72, 1.18), "evening": r.random() < 0.35, "overnight": r.random() < 0.3,
            "opus": r.uniform(0.55, 0.7), "haiku": r.uniform(0.03, 0.07),
            "hit": r.uniform(93.0, 96.8), "rtkshare": r.uniform(0.75, 1.3)}


def activity(t):
    """0..1, how busy the hour starting at local time t was."""
    d = t.date()
    prof = SATURDAY if d.weekday() == 5 else SUNDAY if d.weekday() == 6 else WEEKDAY
    a = prof[t.hour]
    tr = day_traits(d)
    if tr["evening"] and 19 <= t.hour <= 22 and d.weekday() < 5:
        a = max(a, day_rng(d, "ev%d" % t.hour).uniform(0.35, 0.6))
    if a == 0:
        return 0.0
    return min(1.0, a * tr["f"] * day_rng(d, "h%d" % t.hour).uniform(0.85, 1.15))


def hours_between(a, b):
    t = a
    while t <= b:
        yield t
        t = (t + dt.timedelta(hours=1, minutes=5)).replace(minute=0, second=0, microsecond=0)
        # a DST change makes local hours uneven; re-anchor on the wall clock
        t = t.astimezone(TZ)


def build_history(now, live):
    """Hourly statistics for every chart entity, HISTORY_DAYS days back to the current hour."""
    today = now.date()
    cur_hour = now.replace(minute=0, second=0, microsecond=0)
    start = (now - dt.timedelta(days=HISTORY_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)
    hours = list(hours_between(start, cur_hour))
    stats = {k: [] for k in ENTITIES}

    def row(eid, t, lo, hi, mean=None):
        stats[eid].append({"start": t.isoformat(), "min": round(lo, 2), "max": round(hi, 2),
                           "mean": round((lo + hi) / 2 if mean is None else mean, 2)})

    # -- today's shape, scaled so the last hour lands on the live figures
    tod = [h for h in hours if h.date() == today]
    tod_act = [max(activity(h), 0.02 if h.hour >= 7 else 0) for h in tod]
    tod_act[-1] = max(tod_act[-1], 0.3)   # the current hour is busy: five sessions are open
    tod_sum = sum(tod_act) or 1.0

    daily = {}   # date -> totals, for the records and the weekly bars
    by_day = {}
    for h in hours:
        by_day.setdefault(h.date(), []).append(h)

    for d, hs in sorted(by_day.items()):
        tr = day_traits(d)
        is_today = d == today
        acts = tod_act if is_today else [activity(h) for h in hs]
        total_day = sum(acts) * 9.4e6
        fams = {"opus": tr["opus"], "haiku": tr["haiku"], "sonnet": 1 - tr["opus"] - tr["haiku"]}
        # output as a share of each family's tokens, as in the live transcripts
        outr = {"opus": .0145, "sonnet": .0155, "haiku": .05}
        cum = {f: 0.0 for f in ("opus", "sonnet", "haiku", "fable", "other")}
        ocum = dict(cum)
        rtk_cum = 0.0
        rtk_day = sum(acts) * 30000 * tr["f"]
        first_active = None
        running_max = 0
        for i, (h, a) in enumerate(zip(hs, acts)):
            r = day_rng(d, "r%d" % h.hour)
            # -- sessions running (hourly max)
            if a < 0.04:
                mx = 1 if (tr["overnight"] and (h.hour >= 21 or h.hour < 8)) else 0
            else:
                mx = int(round(1 + a * 5.6 + r.choice([-1, 0, 0, 0, 1])))
                mx = max(1, min(mx, 8))
            if is_today and h == cur_hour:
                mx = live["running"]
            elif is_today and h == cur_hour - dt.timedelta(hours=1):
                mx = max(3, min(mx, live["running"] + 1))
            lo = max(0, mx - r.randint(0, 2)) if mx else 0
            row("sensor.claude_sessions_running", h, lo, mx, (lo + mx) / 2)
            running_max = max(running_max, mx)
            if not a:
                continue
            first_active = first_active or h
            # -- tokens by family (a counter that restarts at midnight)
            for f in ("opus", "sonnet", "haiku"):
                if is_today:
                    inc = live["fam"].get(f, {}).get("total", 0) * a / tod_sum
                    oinc = live["fam"].get(f, {}).get("output", 0) * a / tod_sum
                else:
                    inc = total_day * fams[f] * a / (sum(acts) or 1)
                    oinc = inc * outr[f] * r.uniform(0.85, 1.15)
                lo_, lo_o = cum[f], ocum[f]
                cum[f] += inc
                ocum[f] += oinc
                row("sensor.claude_tokens_today_" + f, h, lo_, cum[f])
                row("sensor.claude_output_tokens_today_" + f, h, lo_o, ocum[f])
            for f in ("fable", "other"):
                row("sensor.claude_tokens_today_" + f, h, 0, 0)
                row("sensor.claude_output_tokens_today_" + f, h, 0, 0)
            # -- cache hit, the day's running figure: low while context is rebuilt
            since = (h - first_active).total_seconds() / 3600
            hit = tr["hit"] - 3.5 * math.exp(-since / 1.3) + r.uniform(-0.4, 0.4)
            if is_today:
                w = min(1.0, i / max(len(hs) - 1, 1)) ** 3
                hit = hit * (1 - w) + live["hit"] * w
                if h == cur_hour:
                    hit = live["hit"]
            row("sensor.claude_cache_hit_today", h, hit - 0.3, hit + 0.3, hit)
            # -- RTK: kept out today (a counter) and its share of new context
            lo_ = rtk_cum
            rtk_cum += (live["rtk_saved"] if is_today else rtk_day) * a / ((tod_sum if is_today else sum(acts)) or 1)
            row("sensor.claude_rtk_saved_today", h, lo_, rtk_cum)
            share = live["rtk_share"] * tr["rtkshare"] + r.uniform(-1.5, 1.5)
            if is_today:
                share = live["rtk_share"] + (r.uniform(-2, 2) if h != cur_hour else 0)
            row("sensor.claude_rtk_share_today", h, share - 0.5, share + 0.5, share)
        daily[d.isoformat()] = {"tokens": int(sum(cum.values())), "running": running_max,
                                "rtk": int(rtk_cum), "rtk_target": rtk_day, "weekday": d.weekday()}

    plan = build_plan_history(now, hours, row)
    return stats, daily, plan


def build_plan_history(now, hours, row):
    """Session (5-hour) and weekly pool usage, hourly, ending on the live template's values."""
    s_anchor = now - dt.timedelta(minutes=SESSION_ELAPSED_MIN)
    w_anchor = now - dt.timedelta(hours=WEEK_ELAPSED_H)
    s_now = session_usage_at(now, s_anchor)
    # ---- weekly: points in proportion to activity, each week scaled to its total
    week = dt.timedelta(days=7)
    k0 = math.floor((hours[0] - w_anchor) / week)
    # each earlier week peaks somewhere different; the current one ends on the
    # live value. Keyed by weeks before the current one.
    wk_targets = {0: WEEKLY_NOW, -1: 84, -2: 61, -3: 73, -4: 90, -5: 68}
    wval = {}
    for k in range(k0, 1):
        ws = w_anchor + k * week
        we = min(ws + week, now)
        hs = list(hours_between(ws.astimezone(TZ).replace(minute=0, second=0, microsecond=0), we))
        acts = [activity(h) for h in hs]
        if k == 0:
            acts[-1] = max(acts[-1], 0.3)
        tot = sum(acts) or 1
        target = wk_targets.get(k, 75)
        c = 0.0
        for h, a in zip(hs, acts):
            lo = c
            c += target * a / tot
            wval[h] = (lo if h >= ws else 0.0, c)
    # ---- session: a 5-hour window opens at the first busy hour after the last one ends
    sval = {}
    r = random.Random("sess")
    wstart, use = None, 0.0
    s_hour = s_anchor.replace(minute=0, second=0, microsecond=0)
    for h in hours:
        if h >= s_hour:
            lo = session_usage_at(max(h, s_anchor), s_anchor) if h > s_hour else 0
            hi = session_usage_at(min(h + dt.timedelta(hours=1), now), s_anchor)
            sval[h] = (lo, hi)
            continue
        a = activity(h)
        if wstart is not None and h >= wstart + dt.timedelta(hours=5):
            wstart, use = None, 0.0
        if wstart is None and a > 0.05:
            wstart, use = h, 0.0
        lo = use
        if wstart is not None:
            use = min(100.0, use + a * 17.5 * r.uniform(0.8, 1.2))
        sval[h] = (lo, use)
    for h in hours:
        lo, hi = sval[h]
        row("sensor.claude_plan_session_usage", h, round(lo), round(hi))
        if h in wval:
            lo, hi = wval[h]
            row("sensor.claude_plan_weekly_usage", h, round(lo), round(hi))
    # weekly points used per day, as sensor.claude_weekly_points_by_day keeps them
    days = {}
    prev = None
    for h in hours:
        if h not in wval:
            continue
        v = round(wval[h][1])
        if prev is not None and v > prev:
            k = h.date().isoformat()
            days[k] = round(days.get(k, 0) + v - prev, 1)
        prev = v
    return {"s_anchor": s_anchor.isoformat(), "w_anchor": w_anchor.isoformat(),
            "session_now": s_now, "weekly_now": WEEKLY_NOW, "weekly_days": days,
            "session_peak_today": max(hi for h, (lo, hi) in sval.items() if h.date() == now.date())}


# The live plan source: session usage climbs 0.19 points a minute from 8% at the
# start of each 5-hour window; the window began SESSION_ELAPSED_MIN before
# `prepare`, so the gauge reads 40% then and projects ~65% at its reset: green.
# The week is WEEK_ELAPSED_H hours in (4 days 4 hours, reset in 2 days 20 hours),
# so 60% of it has gone, at WEEKLY_NOW %: 6 points over pace, the middle of
# sensor.claude_weekly_pace's yellow band (3-9 over), so the weekly gauge is
# yellow and "At this rate" says "6 over pace". The template keeps the same
# rate, so it stays 6-7 over for the next few hours.
SESSION_ELAPSED_MIN = 170
SESSION_RATE = 0.19
WEEK_ELAPSED_H = 100
WEEKLY_NOW = 66


def session_usage_at(t, anchor):
    m = ((t - anchor).total_seconds() / 60) % 300
    return min(100, round(8 + SESSION_RATE * m))


ENTITIES = {
    "sensor.claude_sessions_running": "sessions",
    "sensor.claude_plan_session_usage": "%",
    "sensor.claude_plan_weekly_usage": "%",
    "sensor.claude_cache_hit_today": "%",
    "sensor.claude_rtk_saved_today": "tokens",
    "sensor.claude_rtk_share_today": "%",
}
for _f in ("opus", "sonnet", "haiku", "fable", "other"):
    ENTITIES["sensor.claude_tokens_today_" + _f] = "tokens"
    ENTITIES["sensor.claude_output_tokens_today_" + _f] = "tokens"


# ---- RTK ledgers ------------------------------------------------------------------
RTK_CMDS = [  # rtk_cmd, raw output tokens (lo, hi), share of it RTK passes on
    ("rtk git status", (200, 2400), (0.15, 0.3)),
    ("rtk git diff", (1500, 26000), (0.2, 0.4)),
    ("rtk git log -n 20", (900, 6000), (0.1, 0.2)),
    ("rtk pytest -q", (3000, 48000), (0.03, 0.1)),
    ("rtk npm test", (4000, 60000), (0.04, 0.12)),
    ("rtk cargo test", (3000, 40000), (0.03, 0.1)),
    ("rtk grep -rn", (800, 180000), (0.1, 0.3)),
    ("rtk ls -la", (150, 1800), (0.3, 0.5)),
    ("rtk proxy gh pr view", (300, 2000), (1.0, 1.0)),
]
CAP = 30000 // 4


def write_ledger(home, machine, share, daily_rtk, now):
    """RTK's history.db for one fake home: commands whose capped savings add up
    to this computer's share of each day's invented total."""
    d = os.path.join(home, "Library", "Application Support", "rtk")
    os.makedirs(d, exist_ok=True)
    db = os.path.join(d, "history.db")
    if os.path.exists(db):
        os.remove(db)
    c = sqlite3.connect(db)
    c.execute("""create table commands (id integer primary key, timestamp text not null,
                 original_cmd text, rtk_cmd text, project_path text, input_tokens integer,
                 output_tokens integer, saved_tokens integer, savings_pct real, exec_time_ms integer)""")
    today_saved = 0
    for day, v in sorted(daily_rtk.items()):
        dd = dt.date.fromisoformat(day)
        if (now.date() - dd).days > 7:
            continue
        target = v * share
        r = random.Random("ledger/%s/%s" % (machine, day))
        got = 0
        rows = []
        while got < target:
            cmd, (lo, hi), (plo, phi) = r.choice(RTK_CMDS)
            raw = int(r.uniform(lo, hi) if r.random() > 0.1 else r.uniform(lo, lo * 3))
            out = int(raw * r.uniform(plo, phi))
            eff = max(0, min(raw, CAP) - min(out, CAP))
            got += eff
            hour = r.choice([9, 10, 10, 11, 11, 13, 14, 14, 15, 15, 16, 17])
            t = dt.datetime.combine(dd, dt.time(hour, r.randint(0, 59), r.randint(0, 59)), TZ)
            if t > now - dt.timedelta(minutes=2):
                t = now - dt.timedelta(minutes=r.randint(2, 120))
                if t.date() != dd:
                    continue
            rows.append((t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00"),
                         cmd.replace("rtk proxy ", "").replace("rtk ", ""), cmd, "/work/" + machine,
                         raw, out, raw - out, round(100 * (raw - out) / raw, 1) if raw else 0,
                         r.randint(40, 9000)))
            if dd == now.date():
                today_saved += eff
        c.executemany("insert into commands (timestamp, original_cmd, rtk_cmd, project_path, input_tokens,"
                      " output_tokens, saved_tokens, savings_pct, exec_time_ms) values (?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    return today_saved


def cmd_prepare():
    now = dt.datetime.now(TZ).replace(microsecond=0)
    midnight = now.replace(hour=0, minute=0, second=0)
    today = now.date()
    for d in ("transcripts",):
        os.makedirs(os.path.join(OUT, d), exist_ok=True)
    os.makedirs(BIN, exist_ok=True)
    rng = random.Random("cf-demo-sessions")
    plan = []
    live_fam = {}
    live = {"running": 0}
    by_machine = {}
    for s in SESSIONS:
        s = dict(s)
        s["sid"] = sid_of(s)
        s["model_id"] = MODELS[s["model"]]
        s["repo_dir"] = make_repo(s["repo"], s["branch"])
        path, calls = write_transcript(s, now, midnight, rng)
        tot, fam = tokens_today(calls, today)
        for f, v in fam.items():
            lf = live_fam.setdefault(f, {"total": 0, "output": 0})
            lf["total"] += v["total"]
            lf["output"] += v["output"]
        bm = by_machine.setdefault(s["machine"], {"new": 0})
        bm["new"] += tot["cache_write"] + tot["input"]
        if s["final"] != "ended":
            live["running"] += 1
        plan.append({"sid": s["sid"], "key": s["key"], "machine": s["machine"],
                     "home": os.path.join(HOMES, s["machine"]), "cwd": s["repo_dir"],
                     "transcript": path, "final": s["final"], "stale": bool(s.get("stale")),
                     "for_min": s["for_min"], "prompts": s["prompts"], "wait_min": s["wait_min"],
                     "last_prompt": s["asks"][-1], "label": s["label"], "tokens": tot,
                     "edit": os.path.join(s["repo_dir"], "src", "index.ts")})
    live["fam"] = live_fam
    tot_all = {"cache_read": 0, "in": 0}
    for p in plan:
        k = p["tokens"]
        tot_all["cache_read"] += k["cache_read"]
        tot_all["in"] += k["cache_read"] + k["cache_write"] + k["input"]
    live["hit"] = round(100 * tot_all["cache_read"] / tot_all["in"], 1)

    # the fake claude's answers: the session's last prompt is in the excerpt it is shown
    with open(os.path.join(BIN, "summaries.tsv"), "w") as f:
        for s in SESSIONS:
            f.write("%s\t%s\n" % (s["asks"][-1], s["summary"]))
    with open(os.path.join(BIN, "version"), "w") as f:
        f.write(CLI_VERSION + "\n")

    # RTK needs the history's daily totals first; live figures then come from the ledgers
    live["rtk_saved"] = 1.0
    live["rtk_share"] = 20.0
    _, daily, _ = build_history(now, live)
    daily_rtk = {d: v["rtk_target"] for d, v in daily.items()}
    saved = 0
    for m, cfg in MACHINES.items():
        if cfg["rtk"] == "on":
            saved += write_ledger(os.path.join(HOMES, m), m, cfg["rtk_share"], daily_rtk, now)
    new = sum(v["new"] for m, v in by_machine.items() if MACHINES[m]["rtk"] == "on")
    live["rtk_saved"] = saved
    live["rtk_share"] = round(100 * saved / (saved + new), 1) if saved + new else 0
    stats, daily, planh = build_history(now, live)

    dump(os.path.join(OUT, "sessions.json"), plan)
    dump(os.path.join(OUT, "stats.json"), stats)
    dump(os.path.join(OUT, "live.json"), dict(live, prepared=now.isoformat()))
    dump(os.path.join(OUT, "plan.json"), planh)
    dump(os.path.join(OUT, "restore.json"), restore_entries(now, daily, planh, stats,
                                                          sum(p["tokens"]["output"] for p in plan)))
    print("  ok    %d sessions, %.1fM tokens today, cache hit %s%%, RTK kept out %dk (%s%% of new context)"
          % (len(plan), sum(v["total"] for v in live_fam.values()) / 1e6, live["hit"],
             saved // 1000, live["rtk_share"]))


# ---- restore state: records and the other day-scoped trigger sensors -------------
def restore_entries(now, daily, planh, stats, today_output):
    today = now.date().isoformat()
    past = {d: v for d, v in daily.items() if d != today}

    def at(day, hh, mm):
        return dt.datetime.combine(dt.date.fromisoformat(day), dt.time(hh, mm), TZ).isoformat()

    busiest = max(past, key=lambda d: past[d]["tokens"])
    crowded = max(past, key=lambda d: (past[d]["running"], past[d]["tokens"]))
    weekdays = sorted(d for d, v in past.items() if v["weekday"] < 5)
    r = random.Random("records")
    pick = lambda: weekdays[r.randrange(len(weekdays))]
    d_prs, d_solo, d_wait = pick(), pick(), pick()
    records = {
        "concurrent": {"value": past[crowded]["running"], "label": "", "id": crowded, "at": at(crowded, 15, 42)},
        "prs_day": {"value": 6, "label": "#196, #199, #201, #1017, #1020, #52", "id": d_prs, "at": at(d_prs, 17, 58)},
        "prompts_day": {"value": 164, "label": "", "id": busiest, "at": at(busiest, 18, 31)},
        "tokens_day": {"value": past[busiest]["tokens"], "label": "", "id": busiest, "at": at(busiest, 23, 58)},
        # below today's output on purpose: the check asserts that today overtakes
        # it with exactly the sessions' output, which proves the record is fed
        "output_day": {"value": int(today_output * 0.8), "label": "", "id": busiest, "at": at(busiest, 23, 58)},
        "solo_run": {"value": 163, "label": "Port the CSV importer to streaming",
                     "id": "x@" + d_solo, "at": at(d_solo, 14, 5)},
        "longest_wait": {"value": 71, "label": "Review the schema migration plan",
                         "id": "y@" + d_wait, "at": at(d_wait, 12, 50)},
    }
    run_today = [x for x in stats["sensor.claude_sessions_running"] if x["start"][:10] == today]
    peak = max(run_today, key=lambda x: x["max"])
    peak_at = dt.datetime.fromisoformat(peak["start"]) + dt.timedelta(minutes=random.Random("pk").randint(5, 50))
    return [
        {"entity_id": "sensor.claude_fleet_records", "state": "0", "attributes":
            {"records": records, "last_broken": None, "icon": "mdi:trophy-outline",
             "friendly_name": "Claude fleet records"}},
        {"entity_id": "sensor.claude_peak_today", "state": str(int(peak["max"])), "attributes":
            {"day": today, "at": peak_at.strftime("%-I:%M %p"), "unit_of_measurement": "sessions",
             "icon": "mdi:chart-bell-curve-cumulative", "friendly_name": "Claude peak today"}},
        {"entity_id": "sensor.claude_weekly_points_by_day", "state": str(planh["weekly_days"].get(today, 0)),
         "attributes": {"mark": planh["weekly_now"],
                        "days": {k: v for k, v in planh["weekly_days"].items()
                                 if (now.date() - dt.date.fromisoformat(k)).days <= 7},
                        "unit_of_measurement": "points", "icon": "mdi:calendar-today",
                        "friendly_name": "Claude weekly points by day"}},
        {"entity_id": "sensor.claude_plan_session_peak_today", "state": str(planh["session_peak_today"]),
         "attributes": {"day": today, "unit_of_measurement": "%", "icon": "mdi:chart-bell-curve",
                        "friendly_name": "Claude plan session peak today"}},
    ]


def cmd_restore():
    path = os.path.join(DIR, "config", ".storage", "core.restore_state")
    with open(path) as f:
        rs = json.load(f)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    want = {e["entity_id"]: e for e in load("restore.json")}
    data = [x for x in rs["data"] if x["state"]["entity_id"] not in want]
    for eid, e in want.items():
        num = e["state"]
        try:
            native = int(num)
        except ValueError:
            native = float(num)
        unit = e["attributes"].get("unit_of_measurement")
        data.append({"state": {"entity_id": eid, "state": num, "attributes": e["attributes"],
                               "last_changed": now, "last_reported": now, "last_updated": now,
                               "context": {"id": uuid.uuid4().hex[:26].upper(), "parent_id": None, "user_id": None}},
                     "extra_data": {"native_value": native, "native_unit_of_measurement": unit},
                     "last_seen": now})
    # The burn rate's own readings: ten minutes of the live template's values,
    # one a minute, so the rate (which needs five) is known on HA's first minute
    # tick rather than five minutes after the restart.
    p = load("plan.json")
    anchor = dt.datetime.fromisoformat(p["s_anchor"])
    t_now = dt.datetime.now(TZ).replace(microsecond=0)
    readings = [[int((t_now - dt.timedelta(minutes=m)).timestamp()),
                 session_usage_at(t_now - dt.timedelta(minutes=m), anchor)] for m in range(10, 0, -1)]
    rate = round((readings[-1][1] - readings[0][1]) / 9, 4)
    data = [x for x in data if x["state"]["entity_id"] != "sensor.claude_plan_usage_rate"]
    data.append({"state": {"entity_id": "sensor.claude_plan_usage_rate", "state": str(rate),
                           "attributes": {"readings": readings, "state_class": "measurement",
                                          "unit_of_measurement": "%/min", "icon": "mdi:fire",
                                          "friendly_name": "Claude plan usage rate"},
                           "last_changed": now, "last_reported": now, "last_updated": now,
                           "context": {"id": uuid.uuid4().hex[:26].upper(), "parent_id": None, "user_id": None}},
                 "extra_data": {"native_value": rate, "native_unit_of_measurement": "%/min"},
                 "last_seen": now})
    rs["data"] = data
    with open(path, "w") as f:
        json.dump(rs, f)
    print("  ok    restore state seeded: records, peak today, weekly points by day, burn-rate readings")


# ---- recorded history ---------------------------------------------------------
# The history explorer card draws recorded STATES wherever it finds any, and falls
# back to long-term statistics only in some loading orders; a day with no states
# at all can stay blank even with statistics under it (seen with v1.0.51). A
# real install has ten days of states (the recorder's default) and statistics
# behind them, so the demo gets the same: the invented history as states too.
COUNTERS = [e for e in ENTITIES if e.startswith(("sensor.claude_tokens_today", "sensor.claude_output_tokens",
                                                 "sensor.claude_rtk_saved"))]


def fnv1a_32(data):
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def attributes_id(db, eid):
    """The attributes a real state of this sensor carries. Without the unit, the
    recorder sees it change to None and stops compiling statistics for it."""
    unit = ENTITIES[eid]
    name = eid.split(".", 1)[1].replace("_", " ").capitalize()
    shared = json.dumps({"state_class": "measurement", "unit_of_measurement": unit,
                         "friendly_name": name}, separators=(",", ":"))
    h = fnv1a_32(shared.encode())
    row = db.execute("select attributes_id from state_attributes where hash = ? and shared_attrs = ?",
                     (h, shared)).fetchone()
    if row:
        return row[0]
    return db.execute("insert into state_attributes (hash, shared_attrs) values (?, ?)", (h, shared)).lastrowid


def cmd_inject():
    """HA must be STOPPED. Replaces the recorded states of every chart entity with
    the invented history, two points an hour. Daily counters get none for today:
    the live figure arrives when HA starts, and today's bar is then exactly that
    rise, with no invented morning to count twice."""
    live = load("live.json")
    prepared = dt.datetime.fromisoformat(live["prepared"])
    stats = load("stats.json")
    db = sqlite3.connect(os.path.join(DIR, "config", "home-assistant_v2.db"))
    added = 0
    for eid in stats:
        row = db.execute("select metadata_id from states_meta where entity_id = ?", (eid,)).fetchone()
        if row is None:
            mid = db.execute("insert into states_meta (entity_id) values (?)", (eid,)).lastrowid
        else:
            mid = row[0]
            db.execute("update states set old_state_id = null where old_state_id in "
                       "(select state_id from states where metadata_id = ?)", (mid,))
            db.execute("delete from states where metadata_id = ?", (mid,))
        counter = eid in COUNTERS
        aid = attributes_id(db, eid)
        pts = []
        days = set()
        for r in stats[eid]:
            t = dt.datetime.fromisoformat(r["start"])
            if counter and t.date() == prepared.date():
                continue
            days.add(t.date())
            pts.append((t + dt.timedelta(minutes=1), r["min"]))
            pts.append((t + dt.timedelta(minutes=55 if counter else 40), r["max"]))
        if counter:   # back to zero at every midnight, as the real sensors do,
            # today included: today's bar is the rise from there to the live figure
            days.add(prepared.date())
            pts += [(dt.datetime.combine(d, dt.time(0, 0, 30), TZ), 0) for d in days]
            pts.sort()
        prev = None
        for t, v in pts:
            if t >= prepared - dt.timedelta(minutes=1):
                continue
            ts = t.timestamp()
            prev = db.execute("insert into states (state, last_updated_ts, last_reported_ts, old_state_id,"
                              " attributes_id, origin_idx, context_id_bin, metadata_id) values (?,?,?,?,?,0,?,?)",
                              (("%g" % v), ts, ts, prev, aid, uuid.uuid4().bytes, mid)).lastrowid
            added += 1
    db.commit()
    db.close()
    print("  ok    invented history written to the recorder: %d states" % added)


# =============================================================== HA side
def cmd_import_stats():
    import ha
    stats = load("stats.json")
    today = dt.datetime.fromisoformat(load("live.json")["prepared"]).date().isoformat()
    msgs = []
    for eid, rows in stats.items():
        # Not today: HA compiles each hour it was running for itself, from the
        # recorded states, and an imported row for the same hour makes that
        # compile fail (UNIQUE constraint on statistics). Today is drawn from
        # recorded states anyway.
        rows = [r for r in rows if not r["start"].startswith(today)]
        if not rows:
            continue
        msgs.append({"type": "recorder/import_statistics",
                     "metadata": {"has_sum": False, "mean_type": 1, "name": None, "source": "recorder",
                                  "statistic_id": eid, "unit_class": None,
                                  "unit_of_measurement": ENTITIES[eid]},
                     "stats": rows})
    ha.ws(msgs)
    n = sum(len(m["stats"]) for m in msgs)
    print("  ok    imported %d hourly statistics over %d entities" % (n, len(msgs)))


def cmd_plan():
    """Four template helpers, retitled "clawdmeter": the package finds a plan source
    with integration_entities('clawdmeter'), which matches a config entry's TITLE
    before it matches a domain. A YAML template sensor has no config entry, so it
    could never be found that way."""
    import ha
    tok = ha.token()
    p = load("plan.json")
    st, entries = ha.http("GET", "/api/config/config_entries/entry", token=tok)
    for e in entries:
        if e["title"] == "clawdmeter" or (e["domain"] == "template" and e["title"].startswith("Demo plan")):
            ha.http("DELETE", "/api/config/config_entries/entry/" + e["entry_id"], token=tok)
    sa, wa = p["s_anchor"], p["w_anchor"]
    helpers = [
        ("Demo plan session usage", "%", None, "measurement",
         "{%% set m = ((as_timestamp(now()) - as_timestamp('%s')) / 60) %% 300 %%}"
         "{{ [8 + %s * m, 100] | min | round(0) | int }}" % (sa, SESSION_RATE)),
        ("Demo plan session reset", None, "timestamp", None,
         "{%% set a = as_timestamp('%s') %%}{%% set n = ((as_timestamp(now()) - a) // 18000) + 1 %%}"
         "{{ (a + n * 18000) | as_datetime }}" % sa),
        ("Demo plan weekly usage", "%", None, "measurement",
         "{%% set h = ((as_timestamp(now()) - as_timestamp('%s')) / 3600) %% 168 %%}"
         "{{ [%s * h / %s, 100] | min | round(0) | int }}" % (wa, WEEKLY_NOW, WEEK_ELAPSED_H)),
        ("Demo plan weekly reset", None, "timestamp", None,
         "{%% set a = as_timestamp('%s') %%}{%% set n = ((as_timestamp(now()) - a) // 604800) + 1 %%}"
         "{{ (a + n * 604800) | as_datetime }}" % wa),
    ]
    for name, unit, dc, sc, tpl in helpers:
        st, r = ha.http("POST", "/api/config/config_entries/flow", {"handler": "template"}, token=tok)
        st, r = ha.http("POST", "/api/config/config_entries/flow/" + r["flow_id"], {"next_step_id": "sensor"}, token=tok)
        body = {"name": name, "state": tpl}
        if unit:
            body["unit_of_measurement"] = unit
        if dc:
            body["device_class"] = dc
        if sc:
            body["state_class"] = sc
        st, r = ha.http("POST", "/api/config/config_entries/flow/" + r["flow_id"], body, token=tok)
        if r.get("type") != "create_entry":
            ha.fail("template helper %s: %s" % (name, r))
        ha.ws([{"type": "config_entries/update", "entry_id": r["result"]["entry_id"], "title": "clawdmeter"}])
    print("  ok    plan source: 4 template helpers titled 'clawdmeter' (session %s%%, weekly %s%%)"
          % (p["session_now"], p["weekly_now"]))


# =============================================================== sessions
def ev(p, event, extra=None):
    env = dict(os.environ, FAKE_HOME=p["home"], CLAUDE_HA_SYNC="1")
    cmd = [os.path.join(HERE, "..", "ev.sh"), event, p["sid"], p["cwd"], p["transcript"],
           json.dumps(extra or {})]
    subprocess.run(cmd, env=env, check=True, cwd=p["cwd"])


def patch_state(p, state, since_min, **kw):
    sf = os.path.join(p["home"], ".claude", "ha-status", p["sid"] + ".json")
    with open(sf) as f:
        s = json.load(f)
    now = int(time.time())
    s.update(state=state, state_since=now - since_min * 60, last_pub=now - 90,
             prompts_today=p["prompts"], wait_today=p["wait_min"] * 60, **kw)
    with open(sf, "w") as f:
        json.dump(s, f)


def cmd_run():
    """`run` plays every session but the ended one; `run ended` plays that one
    alone, last, so it is still inside the cleanup's 10-minute grace when the
    screenshots are taken."""
    only_ended = sys.argv[2:] == ["ended"]
    plan = [p for p in load("sessions.json") if (p["final"] == "ended") == only_ended]
    stale_done = None
    for p in plan:
        ev(p, "SessionStart", {"source": "startup"})
        ev(p, "UserPromptSubmit", {"prompt": p["last_prompt"]})
        ev(p, "Stop", {"stop_hook_active": False})       # tokens, and the summary
        # The day so far: how long it has been in its state, prompts, waiting.
        # These are the hook's own counters, seeded; the state change itself is
        # always a real hook event.
        if p["final"] in ("working",):
            patch_state(p, "working", p["for_min"])
            ev(p, "PostToolUse", {"tool_name": "Edit", "tool_input": {"file_path": p["edit"]},
                                  "tool_response": {"success": True}})
        elif p["final"] == "needs_input":
            patch_state(p, "needs_input", p["for_min"])
            ev(p, "PreToolUse", {"tool_name": "AskUserQuestion", "tool_input": {"questions": [
                {"question": "Which retry schedule should failed deliveries use?", "header": "Retries",
                 "options": [{"label": "Exponential, 6 tries"}, {"label": "Fixed, every 10 min"}],
                 "multiSelect": False}]}})
        elif p["final"] == "idle":
            patch_state(p, "idle", p["for_min"])
            ev(p, "Stop", {"stop_hook_active": False})
        elif p["final"] == "ended":
            patch_state(p, "idle", p["for_min"])
            ev(p, "Stop", {"stop_hook_active": False})
            ev(p, "SessionEnd", {"reason": "prompt_input_exit"})
        if p["stale"]:
            stale_done = time.time()
        print("  ok    %-8s %-12s %s" % (p["machine"], p["final"] + (" (stale soon)" if p["stale"] else ""), p["label"]))
    if stale_done:
        with open(os.path.join(OUT, "stale_since"), "w") as f:
            f.write(str(int(stale_done)))


def cmd_check():
    import ha
    st, states = ha.http("GET", "/api/states", token=ha.token())
    ss = [s for s in states if s["attributes"].get("claude_session")]
    for s in sorted(ss, key=lambda s: s["attributes"].get("machine", "")):
        a = s["attributes"]
        k = a.get("tokens_today") or {}
        print("  %-7s %-12s %-44s %-26s ctx %6s  prs %s  summary: %s" % (
            a.get("machine"), s["state"], a.get("label")[:44], a.get("model"),
            a.get("context_tokens"), a.get("prs_today"), (a.get("summary") or "-")[:40]))
    return ss


def main():
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = {"prepare": cmd_prepare, "restore": cmd_restore, "import-stats": cmd_import_stats,
          "plan": cmd_plan, "run": cmd_run, "check": cmd_check, "inject": cmd_inject}.get(c)
    if not fn:
        print(__doc__)
        sys.exit(2)
    fn()


if __name__ == "__main__":
    main()
