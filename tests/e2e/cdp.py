import asyncio, json, os, sys, base64, urllib.request, time
WORK=os.environ["WORK"]
def tok():
    rt=open(f"{WORK}/.refresh").read().strip()
    d=urllib.parse.urlencode({"grant_type":"refresh_token","refresh_token":rt,"client_id":"http://localhost:8123/"}).encode()
    r=json.load(urllib.request.urlopen("http://localhost:8123/auth/token",d))
    return {"access_token":r["access_token"],"token_type":"Bearer","expires_in":r["expires_in"],"hassUrl":"http://localhost:8123","clientId":"http://localhost:8123/","expires":int(time.time()*1000)+r["expires_in"]*1000,"refresh_token":rt}
import urllib.parse, websockets
DEEP="""
(() => {
 const out={errors:[],hx:0,hxDefined:!!customElements.get('history-explorer-card'),cards:{}};
 const walk=(root)=>{ for (const el of root.querySelectorAll('*')) {
   const t=el.tagName.toLowerCase();
   if (t==='hui-error-card') out.errors.push((el.shadowRoot?el.shadowRoot.textContent:el.textContent).trim().slice(0,300) + ' | cfg=' + JSON.stringify(el._config||el.config||{}).slice(0,200));
   if (t==='ha-alert' && el.getAttribute('alert-type')==='error') out.errors.push('ha-alert error: '+el.textContent.trim().slice(0,300));
   if (t==='history-explorer-card') out.hx++;
   if (t.startsWith('hui-') && t.endsWith('-card')) out.cards[t]=(out.cards[t]||0)+1;
   if (el.shadowRoot) walk(el.shadowRoot);
 }};
 walk(document); return JSON.stringify(out);
})()
"""
async def main():
    ver=json.load(urllib.request.urlopen("http://127.0.0.1:9333/json/new?about:blank",data=None) if False else urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:9333/json/new?about:blank",method="PUT")))
    async with websockets.connect(ver["webSocketDebuggerUrl"],max_size=None) as ws:
        n=0; logs=[]
        async def call(method,params=None):
            nonlocal n; n+=1; i=n
            await ws.send(json.dumps({"id":i,"method":method,"params":params or {}}))
            while True:
                m=json.loads(await ws.recv())
                if m.get("id")==i: return m.get("result",m)
                handle(m)
        def handle(m):
            meth=m.get("method")
            if meth=="Runtime.consoleAPICalled" and m["params"]["type"] in("error","warning","warn"):
                logs.append(m["params"]["type"]+": "+" ".join(str(a.get("value",a.get("description","")))[:300] for a in m["params"]["args"]))
            elif meth=="Runtime.exceptionThrown":
                d=m["params"]["exceptionDetails"]; logs.append("EXC: "+(d.get("exception",{}).get("description") or d.get("text"))[:400])
            elif meth=="Log.entryAdded" and m["params"]["entry"]["level"] in("error","warning"):
                logs.append("LOG: "+m["params"]["entry"]["text"][:300]+" "+m["params"]["entry"].get("url",""))
        async def pump(sec):
            end=time.time()+sec
            while time.time()<end:
                try: handle(json.loads(await asyncio.wait_for(ws.recv(),timeout=0.5)))
                except asyncio.TimeoutError: pass
        for d in ("Runtime.enable","Log.enable","Page.enable"): await call(d)
        await call("Emulation.setDeviceMetricsOverride",{"width":1600,"height":2600,"deviceScaleFactor":1,"mobile":False})
        await call("Page.navigate",{"url":"http://localhost:8123/"}); await pump(3)
        await call("Runtime.evaluate",{"expression":f"localStorage.setItem('hassTokens', JSON.stringify({json.dumps(tok())}))"})
        for view in sys.argv[1:]:
            logs.clear()
            await call("Page.navigate",{"url":f"http://localhost:8123/claude-fleet/{view}"}); await pump(12)
            r=await call("Runtime.evaluate",{"expression":DEEP,"returnByValue":True})
            print("=====",view); print(r["result"]["value"]); print("console:",json.dumps(logs,indent=0)[:3000])
            s=await call("Page.captureScreenshot",{"format":"png","captureBeyondViewport":False})
            open(f"{WORK}/shot_{view}.png","wb").write(base64.b64decode(s["data"]))
asyncio.run(main())
