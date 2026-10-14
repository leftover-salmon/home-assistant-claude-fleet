/* Claude Fleet flow card.
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
 * No dependencies and nothing fetched: one file, drawn on a canvas. It stops
 * drawing when the card is off screen or the tab is hidden, and draws a still
 * picture for anyone who has asked their system for reduced motion.
 */
const FLOW_VERSION = "2";
const WINDOW_S = 330;         // a computer's rate is read over this long
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

  /* Note each computer's counter, and ask the recorder once for the minutes
   * before this page was opened. */
  _readMachines(hass) {
    const now = Date.now() / 1000;
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
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "claude-fleet-flow-card",
    name: "Claude Fleet flow",
    description: "A stream that runs with the fleet's token rate.",
  });
  console.info(`claude-fleet-flow-card ${FLOW_VERSION}`);
}

window.customElements.whenDefined("home-assistant").then(defineFlowCard);
