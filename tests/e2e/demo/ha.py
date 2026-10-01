"""Home Assistant API helper for the demo scripts.

    python ha.py onboard            create the owner, finish onboarding, keep tokens
    python ha.py wait [SECONDS]     wait until HA reports RUNNING
    python ha.py core-config        time zone, country, units
    python ha.py mqtt HOST PORT     add the MQTT integration through its config flow
    python ha.py resource URL       register a module resource for the dashboards
    python ha.py check-config       run HA's configuration check
    python ha.py settle [SECONDS]   wait until no config entry is still setting up
    python ha.py restart            restart HA and wait for it
    python ha.py ws JSON            send websocket messages, print the results
    python ha.py call DOMAIN SERVICE JSON
    python ha.py states [PREFIX]    print entity_id, state (one per line)

Credentials live in $SECRETS (mode 600): the owner's password, a refresh token
for the browser (cdp-style login) and a long-lived access token for everything
else. None of them is ever passed on a command line.
"""
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import websockets

BASE = os.environ.get("BASE_URL", "http://localhost:8123")
SECRETS = os.environ["SECRETS"]
CLIENT_ID = BASE + "/"


def secret(name):
    with open(os.path.join(SECRETS, name)) as f:
        return f.read().strip()


def keep(name, value):
    path = os.path.join(SECRETS, name)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value + "\n")


def http(method, path, body=None, token=None, form=False, timeout=30):
    data = None
    headers = {}
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode(errors="replace")


def token():
    return secret("token")


def fail(msg):
    print("  FAIL  " + msg, file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- websocket
async def _ws(msgs):
    async with websockets.connect(BASE.replace("http", "ws") + "/api/websocket", max_size=None) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": token()}))
        a = json.loads(await ws.recv())
        if a["type"] != "auth_ok":
            fail("websocket auth: %s" % a)
        out = []
        for i, m in enumerate(msgs, 1):
            m = dict(m, id=i)
            await ws.send(json.dumps(m))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == i and r.get("type") == "result":
                    out.append(r)
                    break
        return out


def ws(msgs, check=True):
    out = asyncio.run(_ws(msgs))
    if check:
        for m, r in zip(msgs, out):
            if not r.get("success"):
                fail("%s: %s" % (m.get("type"), r.get("error")))
    return out


# ---------------------------------------------------------------- commands
def cmd_onboard():
    st, steps = http("GET", "/api/onboarding")
    if st == 404:   # the onboarding views are gone once it is complete
        if not os.path.exists(os.path.join(SECRETS, "token")):
            fail("this Home Assistant is already onboarded, but %s has no token. "
                 "Run down.sh and up.sh again." % SECRETS)
        print("  ok    already onboarded")
        return
    if st != 200:
        fail("GET /api/onboarding: %s %s" % (st, steps))
    done = {s["step"]: s["done"] for s in steps}
    have = os.path.exists(os.path.join(SECRETS, "token"))
    if done.get("user") and not have:
        fail("this Home Assistant is already onboarded, but %s has no token. "
             "Run down.sh and up.sh again." % SECRETS)
    if not done.get("user"):
        st, r = http("POST", "/api/onboarding/users", {
            "client_id": CLIENT_ID, "name": "Demo", "username": "demo",
            "password": secret("ha_password"), "language": "en"})
        if st != 200:
            fail("create owner: %s %s" % (st, r))
        st, t = http("POST", "/auth/token", {"grant_type": "authorization_code",
                     "code": r["auth_code"], "client_id": CLIENT_ID}, form=True)
        if st != 200:
            fail("token exchange: %s %s" % (st, t))
        keep("refresh", t["refresh_token"])
        access = t["access_token"]
        # a long-lived token for the scripts; the refresh token is for the browser
        out = asyncio.run(_ws_with(access, [{"type": "auth/long_lived_access_token",
                                             "client_name": "cf-demo scripts", "lifespan": 3650}]))
        keep("token", out[0]["result"])
        print("  ok    owner 'demo' created; tokens kept in " + SECRETS)
    else:
        print("  ok    owner already created")
    # the onboarding steps want a token tied to the owner's login, not the
    # long-lived one, so take a fresh access token from the refresh token
    st, t = http("POST", "/auth/token", {"grant_type": "refresh_token",
                 "refresh_token": secret("refresh"), "client_id": CLIENT_ID}, form=True)
    if st != 200:
        fail("refresh token: %s %s" % (st, t))
    tok = t["access_token"]
    for step, body in (("core_config", None), ("analytics", None),
                       ("integration", {"client_id": CLIENT_ID, "redirect_uri": BASE + "/?auth_callback=1"})):
        if done.get(step):
            continue
        st, r = http("POST", "/api/onboarding/" + step, body if body is not None else {}, token=tok)
        if st != 200:
            fail("onboarding step %s: %s %s" % (step, st, r))
    print("  ok    onboarding finished")


async def _ws_with(access, msgs):
    async with websockets.connect(BASE.replace("http", "ws") + "/api/websocket", max_size=None) as w:
        await w.recv()
        await w.send(json.dumps({"type": "auth", "access_token": access}))
        await w.recv()
        out = []
        for i, m in enumerate(msgs, 1):
            await w.send(json.dumps(dict(m, id=i)))
            while True:
                r = json.loads(await w.recv())
                if r.get("id") == i and r.get("type") == "result":
                    if not r.get("success"):
                        fail("%s: %s" % (m["type"], r.get("error")))
                    out.append(r)
                    break
        return out


def cmd_wait(secs=240):
    end = time.time() + float(secs)
    last = None
    while time.time() < end:
        try:
            st, r = http("GET", "/api/config", token=token(), timeout=5)
            if st == 200 and r.get("state") == "RUNNING":
                return
            last = (st, r.get("state") if isinstance(r, dict) else r)
        except Exception as e:  # connection refused while restarting
            last = e
        time.sleep(2)
    fail("Home Assistant not RUNNING after %ss (last: %s)" % (secs, last))


def cmd_core_config():
    ws([{"type": "config/core/update", "time_zone": os.environ.get("TZ_NAME", "America/Los_Angeles"),
         "country": "US", "currency": "USD", "unit_system": "us_customary",
         "location_name": "Claude Fleet demo", "language": "en"}])
    print("  ok    time zone %s" % os.environ.get("TZ_NAME"))


def cmd_mqtt(host, port):
    st, entries = http("GET", "/api/config/config_entries/entry", token=token())
    if any(e["domain"] == "mqtt" for e in entries):
        print("  ok    MQTT integration already set up")
        return
    st, r = http("POST", "/api/config/config_entries/flow", {"handler": "mqtt"}, token=token())
    if st != 200:
        fail("start MQTT flow: %s %s" % (st, r))
    if r.get("type") == "menu":
        st, r = http("POST", "/api/config/config_entries/flow/" + r["flow_id"],
                     {"next_step_id": "broker"}, token=token())
    if r.get("step_id") != "broker":
        fail("unexpected MQTT flow step: %s" % r)
    st, r = http("POST", "/api/config/config_entries/flow/" + r["flow_id"], {
        "broker": host, "port": int(port), "protocol": "5", "username": "homeassistant",
        "password": secret("mqtt_ha_password"),
        "other_settings": {"set_client_cert": False, "set_ca_cert": "off", "transport": "tcp"}},
        token=token())
    if r.get("type") != "create_entry":
        fail("MQTT flow did not create an entry: %s" % r)
    print("  ok    MQTT integration added (broker %s:%s)" % (host, port))


def cmd_resource(url):
    have = ws([{"type": "lovelace/resources"}])[0]["result"]
    if any(x["url"] == url for x in have):
        print("  ok    resource %s already registered" % url)
        return
    ws([{"type": "lovelace/resources/create", "res_type": "module", "url": url}])
    print("  ok    resource %s registered" % url)


def cmd_check_config():
    st, r = http("POST", "/api/config/core/check_config", {}, token=token(), timeout=120)
    if st != 200 or r.get("result") != "valid":
        fail("configuration check: %s" % (r,))
    print("  ok    configuration valid")


def cmd_settle(secs=120):
    """Wait until no config entry is still setting up. A restart in the middle of
    one (default_config's Radio Browser looks up its servers online) is logged
    as an ERROR: "Setup of config entry ... cancelled"."""
    end = time.time() + float(secs)
    busy = []
    while time.time() < end:
        st, entries = http("GET", "/api/config/config_entries/entry", token=token())
        busy = [e["title"] for e in entries if e.get("state") == "setup_in_progress"] if st == 200 else ["?"]
        if not busy:
            print("  ok    config entries settled")
            return
        time.sleep(2)
    print("  note  still setting up after %ss: %s" % (secs, ", ".join(busy)))


def cmd_restart():
    try:
        http("POST", "/api/services/homeassistant/restart", {}, token=token(), timeout=10)
    except Exception:
        pass  # the connection drops as it goes down
    time.sleep(8)
    cmd_wait(300)
    print("  ok    restarted")


def cmd_call(domain, service, data):
    st, r = http("POST", "/api/services/%s/%s" % (domain, service), json.loads(data), token=token(), timeout=120)
    if st != 200:
        fail("%s.%s: %s %s" % (domain, service, st, r))


def cmd_states(prefix=""):
    st, r = http("GET", "/api/states", token=token())
    for s in sorted(r, key=lambda s: s["entity_id"]):
        if s["entity_id"].startswith(prefix):
            print(s["entity_id"], s["state"])


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        sys.exit(2)
    c = a[0]
    if c == "onboard":
        cmd_onboard()
    elif c == "wait":
        cmd_wait(*a[1:])
    elif c == "core-config":
        cmd_core_config()
    elif c == "mqtt":
        cmd_mqtt(a[1], a[2])
    elif c == "resource":
        cmd_resource(a[1])
    elif c == "check-config":
        cmd_check_config()
    elif c == "settle":
        cmd_settle(*a[1:])
    elif c == "restart":
        cmd_restart()
    elif c == "ws":
        print(json.dumps(ws(json.loads(a[1]), check=False)))
    elif c == "call":
        cmd_call(a[1], a[2], a[3] if len(a) > 3 else "{}")
    elif c == "states":
        cmd_states(*a[1:])
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
