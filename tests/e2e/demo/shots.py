"""Screenshots of the demo dashboard, through a headless Chrome's DevTools port.

    python shots.py OUTDIR NAME:VIEW:WIDTH[:phone[:MAXHEIGHT]] ...

Logs in the way cdp.py does (the refresh token in $SECRETS/refresh, put where
the frontend keeps its tokens), loads each view, sizes the window to the whole
page, and saves OUTDIR/NAME.png. Prints, per view, any error cards, console
errors and how many history explorer charts drew, and exits non-zero if a view
has an error card.
"""
import asyncio
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import websockets

BASE = os.environ.get("BASE_URL", "http://localhost:8123")
PORT = int(os.environ.get("CDP_PORT", "9333"))
SECRETS = os.environ["SECRETS"]


def tokens():
    rt = open(os.path.join(SECRETS, "refresh")).read().strip()
    d = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": rt,
                                "client_id": BASE + "/"}).encode()
    r = json.load(urllib.request.urlopen(BASE + "/auth/token", d))
    return {"access_token": r["access_token"], "token_type": "Bearer", "expires_in": r["expires_in"],
            "hassUrl": BASE, "clientId": BASE + "/", "refresh_token": rt,
            "expires": int(time.time() * 1000) + r["expires_in"] * 1000}


# Walks every shadow root: error cards, error alerts, charts, and the height of
# the view's content, which lives inside shadow roots the document cannot measure.
PROBE = """
(() => {
 const out = {errors: [], hx: 0, hxCanvas: 0, bottom: 0, unknown: []};
 const walk = (root) => { for (const el of root.querySelectorAll('*')) {
   const t = el.tagName.toLowerCase();
   if (t === 'hui-error-card') out.errors.push((el.shadowRoot ? el.shadowRoot.textContent : el.textContent).trim().slice(0, 300));
   if (t === 'ha-alert' && el.getAttribute('alert-type') === 'error') out.errors.push('alert: ' + el.textContent.trim().slice(0, 300));
   if (t === 'history-explorer-card') out.hx++;
   if (t === 'canvas') out.hxCanvas++;
   if (t === 'hui-sections-view' || t === 'hui-masonry-view') {
     const r = el.getBoundingClientRect(); out.bottom = Math.max(out.bottom, r.bottom + window.scrollY);
   }
   if (el.shadowRoot) walk(el.shadowRoot);
 }};
 walk(document);
 const txt = document.body.innerText;
 return JSON.stringify(out);
})()
"""


async def main():
    outdir = sys.argv[1]
    jobs = [a.split(":") for a in sys.argv[2:]]
    req = urllib.request.Request("http://127.0.0.1:%d/json/new?about:blank" % PORT, method="PUT")
    target = json.load(urllib.request.urlopen(req))
    bad = 0
    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None) as ws:
        n = 0
        logs = []

        def handle(m):
            meth = m.get("method")
            if meth == "Runtime.consoleAPICalled" and m["params"]["type"] in ("error", "warning", "warn"):
                logs.append(m["params"]["type"] + ": " + " ".join(
                    str(a.get("value", a.get("description", "")))[:300] for a in m["params"]["args"]))
            elif meth == "Runtime.exceptionThrown":
                d = m["params"]["exceptionDetails"]
                logs.append("EXC: " + (d.get("exception", {}).get("description") or d.get("text"))[:400])
            elif meth == "Log.entryAdded" and m["params"]["entry"]["level"] in ("error", "warning"):
                logs.append("LOG: " + m["params"]["entry"]["text"][:300])

        async def call(method, params=None):
            nonlocal n
            n += 1
            i = n
            await ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == i:
                    return m.get("result", m)
                handle(m)

        async def pump(sec):
            end = time.time() + sec
            while time.time() < end:
                try:
                    handle(json.loads(await asyncio.wait_for(ws.recv(), timeout=0.5)))
                except asyncio.TimeoutError:
                    pass

        async def evaluate(expr):
            r = await call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
            return r["result"].get("value")

        async def size(w, h, phone):
            await call("Emulation.setDeviceMetricsOverride", {
                "width": w, "height": h, "deviceScaleFactor": 2 if phone else 1, "mobile": phone})
            if phone:
                await call("Emulation.setUserAgentOverride", {"userAgent":
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
                    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"})

        for d in ("Runtime.enable", "Log.enable", "Page.enable"):
            await call(d)
        await size(1600, 1000, False)
        # a page on HA's origin that is not the frontend: the storage below is set
        # before the frontend first runs, so it never tries to connect without it
        await call("Page.navigate", {"url": BASE + "/manifest.json"})
        await pump(2)
        # the frontend's own storage: its tokens, the sidebar hidden, light theme
        await evaluate("localStorage.setItem('hassTokens', JSON.stringify(%s));"
                       "localStorage.setItem('dockedSidebar', JSON.stringify('always_hidden'));"
                       "localStorage.setItem('selectedTheme', JSON.stringify({theme: 'default', dark: false}));"
                       % json.dumps(tokens()))
        for name, view, width, *rest in jobs:
            phone = bool(rest and rest[0] == "phone")
            w = int(width)
            logs.clear()
            await size(w, 1000, phone)
            await call("Page.navigate", {"url": "%s/claude-fleet/%s" % (BASE, view)})
            await pump(10)
            p = json.loads(await evaluate(PROBE))
            h = max(int(p["bottom"]) + 24, 600)
            if len(rest) > 1:
                h = min(h, int(rest[1]))   # the top of a long page only
            await size(w, h, phone)
            await pump(6)    # charts redraw at the new size
            p = json.loads(await evaluate(PROBE))
            shot = await call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
            path = os.path.join(outdir, name + ".png")
            with open(path, "wb") as f:
                f.write(base64.b64decode(shot["data"]))
            print("== %s  %dx%d  %s" % (path, w, h, "phone" if phone else ""))
            print("   history explorer cards: %d, canvases: %d" % (p["hx"], p["hxCanvas"]))
            for e in p["errors"]:
                print("   ERROR CARD: " + e)
            for l in logs:
                print("   console: " + l[:300])
            bad += len(p["errors"])
    sys.exit(1 if bad else 0)


asyncio.run(main())
