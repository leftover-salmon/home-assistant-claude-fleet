/* Claude Fleet cards: the flow card, and below it the timeline and projects
 * cards, which load with it (one file, one extra_module_url line).
 *
 * The flow card.
 *
 * A stream that runs with the fleet's token rate: still water at zero, a slow
 * current when one session is writing, a fast, full, bright one when many are.
 * One number becomes speed, depth, colour and the count of drops in the water.
 *
 * Loaded by `frontend: extra_module_url: [/local/claude-fleet-flow-card.js?v=N]`
 * in configuration.yaml. Browsers keep this file for a long time: when it
 * changes, FLOW_VERSION below goes up and so does the N in that line (the
 * README carries the current one), or people go on seeing the old card.
 *
 *   type: custom:claude-fleet-flow-card
 *   entity: sensor.claude_output_token_rate
 *   name: Output tokens a minute        (optional)
 *   max: 30000                          (optional; the rate that reads as full)
 *
 * The scale is logarithmic. Real rates sit near zero most of the time and reach
 * tens of thousands in bursts; drawn in proportion, the stream would be flat
 * almost always. A plain log scale errs the other way: 150 a minute, one
 * session thinking aloud, drew a river half full. So the log is raised to a
 * power (CURVE), which keeps the low end low: with the default max, 150 a
 * minute is a trickle, 2,500 flows, 10,000 runs fast and 30,000 floods.
 *
 * Tributaries. Every computer's hook publishes what it wrote today (a sensor
 * carrying the attribute `claude_machine_tokens`), and the card finds those by
 * that attribute: nothing to list, and a new computer simply appears. Each
 * gets a stream of its own on the left, as wide and as fast as that computer
 * is writing, and they run together into the river. A computer's rate is
 * worked out here, from its counter over the last five minutes (the recorder
 * is asked for those minutes once, so the streams are right on a fresh page),
 * because Home Assistant cannot hold a rate sensor for a computer it has not
 * met. With no such sensors, or on a card too narrow to fit them, there is
 * only the river.
 *
 * A page that was asleep (a background tab, a closed laptop) heard nothing
 * while it slept, so its last reading is from before it went to sleep, and
 * everything written in between would land in the five minutes after it woke:
 * a stream in flood that was a trickle all along. So a page that has heard
 * nothing from Home Assistant for ASLEEP_S forgets its readings and asks the
 * recorder again, as a fresh page does. A live page hears something every few
 * seconds, and asking again costs nothing but one recorder query.
 *
 * No dependencies and nothing fetched: one file, drawn on a canvas. It stops
 * drawing when the card is off screen or the tab is hidden, and draws a still
 * picture for anyone who has asked their system for reduced motion.
 */
const FLOW_VERSION = "4";
const WINDOW_S = 330;         // a computer's rate is read over this long
const ASLEEP_S = 90;          // no word from Home Assistant this long: the page was asleep
const MIN_TRIB_WIDTH = 360;   // narrower than this, the river alone
const CURVE = 2.2;

class ClaudeFleetFlowCard extends HTMLElement {
  static getStubConfig() {
    return { entity: "sensor.claude_output_token_rate" };
  }

  setConfig(config) {
    if (!config || !config.entity) throw new Error("claude-fleet-flow-card needs an entity");
    this._config = { name: "Output tokens a minute", max: 30000, ...config };
    this._level = 0;      // what is drawn, 0..1; eases toward the target
    this._target = 0;     // where the rate says it should be
    this._phase = 0;      // how far the water has travelled
    this._drops = [];
    this._machines = new Map();   // name -> {readings, rate, level, drops}
    if (!this.shadowRoot) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    const st = hass.states[this._config.entity];
    const rate = st ? Number(st.state) : NaN;
    this._known = Number.isFinite(rate);
    this._rate = this._known ? Math.max(0, rate) : 0;
    this._readMachines(hass);
    this._show();
    if (this._still) this._wake();   // no animation running: draw the new reading once
  }

  /* The river carries at least what its streams bring. The fleet's sensor and
   * the streams are counted apart (the sensor in Home Assistant, each stream
   * here), and for a few minutes after a restart the sensor reads low while it
   * gathers readings again: three streams in flood pouring into still water.
   * The sensor still counts for computers on a hook too old to have a stream. */
  _show() {
    let streams = 0;
    for (const m of this._machines.values()) streams += m.rate;
    const rate = Math.max(this._rate || 0, streams);
    const known = this._known || streams > 0;
    this._target = ClaudeFleetFlowCard.level(rate, this._config.max);
    if (this._label) this._label.textContent = known ? rate.toLocaleString() : "—";
    if (this._word) this._word.textContent = known ? ClaudeFleetFlowCard.word(this._target) : "no reading";
  }

  /* 0..1 on a curved log scale; `max` reads as 1 and anything above stays there. */
  static level(rate, max) {
    if (!(rate > 0) || !(max > 1)) return 0;
    return Math.pow(Math.min(1, Math.log10(1 + rate) / Math.log10(1 + max)), CURVE);
  }

  /* Tokens a minute from `readings` ([seconds, count] pairs, oldest first) as of
   * `now`, over the last WINDOW_S seconds. The newest reading from before the
   * window is kept as the count the window opened on; older ones are dropped,
   * and so is everything before a fall in the count (midnight, or a restart).
   * The array is trimmed in place. Fewer than two readings left is a rate of 0. */
  static machineRate(readings, now) {
    let from = 0;
    for (let i = 1; i < readings.length; i++) {
      if (readings[i][1] < readings[i - 1][1]) from = i;            // a fall: start again here
      else if (now - readings[i][0] >= WINDOW_S) from = Math.max(from, i);   // i is a newer "before"
    }
    if (from) readings.splice(0, from);
    if (readings.length < 2) return 0;
    const first = readings[0], last = readings[readings.length - 1];
    const opened = Math.max(first[0], now - WINDOW_S);
    const minutes = Math.max(60, now - opened) / 60;
    return Math.max(0, Math.round((last[1] - first[1]) / minutes));
  }

  /* Whether a page last told anything by Home Assistant at `heardAt` was
   * asleep until `now`. A page that has never heard anything was not. */
  static slept(heardAt, now) {
    return heardAt !== undefined && now - heardAt > ASLEEP_S;
  }

  /* Note each computer's counter, and ask the recorder once for the minutes
   * before this page was opened. */
  _readMachines(hass) {
    const now = Date.now() / 1000;
    if (ClaudeFleetFlowCard.slept(this._heardAt, now)) this._machines.clear();
    this._heardAt = now;
    const ids = [];
    for (const id in hass.states) {
      const st = hass.states[id];
      if (!st.attributes || st.attributes.claude_machine_tokens !== true) continue;
      const count = Number(st.state);
      if (!Number.isFinite(count)) continue;
      const name = String(st.attributes.machine || id);
      let m = this._machines.get(name);
      if (!m) {
        m = { id, readings: [], rate: 0, level: 0, drops: [] };
        this._machines.set(name, m);
        ids.push(id);
      }
      const last = m.readings[m.readings.length - 1];
      if (!last || last[1] !== count) m.readings.push([now, count]);
    }
    if (ids.length && hass.callWS) this._history(hass, ids, now);
  }

  _history(hass, ids, now) {
    hass.callWS({
      type: "history/history_during_period",
      start_time: new Date((now - WINDOW_S) * 1000).toISOString(),
      end_time: new Date(now * 1000).toISOString(),
      entity_ids: ids, minimal_response: true, no_attributes: true,
      include_start_time_state: true,
    }).then((res) => {
      for (const m of this._machines.values()) {
        const past = (res && res[m.id] || [])
          .map((r) => [Number(r.lu), Number(r.s)])
          .filter((r) => Number.isFinite(r[0]) && Number.isFinite(r[1]) && r[0] < now);
        if (past.length) m.readings = past.concat(m.readings.filter((r) => r[0] >= now));
      }
    }).catch(() => {});   // no recorder, no past: the streams start from now
  }

  static word(level) {
    if (level <= 0) return "still";
    if (level < 0.3) return "a trickle";
    if (level < 0.65) return "flowing";
    if (level < 0.9) return "running fast";
    return "in flood";
  }

  getCardSize() { return 3; }
  getGridOptions() { return { columns: "full", rows: 3, min_rows: 2 }; }

  _build() {
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        ha-card { display: block; position: relative; overflow: hidden; height: 100%; min-height: 150px; }
        canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
        .text { position: relative; padding: 14px 18px; pointer-events: none;
                text-shadow: 0 0 8px var(--card-background-color, #fff), 0 0 2px var(--card-background-color, #fff); }
        .text.right { text-align: right; }
        .name { font-size: 14px; color: var(--secondary-text-color); }
        .rate { font-size: 34px; font-weight: 500; line-height: 1.2; color: var(--primary-text-color); }
        .word { font-size: 14px; color: var(--secondary-text-color); }
      </style>
      <ha-card>
        <canvas></canvas>
        <div class="text">
          <div class="name"></div>
          <div class="rate">—</div>
          <div class="word"></div>
        </div>
      </ha-card>`;
    this._canvas = root.querySelector("canvas");
    this._text = root.querySelector(".text");
    this._label = root.querySelector(".rate");
    this._word = root.querySelector(".word");
    root.querySelector(".name").textContent = this._config.name;
    this._still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  connectedCallback() {
    this._visible = true;
    if ("IntersectionObserver" in window) {
      this._io = new IntersectionObserver((e) => { this._visible = e[0].isIntersecting; this._wake(); });
      this._io.observe(this);
    }
    this._onVis = () => this._wake();
    document.addEventListener("visibilitychange", this._onVis);
    this._wake();
  }

  disconnectedCallback() {
    if (this._io) this._io.disconnect();
    document.removeEventListener("visibilitychange", this._onVis);
    cancelAnimationFrame(this._raf);
    this._raf = null;
  }

  _wake() {
    if (this._raf || !this._visible || document.hidden || !this._canvas) return;
    this._last = performance.now();
    this._raf = requestAnimationFrame((t) => this._frame(t));
  }

  _frame(t) {
    this._raf = null;
    const dt = Math.min(0.1, (t - this._last) / 1000);
    this._last = t;
    // ease toward the rate, so a new reading swells or ebbs over a few seconds
    this._level += (this._target - this._level) * Math.min(1, dt * 0.9);
    const L = this._still ? this._target : this._level;
    // still water still breathes; a flood moves forty times faster
    this._phase += dt * (0.12 + 4.8 * L * L);
    const now = t / 1000;
    if (!this._ratesAt || now - this._ratesAt > 5) {   // also lets a quiet stream run dry
      this._ratesAt = now;
      for (const m of this._machines.values()) {
        m.rate = ClaudeFleetFlowCard.machineRate(m.readings, Date.now() / 1000);
        m.target = ClaudeFleetFlowCard.level(m.rate, this._config.max);
      }
      this._show();
    }
    for (const m of this._machines.values()) {
      m.level = this._still ? (m.target || 0) : m.level + ((m.target || 0) - m.level) * Math.min(1, dt * 0.9);
    }
    this._draw(L, dt);
    if (this._still) return;                      // one picture, redrawn on the next reading
    if (this._visible && !document.hidden) this._raf = requestAnimationFrame((n) => this._frame(n));
  }

  _draw(L, dt) {
    const c = this._canvas, dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    if (!w || !h) return;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
      c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
    }
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    const tribs = w >= MIN_TRIB_WIDTH ? [...this._machines.keys()].sort() : [];
    const x0 = tribs.length ? Math.round(w * 0.4) : 0;   // where the river begins
    if (this._text) this._text.classList.toggle("right", tribs.length > 0);

    // depth: a quarter of the card when still, most of it in flood
    const depth = h * (0.22 + 0.5 * L);
    const top = h - depth;
    // colour: pale and cool when slow, deep and saturated when fast
    const hue = 205 - 12 * L, sat = 45 + 45 * L;
    const amp = 2 + 13 * L;                        // wave height
    const layers = [
      { k: 0.010, s: 1.0, a: 0.22, lift: 0 },
      { k: 0.016, s: 1.6, a: 0.30, lift: 0.18 },
      { k: 0.024, s: 2.4, a: 0.42, lift: 0.36 },
    ];
    for (const ly of layers) {
      const y0 = top + depth * ly.lift;
      g.beginPath();
      g.moveTo(x0, h);
      for (let x = x0; x <= w; x += 6) {
        const y = y0 + Math.sin(x * ly.k - this._phase * ly.s) * amp
                     + Math.sin(x * ly.k * 2.3 - this._phase * ly.s * 1.7) * amp * 0.35;
        g.lineTo(x, y);
      }
      g.lineTo(w, h);
      g.closePath();
      const colour = (a) => `hsla(${hue}, ${sat}%, ${62 - 22 * L}%, ${a})`;
      if (x0) {   // the river gathers out of the streams; it does not begin at a wall
        const fade = g.createLinearGradient(x0, 0, x0 + 70, 0);
        fade.addColorStop(0, colour(0));
        fade.addColorStop(1, colour(ly.a + 0.25 * L));
        g.fillStyle = fade;
      } else {
        g.fillStyle = colour(ly.a + 0.25 * L);
      }
      g.fill();
    }

    // drops carried along: none in still water, a crowd in flood
    const want = Math.round(L * L * Math.min(140, (w - x0) / 6));
    while (this._drops.length < want) this._drops.push(this._drop(w, true, x0));
    if (this._drops.length > want) this._drops.length = want;
    const speed = w * (0.05 + 0.75 * L * L);       // px a second
    g.lineCap = "round";
    for (const d of this._drops) {
      if (!this._still) d.x += speed * d.v * dt;
      if (d.x > w + 20) Object.assign(d, this._drop(w, false, x0));
      const y = top + depth * d.y + Math.sin(d.x * 0.02 - this._phase * 2) * amp * 0.6;
      const len = 3 + 26 * L * d.v;                // faster water draws longer streaks
      g.strokeStyle = `hsla(${hue - 8}, 95%, ${88 - 10 * L}%, ${0.25 + 0.5 * d.v * L})`;
      g.lineWidth = 1 + 1.6 * d.v;
      g.beginPath();
      g.moveTo(Math.max(x0, d.x - len), y);
      g.lineTo(Math.max(x0, d.x), y);
      g.stroke();
    }
    if (tribs.length) this._drawTributaries(g, tribs, x0, h, top, depth, dt);
  }

  /* One stream a computer, from the left edge into the river. A stream is a
   * thick curve: its width and the speed of what it carries are that computer's
   * rate, and a computer writing nothing is a thin pale bed with its name on it. */
  _drawTributaries(g, names, x0, h, top, depth, dt) {
    const css = getComputedStyle(this);
    const ink = css.getPropertyValue("--secondary-text-color").trim() || "#727272";
    const n = names.length, band = (h - 16) / n;
    g.font = "12px Roboto, Noto, sans-serif";
    g.textBaseline = "bottom";
    names.forEach((name, i) => {
      const m = this._machines.get(name), L = m.level;
      const y0 = 8 + band * (i + 0.5) + Math.min(10, band * 0.15);   // leaves room for the name above
      const y1 = top + depth * (0.2 + 0.6 * (i + 0.5) / n);
      const thick = Math.min(band * 0.55, 2 + 24 * L);
      const p = [[-4, y0], [x0 * 0.5, y0], [x0 * 0.6, y1], [x0 + 60, y1]];   // ends inside the river's fade
      const at = (t) => {
        const u = 1 - t;
        return [0, 1].map((k) => u * u * u * p[0][k] + 3 * u * u * t * p[1][k] + 3 * u * t * t * p[2][k] + t * t * t * p[3][k]);
      };
      const hue = 205 - 12 * L, sat = 45 + 45 * L;
      g.lineCap = "butt";
      const water = (a) => `hsla(${hue}, ${sat}%, ${62 - 22 * L}%, ${a})`;
      const tail = g.createLinearGradient(x0, 0, x0 + 60, 0);   // dissolves into the river
      tail.addColorStop(0, water(0.35 + 0.5 * L));
      tail.addColorStop(1, water(0));
      g.strokeStyle = tail;
      g.lineWidth = thick;
      g.beginPath();
      g.moveTo(p[0][0], p[0][1]);
      g.bezierCurveTo(p[1][0], p[1][1], p[2][0], p[2][1], p[3][0], p[3][1]);
      g.stroke();

      const want = Math.round(L * 14);
      while (m.drops.length < want) m.drops.push({ t: Math.random(), o: Math.random() - 0.5, v: 0.5 + Math.random() * 0.7 });
      if (m.drops.length > want) m.drops.length = want;
      g.lineCap = "round";
      for (const d of m.drops) {
        if (!this._still) d.t += dt * d.v * (0.06 + 0.9 * L * L);
        if (d.t > 1) { d.t = 0; d.o = Math.random() - 0.5; }
        const a = at(Math.max(0, d.t - 0.02 - 0.05 * L)), b = at(d.t), off = d.o * thick * 0.7;
        g.strokeStyle = `hsla(${hue - 8}, 95%, ${88 - 10 * L}%, ${0.3 + 0.5 * L})`;
        g.lineWidth = 1 + 1.4 * d.v;
        g.beginPath();
        g.moveTo(a[0], a[1] + off);
        g.lineTo(b[0], b[1] + off);
        g.stroke();
      }
      g.fillStyle = ink;
      g.fillText(`${name} · ${m.rate.toLocaleString()}`, 12, y0 - thick / 2 - 3);
    });
  }

  _drop(w, anywhere, x0) {
    return { x: anywhere ? x0 + Math.random() * (w - x0) : x0 - Math.random() * 40,
             y: 0.12 + Math.random() * 0.8, v: 0.45 + Math.random() * 0.75 };
  }
}

/* ------------------------------------------------------------------ timeline
 * Claude Fleet timeline card: today, one lane per session, coloured by what it
 * was doing: working, waiting on you, idle. The dashboard counts how long
 * sessions waited on you; this shows WHEN, and whether three waited at once.
 *
 *   type: custom:claude-fleet-timeline-card
 *   title: Today                        (optional)
 *
 * Nothing to configure: the sessions are the sensors carrying `claude_session`,
 * plus the ones the cleanup has already removed today, which the archive
 * (sensor.claude_archived_today) lists with their entity and when they went.
 * Their states come from the recorder, asked again every minute. A lane ends
 * where its session was archived; a session that never worked or waited today
 * (open since yesterday, idle all day) gets no lane.
 */
const TIMELINE_KINDS = {
  working: { label: "working", colour: "#285AFF" },
  needs_input: { label: "waiting on you", colour: "#FF8C00" },
  idle: { label: "idle", colour: "#00C85A", faint: true },
};
const TIMELINE_REFRESH_S = 60;

class ClaudeFleetTimelineCard extends HTMLElement {
  static getStubConfig() { return {}; }

  setConfig(config) {
    this._config = { title: "Today", ...(config || {}) };
    if (!this.shadowRoot) this._build();
    this._title.textContent = this._config.title;
  }

  set hass(hass) {
    this._hass = hass;
    const now = Date.now() / 1000;
    const sig = ClaudeFleetTimelineCard.signature(hass.states);
    const due = !this._fetchedAt || now - this._fetchedAt > TIMELINE_REFRESH_S;
    const changed = sig !== this._sig && (!this._fetchedAt || now - this._fetchedAt > 10);
    if (due || changed) { this._sig = sig; this._fetch(hass, now); }
  }

  /* What a refetch hangs on: which sessions there are and what each is doing. */
  static signature(states) {
    const parts = [];
    for (const id in states) {
      const a = states[id].attributes;
      if (a && a.claude_session) parts.push(id + "=" + states[id].state);
    }
    return parts.sort().join(",");
  }

  /* Seconds since the epoch of the last midnight in time zone `tz`. */
  static midnight(nowS, tz) {
    try {
      const f = new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23",
        hour: "2-digit", minute: "2-digit", second: "2-digit" });
      const p = {};
      for (const x of f.formatToParts(new Date(nowS * 1000))) p[x.type] = x.value;
      return Math.floor(nowS) - (Number(p.hour) % 24) * 3600 - Number(p.minute) * 60 - Number(p.second);
    } catch (e) {
      const d = new Date(nowS * 1000); d.setHours(0, 0, 0, 0);
      return d.getTime() / 1000;
    }
  }

  /* The sessions to draw: entity id -> {label, machine, live, endAt}. Live ones
   * from their sensors, archived ones from the archive, live winning (a session
   * resumed after the cleanup removed it is both). */
  static wanted(states) {
    const arch = (states["sensor.claude_archived_today"] || {}).attributes || {};
    const today = arch.day;
    const out = new Map();
    for (const id in states) {
      const a = states[id].attributes || {};
      if (a.claude_session && (!today || a.day === today)) {
        out.set(id, { label: a.label || id, machine: a.machine || "", live: true });
      }
    }
    for (const e of (today ? arch.sessions || [] : [])) {
      if (!e || !e.entity_id || out.has(e.entity_id)) continue;
      const at = e.archived_at ? Date.parse(e.archived_at) / 1000 : NaN;
      out.set(e.entity_id, { label: e.label || e.entity_id, machine: e.machine || "", live: false,
                             endAt: Number.isFinite(at) ? at : undefined });
    }
    return out;
  }

  /* Recorder rows (oldest first, {s, lu}) to [{kind, from, to}] between `start`
   * and `end`, stopping at `endAt` when the session was archived. States that are
   * not a kind (ended, unknown, unavailable) are gaps. Touching runs of one kind
   * merge. */
  static segments(rows, start, end, endAt) {
    const stop = Math.min(end, endAt === undefined ? end : endAt);
    const out = [];
    for (let i = 0; i < rows.length; i++) {
      const kind = rows[i].s;
      if (!TIMELINE_KINDS[kind]) continue;
      const a = Math.max(start, Number(rows[i].lu));
      const b = Math.min(stop, i + 1 < rows.length ? Number(rows[i + 1].lu) : stop);
      if (!(b > a)) continue;
      const last = out[out.length - 1];
      if (last && last.kind === kind && a - last.to < 1) last.to = b;
      else out.push({ kind, from: a, to: b });
    }
    return out;
  }

  /* Time spent waiting on you across all lanes: total seconds, how many waits,
   * and the longest ({d, at, label}). */
  static waits(lanes) {
    const w = { total: 0, count: 0, longest: null };
    for (const lane of lanes) {
      for (const g of lane.segs) {
        if (g.kind !== "needs_input") continue;
        const d = g.to - g.from;
        w.total += d; w.count += 1;
        if (!w.longest || d > w.longest.d) w.longest = { d, at: g.from, label: lane.label };
      }
    }
    return w;
  }

  /* Lanes worth drawing (something besides idle), ordered by when each first
   * worked or waited. */
  static lanes(wanted, history, start, now) {
    const out = [];
    for (const [id, w] of wanted) {
      const rows = (history && history[id]) || [];
      let endAt = w.endAt;
      // archived before the archive kept the time: stop at its last state change
      if (!w.live && endAt === undefined && rows.length) endAt = Number(rows[rows.length - 1].lu);
      const segs = ClaudeFleetTimelineCard.segments(rows, start, now, endAt);
      const busy = segs.filter((g) => g.kind !== "idle");
      if (!busy.length) continue;
      out.push({ id, label: w.label, machine: w.machine, segs, first: busy[0].from });
    }
    return out.sort((a, b) => a.first - b.first);
  }

  _fetch(hass, now) {
    this._fetchedAt = now;
    const tz = (hass.config && hass.config.time_zone) || undefined;
    const start = ClaudeFleetTimelineCard.midnight(now, tz);
    const wanted = ClaudeFleetTimelineCard.wanted(hass.states);
    if (!wanted.size || !hass.callWS) { this._render({ lanes: [], start, now, tz }); return; }
    hass.callWS({
      type: "history/history_during_period",
      start_time: new Date(start * 1000).toISOString(),
      end_time: new Date(now * 1000).toISOString(),
      entity_ids: [...wanted.keys()], minimal_response: true, no_attributes: true,
      include_start_time_state: true,
    }).then((res) => {
      this._render({ lanes: ClaudeFleetTimelineCard.lanes(wanted, res, start, now), start, now, tz });
    }).catch((err) => {
      this._render({ error: (err && err.message) || "no answer", start, now, tz });
    });
  }

  static clock(t, tz) {
    try {
      return new Date(t * 1000).toLocaleTimeString([], { timeZone: tz, hour: "numeric", minute: "2-digit" });
    } catch (e) { return new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }
  }

  static mins(d) {
    const m = Math.round(d / 60);
    return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m} min`;
  }

  _render(v) {
    const el = this._body;
    if (!el) return;
    const C = ClaudeFleetTimelineCard;
    if (v.error) {
      el.innerHTML = `<div class="note">Could not read today's history from the recorder (${C.esc(v.error)}).</div>`;
      return;
    }
    if (!v.lanes.length) {
      el.innerHTML = `<div class="note">No session has worked or waited on you yet today.</div>`;
      return;
    }
    // the axis starts on the hour before the first thing drawn, not at midnight
    const first = Math.min(...v.lanes.map((l) => l.segs[0].from));
    const axis0 = Math.max(v.start, first - (first - v.start) % 3600);
    const span = Math.max(600, v.now - axis0);
    const pct = (t) => (100 * (t - axis0) / span);
    const hours = span / 3600;
    const step = [1, 2, 3, 4, 6].find((h) => hours / h <= 7) || 6;
    const ticks = [];
    for (let t = axis0 + 3600 - ((axis0 - v.start) % 3600 || 3600); t < v.now; t += 3600) {
      if (Math.round((t - v.start) / 3600) % step === 0) ticks.push(t);
    }
    const tickLines = ticks.map((t) => `<i class="tick" style="left:${pct(t).toFixed(2)}%"></i>`).join("");
    const w = C.waits(v.lanes);
    const said = w.count
      ? `Waited on you <b>${C.mins(w.total)}</b> today, ${w.count} time${w.count === 1 ? "" : "s"}; ` +
        `longest ${C.mins(w.longest.d)} at ${C.clock(w.longest.at, v.tz)} (${C.esc(w.longest.label)}).`
      : "Nothing waited on you today.";
    const machines = new Set(v.lanes.map((l) => l.machine));
    const rows = v.lanes.map((l) => {
      const segs = l.segs.map((g) => {
        const k = TIMELINE_KINDS[g.kind];
        const tip = `${l.label}: ${k.label}, ${C.clock(g.from, v.tz)}–${C.clock(g.to, v.tz)} (${C.mins(g.to - g.from)})`;
        return `<i class="seg${k.faint ? " faint" : ""}" title="${C.esc(tip)}" style="left:${pct(g.from).toFixed(3)}%;` +
               `width:calc(${(pct(g.to) - pct(g.from)).toFixed(3)}% - 1px);background:${k.colour}"></i>`;
      }).join("");
      const sub = machines.size > 1 ? `<small>${C.esc(l.machine)}</small>` : "";
      return `<div class="row"><div class="name" title="${C.esc(l.label)}">${C.esc(l.label)}${sub}</div>` +
             `<div class="track">${tickLines}${segs}</div></div>`;
    }).join("");
    const labels = ticks.map((t) => `<span style="left:${pct(t).toFixed(2)}%">${C.clock(t, v.tz)}</span>`).join("");
    const legend = Object.values(TIMELINE_KINDS).map((k) =>
      `<span><i class="${k.faint ? "faint" : ""}" style="background:${k.colour}"></i>${k.label}</span>`).join("");
    el.innerHTML = `<div class="said">${said}</div><div class="legend">${legend}</div>${rows}` +
                   `<div class="row axis"><div class="name"></div><div class="track">${labels}</div></div>`;
  }

  static esc(x) {
    return String(x).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  getCardSize() { return 4; }
  getGridOptions() { return { columns: "full", min_rows: 2 }; }

  _build() {
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        ha-card { padding: 12px 16px 14px; }
        h2 { margin: 0 0 6px; font-size: 16px; font-weight: 500; color: var(--primary-text-color); }
        .said { font-size: 14px; color: var(--primary-text-color); margin-bottom: 6px; }
        .note { font-size: 14px; color: var(--secondary-text-color); }
        .legend { display: flex; gap: 14px; font-size: 12px; color: var(--secondary-text-color); margin-bottom: 8px; }
        .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
        .row { display: flex; align-items: center; height: 24px; }
        .name { width: 28%; max-width: 260px; padding-right: 10px; box-sizing: border-box; font-size: 13px;
                color: var(--primary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .name small { color: var(--secondary-text-color); margin-left: 6px; }
        .track { position: relative; flex: 1; height: 100%; }
        .seg { position: absolute; top: 6px; height: 12px; border-radius: 3px; min-width: 2px; }
        .faint { opacity: .3; }
        .tick { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--divider-color, rgba(127,127,127,.2)); }
        .axis { height: 18px; }
        .axis span { position: absolute; transform: translateX(-50%); font-size: 11px; color: var(--secondary-text-color); white-space: nowrap; }
      </style>
      <ha-card><h2></h2><div class="body"><div class="note">Reading today's history…</div></div></ha-card>`;
    this._title = root.querySelector("h2");
    this._body = root.querySelector(".body");
  }
}

/* ------------------------------------------------------------------ projects
 * Claude Fleet projects card: where the tokens went, by project, today and day
 * by day. A project is the session's git repository, or its folder when it is
 * not in one: the hook reports either as `repo`, so this reads the same for
 * someone who never uses git, and someone who only ever works in one project
 * is told so in a sentence instead of shown a chart of one colour.
 *
 *   type: custom:claude-fleet-projects-card
 *   days: 30                            (optional; how many days the trend shows)
 *
 * Today is live, from sensor.claude_tokens_by_project_today. Earlier days come
 * from sensor.claude_tokens_by_project_by_day, which saves each day just before
 * midnight. A toggle switches between all tokens and the tokens written
 * (output): almost all of "all" is the conversation re-read every turn, so it
 * measures how long contexts got as much as how much work was done.
 *
 * Colours follow the project, never its rank on a day: the biggest projects
 * across the days shown take the palette in order, the rest share "Other".
 */
const DAY_BARS_PX = 130;   // height of the by-day bars
const PROJECT_COLOURS = {
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"],
  dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9"],
  other: "#8a8a8a",
};

class ClaudeFleetProjectsCard extends HTMLElement {
  static getStubConfig() { return {}; }

  setConfig(config) {
    this._config = { title: "Tokens by project", days: 30,
                     today_entity: "sensor.claude_tokens_by_project_today",
                     days_entity: "sensor.claude_tokens_by_project_by_day", ...(config || {}) };
    this._measure = this._measure || "total";
    if (!this.shadowRoot) this._build();
    this._title.textContent = this._config.title;
  }

  set hass(hass) {
    this._hass = hass;
    const a = hass.states[this._config.today_entity], b = hass.states[this._config.days_entity];
    const sig = [a && a.last_updated, b && b.last_updated, hass.themes && hass.themes.darkMode].join("|");
    if (sig !== this._sig) { this._sig = sig; this._render(); }
  }

  /* Everything the card draws, from the today list ([{name, total, output}]), the
   * saved days ({day: {name: [total, output]}}), today's date, how many days to
   * show and which measure. Pure, so it can be tested without a page. */
  static model(today, saved, todayKey, nDays, measure) {
    const idx = measure === "output" ? 1 : 0;
    const days = {};
    for (const d in (saved || {})) if (d !== todayKey && saved[d] && typeof saved[d] === "object") days[d] = saved[d];
    const live = {};
    for (const p of today || []) if (p && p.name) live[p.name] = [Number(p.total) || 0, Number(p.output) || 0];
    if (Object.keys(live).length) days[todayKey] = live;
    const keys = Object.keys(days).sort().slice(-nDays);
    const sums = new Map();
    for (const d of keys) for (const n in days[d]) sums.set(n, (sums.get(n) || 0) + (Number(days[d][n][idx]) || 0));
    const ranked = [...sums.entries()].filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([n]) => n);
    const named = ranked.filter((n) => n !== "(others)").slice(0, PROJECT_COLOURS.light.length);
    const slot = new Map(named.map((n, i) => [n, i]));
    const fold = (row) => {
      const parts = new Map();
      let total = 0;
      for (const n in row) {
        const v = Number(row[n][idx]) || 0;
        if (!(v > 0)) continue;
        const k = slot.has(n) ? n : "Other";
        parts.set(k, (parts.get(k) || 0) + v);
        total += v;
      }
      const order = [...named, "Other"].filter((k) => parts.has(k));
      return { total, parts: order.map((k) => ({ name: k, value: parts.get(k), slot: slot.has(k) ? slot.get(k) : -1 })) };
    };
    return {
      single: ranked.length === 1 ? ranked[0] : null,
      empty: ranked.length === 0,
      named, keys,
      today: days[todayKey] ? fold(days[todayKey]) : { total: 0, parts: [] },
      rows: keys.map((d) => ({ day: d, ...fold(days[d]) })),
      saved: keys.filter((d) => d !== todayKey).length,
    };
  }

  static n(x) {
    if (x >= 1e9) return (x / 1e9).toFixed(1) + "B";
    if (x >= 1e6) return (x / 1e6).toFixed(1) + "M";
    if (x >= 1e3) return Math.round(x / 1e3) + "k";
    return String(Math.round(x));
  }

  _render() {
    const hass = this._hass, el = this._body;
    if (!hass || !el) return;
    const C = ClaudeFleetProjectsCard, esc = ClaudeFleetTimelineCard.esc;
    const tSt = hass.states[this._config.today_entity];
    if (!tSt) {
      el.innerHTML = `<div class="note">${esc(this._config.today_entity)} is missing: is the Claude Fleet package up to date?</div>`;
      return;
    }
    const arch = hass.states["sensor.claude_archived_today"];
    const todayKey = (arch && arch.attributes.day) || new Date().toISOString().slice(0, 10);
    const dSt = hass.states[this._config.days_entity];
    const m = C.model(tSt.attributes.projects, dSt && dSt.attributes.days, todayKey,
                      Number(this._config.days) || 30, this._measure);
    const pal = PROJECT_COLOURS[hass.themes && hass.themes.darkMode ? "dark" : "light"];
    const colour = (p) => (p.slot >= 0 ? pal[p.slot] : PROJECT_COLOURS.other);
    const what = "A project is the git repository a session works in, or its folder when it is not in one.";
    this._toggle.hidden = m.empty || !!m.single;
    if (m.empty) {
      el.innerHTML = `<div class="note">Nothing counted yet today.</div><div class="foot">${what}</div>`;
      return;
    }
    if (m.single) {
      el.innerHTML = `<div class="note">All of it was in one project: <b>${esc(m.single)}</b>. ` +
        `With work in more than one, this shows where the tokens went.</div><div class="foot">${what}</div>`;
      return;
    }
    const share = (v, t) => (t > 0 ? Math.round(100 * v / t) : 0);
    const bar = m.today.parts.map((p) =>
      `<i style="flex:${p.value} 1 0;background:${colour(p)}" title="${esc(`${p.name}: ${C.n(p.value)} (${share(p.value, m.today.total)}%)`)}"></i>`).join("");
    const legend = m.today.parts.map((p) =>
      `<li><i style="background:${colour(p)}"></i><span class="pn">${esc(p.name)}</span>` +
      `<span class="pv">${share(p.value, m.today.total)}% · ${C.n(p.value)}</span></li>`).join("");
    const todayBlock = m.today.total > 0
      ? `<div class="h">Today · ${C.n(m.today.total)}</div><div class="stack">${bar}</div><ul>${legend}</ul>`
      : `<div class="h">Today</div><div class="note">Nothing counted yet today.</div>`;
    let trend;
    if (m.saved === 0) {
      trend = `<div class="note">The trend starts tonight: each day is saved just before midnight.</div>`;
    } else {
      const max = Math.max(...m.rows.map((r) => r.total), 1);
      const every = Math.max(1, Math.ceil(m.rows.length / 6));
      const cols = m.rows.map((r, i) => {
        // a sliver under ~1.5px is not drawn: with the gap between segments a
        // few of them stack into stripes; the day's title still lists them
        const tip = r.parts.map((p) => `${p.name} ${C.n(p.value)}`).join(", ");
        const segs = r.parts.filter((p) => p.value / max * DAY_BARS_PX >= 1.5).map((p) =>
          `<i style="height:${(100 * p.value / max).toFixed(2)}%;background:${colour(p)}" ` +
          `title="${esc(`${r.day} · ${p.name}: ${C.n(p.value)} (${share(p.value, r.total)}%)`)}"></i>`).join("");
        const lab = (m.rows.length - 1 - i) % every === 0 ? r.day.slice(5).replace("-", "/") : "";
        return `<div class="col" title="${esc(`${r.day} · ${C.n(r.total)}: ${tip}`)}"><div class="bars">${segs}</div><span>${lab}</span></div>`;
      }).join("");
      const names = [...m.named, ...(m.rows.some((r) => r.parts.some((p) => p.slot < 0)) ? ["Other"] : [])];
      const key = names.map((n) => {
        const i = m.named.indexOf(n);
        return `<span><i style="background:${i >= 0 ? pal[i] : PROJECT_COLOURS.other}"></i>${esc(n)}</span>`;
      }).join("");
      trend = `<div class="h">By day <small>· tallest ${C.n(max)}</small></div><div class="days">${cols}</div><div class="key">${key}</div>`;
    }
    el.innerHTML = todayBlock + trend + `<div class="foot">${what}</div>`;
  }

  getCardSize() { return 5; }
  getGridOptions() { return { columns: "full", min_rows: 3 }; }

  _build() {
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        ha-card { padding: 12px 16px 14px; }
        header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
        h2 { margin: 0; font-size: 16px; font-weight: 500; color: var(--primary-text-color); }
        .toggle { display: flex; border: 1px solid var(--divider-color, rgba(127,127,127,.3)); border-radius: 6px; overflow: hidden; }
        .toggle[hidden] { display: none; }
        .toggle button { font: inherit; font-size: 12px; padding: 3px 10px; border: 0; background: none; cursor: pointer;
                         color: var(--secondary-text-color); }
        .toggle button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); }
        .h { font-size: 13px; color: var(--secondary-text-color); margin: 8px 0 6px; }
        .h small { font-size: 12px; }
        .note { font-size: 14px; color: var(--primary-text-color); }
        .foot { font-size: 12px; color: var(--secondary-text-color); margin-top: 10px; }
        .stack { display: flex; gap: 2px; height: 14px; }
        .stack i { min-width: 2px; border-radius: 3px; }
        ul { list-style: none; padding: 0; margin: 8px 0 0; display: flex; flex-wrap: wrap; gap: 4px 20px; }
        li { display: flex; align-items: center; font-size: 13px; color: var(--primary-text-color); min-width: 0; max-width: 100%; }
        li i, .key i { flex: none; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; display: inline-block; }
        .pn { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .pv { color: var(--secondary-text-color); margin-left: 8px; white-space: nowrap; }
        .days { display: flex; gap: 2px; height: ${DAY_BARS_PX + 14}px; align-items: stretch; }
        .col { flex: 1; display: flex; flex-direction: column; min-width: 0; }
        .bars { flex: 1; display: flex; flex-direction: column-reverse; gap: 1px; }
        .bars i { display: block; border-radius: 2px; min-height: 0; }
        .bars i:first-child { border-radius: 0 0 2px 2px; }
        .col span { height: 14px; font-size: 10px; text-align: center; color: var(--secondary-text-color); white-space: nowrap; overflow: visible; }
        .key { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: var(--secondary-text-color); margin-top: 6px; }
      </style>
      <ha-card>
        <header><h2></h2><div class="toggle"><button data-m="total">All tokens</button><button data-m="output">Written</button></div></header>
        <div class="body"></div>
      </ha-card>`;
    this._title = root.querySelector("h2");
    this._body = root.querySelector(".body");
    this._toggle = root.querySelector(".toggle");
    const sync = () => this._toggle.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.m === this._measure));
    this._toggle.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      this._measure = b.dataset.m;
      sync();
      this._render();
    });
    sync();
  }
}

/* Defined only once Home Assistant itself is. Loaded through `extra_module_url`
 * this file can run before the frontend has started, and the frontend replaces
 * the browser's element registry when it does: an element defined before that
 * is left in the old one, where Home Assistant never looks, and the card shows
 * "Custom element doesn't exist" on the first page load and works on the next.
 * The string below is spelt out in full because the end-to-end check looks for it. */
function defineFlowCard() {
  if (window.customElements.get("claude-fleet-flow-card")) return;
  // customElements.define("claude-fleet-flow-card", ...)
  window.customElements.define("claude-fleet-flow-card", ClaudeFleetFlowCard);
  // customElements.define("claude-fleet-timeline-card", ...)
  window.customElements.define("claude-fleet-timeline-card", ClaudeFleetTimelineCard);
  // customElements.define("claude-fleet-projects-card", ...)
  window.customElements.define("claude-fleet-projects-card", ClaudeFleetProjectsCard);
  window.customCards = window.customCards || [];
  window.customCards.push(
    { type: "claude-fleet-flow-card", name: "Claude Fleet flow",
      description: "A stream that runs with the fleet's token rate." },
    { type: "claude-fleet-timeline-card", name: "Claude Fleet timeline",
      description: "Today, one lane per session: working, waiting on you, idle." },
    { type: "claude-fleet-projects-card", name: "Claude Fleet projects",
      description: "Where the tokens went, by project, today and by day." });
  console.info(`claude-fleet cards ${FLOW_VERSION}`);
}

window.customElements.whenDefined("home-assistant").then(defineFlowCard);
