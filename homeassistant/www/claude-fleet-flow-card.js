/* Claude Fleet flow card.
 *
 * A stream that runs with the fleet's token rate: still water at zero, a slow
 * current when one session is writing, a fast, full, bright one when many are.
 * One number becomes speed, depth, colour and the count of drops in the water.
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
 * No dependencies and nothing fetched: one file, drawn on a canvas. It stops
 * drawing when the card is off screen or the tab is hidden, and draws a still
 * picture for anyone who has asked their system for reduced motion.
 */
const FLOW_VERSION = "1";
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
    if (!this.shadowRoot) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    const st = hass.states[this._config.entity];
    const rate = st ? Number(st.state) : NaN;
    this._known = Number.isFinite(rate);
    this._rate = this._known ? Math.max(0, rate) : 0;
    this._target = ClaudeFleetFlowCard.level(this._rate, this._config.max);
    if (this._label) this._label.textContent = this._known ? this._rate.toLocaleString() : "—";
    if (this._word) this._word.textContent = this._known ? ClaudeFleetFlowCard.word(this._target) : "no reading";
    if (this._still) this._wake();   // no animation running: draw the new reading once
  }

  /* 0..1 on a curved log scale; `max` reads as 1 and anything above stays there. */
  static level(rate, max) {
    if (!(rate > 0) || !(max > 1)) return 0;
    return Math.pow(Math.min(1, Math.log10(1 + rate) / Math.log10(1 + max)), CURVE);
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
        ha-card { position: relative; overflow: hidden; height: 100%; min-height: 150px; }
        canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
        .text { position: relative; padding: 14px 18px; pointer-events: none;
                text-shadow: 0 0 8px var(--card-background-color, #fff), 0 0 2px var(--card-background-color, #fff); }
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
      g.moveTo(0, h);
      for (let x = 0; x <= w; x += 6) {
        const y = y0 + Math.sin(x * ly.k - this._phase * ly.s) * amp
                     + Math.sin(x * ly.k * 2.3 - this._phase * ly.s * 1.7) * amp * 0.35;
        g.lineTo(x, y);
      }
      g.lineTo(w, h);
      g.closePath();
      g.fillStyle = `hsla(${hue}, ${sat}%, ${62 - 22 * L}%, ${ly.a + 0.25 * L})`;
      g.fill();
    }

    // drops carried along: none in still water, a crowd in flood
    const want = Math.round(L * L * Math.min(140, w / 6));
    while (this._drops.length < want) this._drops.push(this._drop(w, true));
    if (this._drops.length > want) this._drops.length = want;
    const speed = w * (0.05 + 0.75 * L * L);       // px a second
    g.lineCap = "round";
    for (const d of this._drops) {
      if (!this._still) d.x += speed * d.v * dt;
      if (d.x > w + 20) Object.assign(d, this._drop(w, false));
      const y = top + depth * d.y + Math.sin(d.x * 0.02 - this._phase * 2) * amp * 0.6;
      const len = 3 + 26 * L * d.v;                // faster water draws longer streaks
      g.strokeStyle = `hsla(${hue - 8}, 95%, ${88 - 10 * L}%, ${0.25 + 0.5 * d.v * L})`;
      g.lineWidth = 1 + 1.6 * d.v;
      g.beginPath();
      g.moveTo(d.x - len, y);
      g.lineTo(d.x, y);
      g.stroke();
    }
  }

  _drop(w, anywhere) {
    return { x: anywhere ? Math.random() * w : -Math.random() * 40,
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
