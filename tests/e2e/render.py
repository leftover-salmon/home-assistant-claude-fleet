import asyncio, json, sys, os, websockets
async def main():
    tok=open(os.environ["TOKFILE"]).read().strip()
    async with websockets.connect("ws://localhost:8123/api/websocket", max_size=None) as ws:
        await ws.recv(); await ws.send(json.dumps({"type":"auth","access_token":tok})); await ws.recv()
        n=1
        async def call(m):
            nonlocal n; m["id"]=n; n+=1; await ws.send(json.dumps(m))
            while True:
                r=json.loads(await ws.recv())
                if r.get("id")==m["id"] and r.get("type")=="result": return r
        cfg=await call({"type":"lovelace/config","url_path":"claude-fleet"})
        if not cfg["success"]: print("CONFIG FAIL",cfg); return
        conf=cfg["result"]
        json.dump(conf,open(os.environ["OUT"],"w"),indent=1)
        cards=[]
        def walk(o,path):
            if isinstance(o,dict):
                if o.get("type")=="markdown": cards.append((path,o))
                for k,v in o.items(): walk(v,path+"/"+str(o.get("title") or k) if k in("views",) else path+"/"+k)
            elif isinstance(o,list):
                for i,v in enumerate(o): walk(v,f"{path}[{i}]")
        walk(conf,"")
        types={}
        def cnt(o):
            if isinstance(o,dict):
                if "type" in o and isinstance(o["type"],str): types[o["type"]]=types.get(o["type"],0)+1
                for v in o.values(): cnt(v)
            elif isinstance(o,list):
                for v in o: cnt(v)
        cnt(conf); print("TYPES",types); print("VIEWS",[v.get("title") for v in conf["views"]])
        for path,c in cards:
            tpl=c.get("content","")
            m={"type":"render_template","template":tpl,"strict":True,"report_errors":True}
            if c.get("entity_ids"): m["entity_ids"]=c["entity_ids"]
            m["id"]=n; n+=1; await ws.send(json.dumps(m))
            res=None; ev=None
            while True:
                r=json.loads(await ws.recv())
                if r.get("id")!=m["id"]: continue
                if r["type"]=="result":
                    if not r["success"]: res=("ERR",r["error"]); break
                    continue
                if r["type"]=="event": ev=r["event"]; break
            await call({"type":"unsubscribe_events","subscription":m["id"]}) if ev else None
            print("="*20, path, "| title:", c.get("title"), "| visibility:", json.dumps(c.get("visibility"))[:150])
            if res: print("  ERROR:",res[1])
            elif "error" in ev: print("  TEMPLATE ERROR:",ev.get("level"),ev["error"])
            else: print(ev["result"][:3000])
asyncio.run(main())
