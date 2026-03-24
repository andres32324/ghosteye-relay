import asyncio
import aiohttp
from aiohttp import web
import random
import string
import os

clients = {}   # code -> ghosteye ws
waiters = {}   # code -> future para specter
watchers = {}  # code -> set de ws en modo WATCH

def gen_code():
    while True:
        code = ''.join(random.choices(string.digits, k=4))
        if code not in clients:
            return code

async def handle(request):
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    try:
        msg = await asyncio.wait_for(ws.receive(), timeout=10)
        role = msg.data

        # ── GHOSTEYE 2 emisor ──
        if role == "EMIT" or role.startswith("EMIT:"):
            if role.startswith("EMIT:"):
                code = role[5:].strip()
                if not code.isdigit() or len(code) != 4:
                    code = gen_code()
            else:
                code = gen_code()
            clients.pop(code, None)
            if code in waiters and not waiters[code].done():
                waiters[code].cancel()
            clients[code] = ws
            waiters[code] = asyncio.get_event_loop().create_future()
            if code not in watchers:
                watchers[code] = set()
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
                    try: await specter_ws.send_bytes(msg.data)
                    except Exception: break
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    try: await specter_ws.send_str(msg.data)
                    except Exception: break
                    if msg.data.startswith("INFO|"):
                        dead = set()
                        for watcher in watchers.get(code, set()):
                            try: await watcher.send_str(msg.data)
                            except Exception: dead.add(watcher)
                        watchers.get(code, set()).difference_update(dead)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

        # ── SPECTER oyente con audio ──
        elif role.startswith("JOIN:"):
            code = role[5:].strip()
            if code not in clients:
                await ws.send_str("ERROR:INVALID_CODE")
                return ws
            await ws.send_str("OK")
            if code in waiters and not waiters[code].done():
                waiters[code].set_result(ws)
            ghosteye_ws = clients.get(code)
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    ghosteye_ws = clients.get(code)
                    if ghosteye_ws:
                        try: await ghosteye_ws.send_str(msg.data)
                        except Exception: pass
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

        # ── SPECTER solo info (WATCH) ──
        elif role.startswith("WATCH:"):
            code = role[6:].strip()
            if code not in clients:
                await ws.send_str("ERROR:INVALID_CODE")
                return ws
            await ws.send_str("WATCHING")
            if code not in watchers:
                watchers[code] = set()
            watchers[code].add(ws)
            ghosteye_ws = clients.get(code)
            if ghosteye_ws:
                try: await ghosteye_ws.send_str("GET_INFO")
                except Exception: pass
            async for msg in ws:
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
            watchers.get(code, set()).discard(ws)

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
