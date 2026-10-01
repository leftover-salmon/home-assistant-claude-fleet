import asyncio, json, sys, os, websockets
async def main():
    tok=open(os.environ["TOKFILE"]).read().strip()
    msgs=json.loads(sys.argv[1])
    async with websockets.connect("ws://localhost:8123/api/websocket", max_size=None) as ws:
        await ws.recv(); await ws.send(json.dumps({"type":"auth","access_token":tok})); a=json.loads(await ws.recv())
        if a["type"]!="auth_ok": print(a); return
        out=[]
        for i,m in enumerate(msgs,1):
            m["id"]=i; await ws.send(json.dumps(m))
            while True:
                r=json.loads(await ws.recv())
                if r.get("id")==i and r.get("type")=="result": out.append(r); break
        print(json.dumps(out))
asyncio.run(main())
