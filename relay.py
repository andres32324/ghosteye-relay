import asyncio
import aiohttp
from aiohttp import web
import random
import string
import os

clients = {}
waiters = {}

def gen_code():
    while True:
        code = ''.join(random.choices(string.digits, k=6))
        if code not in clients:
            return code

async def handle(request):
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)

    try:
        role = await asyncio.wait_for(ws.receive_str(), timeout=10)

        if role == "EMIT" or role.startswith("EMIT:"):
            if role.startswith("EMIT:"):
                code = role[5:].strip()
                if not code.isdigit() or len(code) != 6:
                    code = gen_code()
            else:
                code = gen_code()

            clients.pop(code, None)
            if code in waiters and not waiters[code].done():
                waiters[code].cancel()

            clients[code] = ws
            waiters[code] = asyncio.get_event_loop().create_future()
            await ws.send_str(f"CODE:{code}")

            try:
                specter_ws = await asyncio.wait_for(waiters[code], timeout=300)
            except asyncio.TimeoutError:
                clients.pop(code, None)
                waiters.pop(code, None)
                return ws

            await ws.send_str("READY")

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    try:
                        await specter_ws.send_bytes(msg.data)
                    except Exception:
                        break
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        await specter_ws.send_str(msg.data)
                    except Exception:
                        break
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

        elif role.startswith("JOIN:"):
            code = role[5:].strip()
            if code not in clients:
                await ws.send_str("ERROR:INVALID_CODE")
                return ws

            await ws.send_str("OK")
            if code in waiters and not waiters[code].done():
                waiters[code].set_result(ws)

            async for msg in ws:
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

    except Exception as e:
        print(f"Error: {e}")
    finally:
        clients.pop(next((k for k,v in clients.items() if v==ws), None), None)

    return ws

async def main():
    port = int(os.environ.get("PORT", 8765))
    app = web.Application()
    app.router.add_get("/", handle)
    app.router.add_get("/ws", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Relay corriendo en puerto {port}")
    await asyncio.Future()

asyncio.run(main())
```

Y cambia el `requirements.txt` a:
```
aiohttp
