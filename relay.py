import asyncio
import aiohttp
from aiohttp import web
import random
import string
import os
import time

emitters  = {}
listeners = {}
watchers  = {}
last_info = {}

# ── Registro Lyn ──
lyn_registry = {}      # {code: {ip, localIp, timestamp}}
lyn_emitters = {}      # {code: ws}  — Lyn conectado por WS
lyn_listeners = {}     # {code: {video: ws, audio: ws}}

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
    if role == "EMIT":
        if emitters.get(code) is ws:
            emitters.pop(code, None)
        listener = listeners.get(code)
        if listener and not listener.closed:
            await safe_send_str(listener, "EMITTER_GONE")

    elif role == "JOIN":
        if listeners.get(code) is ws:
            listeners.pop(code, None)
        emitter = emitters.get(code)
        if emitter and not emitter.closed:
            await safe_send_str(emitter, "STOP")

    elif role == "WATCH":
        if code in watchers:
            watchers[code].discard(ws)

    elif role == "LYN_EMIT":
        if lyn_emitters.get(code) is ws:
            lyn_emitters.pop(code, None)
        # Notificar a Pulso que Lyn se desconectó
        listeners_lyn = lyn_listeners.get(code, {})
        for ws_listener in listeners_lyn.values():
            if ws_listener and not ws_listener.closed:
                await safe_send_str(ws_listener, "LYN_GONE")

    elif role == "LYN_VIDEO":
        listeners_lyn = lyn_listeners.get(code, {})
        if listeners_lyn.get("video") is ws:
            listeners_lyn.pop("video", None)

    elif role == "LYN_AUDIO":
        listeners_lyn = lyn_listeners.get(code, {})
        if listeners_lyn.get("audio") is ws:
            listeners_lyn.pop("audio", None)

async def handle_ping(request):
    return web.Response(text="GhostEye Relay OK", status=200)

# ── Lyn registra su IP (HTTP) ──
async def handle_lyn_register(request):
    try:
        data = await request.json()
        code = data.get("code", "").strip().upper()
        ip = data.get("ip", "").strip()
        local_ip = data.get("localIp", "").strip()

        if not code or not ip:
            return web.json_response({"error": "code e ip requeridos"}, status=400)

        lyn_registry[code] = {
            "ip": ip,
            "localIp": local_ip,
            "timestamp": time.time()
        }
        print(f"Lyn registrado: code={code} ip={ip} localIp={local_ip}")
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

# ── Pulso consulta IP por código (HTTP) ──
async def handle_lyn_lookup(request):
    code = request.match_info.get("code", "").strip().upper()
    if not code:
        return web.json_response({"error": "código requerido"}, status=400)

    entry = lyn_registry.get(code)
    if not entry:
        return web.json_response({"error": "código no encontrado"}, status=404)

    age = time.time() - entry["timestamp"]
    if age > 86400:
        lyn_registry.pop(code, None)
        return web.json_response({"error": "código expirado"}, status=404)

    return web.json_response({
        "ok": True,
        "ip": entry["ip"],
        "localIp": entry.get("localIp", ""),
        "code": code
    })

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

# ── WebSocket Specter/GhostEye (sin cambios) ──
async def handle(request):
    ws = web.WebSocketResponse(heartbeat=15)
    await ws.prepare(request)
    code = None
    role = None

    try:
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=15)
        except asyncio.TimeoutError:
            await ws.close()
            return ws

        if msg.type != aiohttp.WSMsgType.TEXT:
            await ws.close()
            return ws

        text = msg.data.strip()

        if text == "EMIT" or text.startswith("EMIT:"):
            if text.startswith("EMIT:"):
                code = text[5:].strip()
                if not code.isdigit() or len(code) != 4:
                    code = gen_code()
            else:
                code = gen_code()

            role = "EMIT"
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

        elif text.startswith("JOIN:"):
            code = text[5:].strip()
            if code not in emitters:
                await safe_send_str(ws, "ERROR:INVALID_CODE")
                await ws.close()
                return ws

            role = "JOIN"
            old = listeners.get(code)
            if old and not old.closed:
                try:
                    await old.close()
                except Exception:
                    pass

            listeners[code] = ws
            await safe_send_str(ws, "OK")

            emitter = emitters.get(code)
            if emitter and not emitter.closed:
                await safe_send_str(emitter, "READY")

            if code in last_info:
                await safe_send_str(ws, last_info[code])

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    emitter = emitters.get(code)
                    if emitter and not emitter.closed:
                        await safe_send_str(emitter, msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

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
        if code and role:
            await cleanup_code(code, role, ws)
        if not ws.closed:
            try:
                await ws.close()
            except Exception:
                pass

    return ws

# ── WebSocket Lyn/Pulso ──
async def handle_lyn(request):
    ws = web.WebSocketResponse(heartbeat=15)
    await ws.prepare(request)
    code = None
    role = None

    try:
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=15)
        except asyncio.TimeoutError:
            await ws.close()
            return ws

        if msg.type != aiohttp.WSMsgType.TEXT:
            await ws.close()
            return ws

        text = msg.data.strip()

        # ── Lyn emisor ──
        if text.startswith("LYN_EMIT:"):
            code = text[9:].strip().upper()
            role = "LYN_EMIT"

            old = lyn_emitters.get(code)
            if old and not old.closed:
                try:
                    await old.close()
                except Exception:
                    pass

            lyn_emitters[code] = ws
            if code not in lyn_listeners:
                lyn_listeners[code] = {}

            await safe_send_str(ws, "READY")
            print(f"Lyn emisor conectado: {code}")

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    # Primero byte indica tipo: 0=video, 1=audio
                    if len(msg.data) < 1:
                        continue
                    msg_type = msg.data[0]
                    payload = msg.data[1:]

                    listeners_lyn = lyn_listeners.get(code, {})
                    if msg_type == 0:  # Video
                        video_ws = listeners_lyn.get("video")
                        if video_ws and not video_ws.closed:
                            await safe_send_bytes(video_ws, payload)
                    elif msg_type == 1:  # Audio
                        audio_ws = listeners_lyn.get("audio")
                        if audio_ws and not audio_ws.closed:
                            await safe_send_bytes(audio_ws, payload)

                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

        # ── Pulso receptor video ──
        elif text.startswith("PULSO_VIDEO:"):
            code = text[12:].strip().upper()
            role = "LYN_VIDEO"

            if code not in lyn_listeners:
                lyn_listeners[code] = {}

            old = lyn_listeners[code].get("video")
            if old and not old.closed:
                try:
                    await old.close()
                except Exception:
                    pass

            lyn_listeners[code]["video"] = ws
            await safe_send_str(ws, "OK")
            print(f"Pulso video conectado: {code}")

            # Notificar a Lyn que Pulso está listo
            lyn_emitter = lyn_emitters.get(code)
            if lyn_emitter and not lyn_emitter.closed:
                await safe_send_str(lyn_emitter, "PULSO_READY")

            async for msg in ws:
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

        # ── Pulso receptor audio ──
        elif text.startswith("PULSO_AUDIO:"):
            code = text[12:].strip().upper()
            role = "LYN_AUDIO"

            if code not in lyn_listeners:
                lyn_listeners[code] = {}

            old = lyn_listeners[code].get("audio")
            if old and not old.closed:
                try:
                    await old.close()
                except Exception:
                    pass

            lyn_listeners[code]["audio"] = ws
            await safe_send_str(ws, "OK")
            print(f"Pulso audio conectado: {code}")

            async for msg in ws:
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

        else:
            await ws.close()
            return ws

    except Exception as e:
        print(f"Error handle_lyn [{role}][{code}]: {e}")
    finally:
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
    app = web.Application(client_max_size=5*1024*1024)  # 5MB max para frames
    app.router.add_get("/", handle)
    app.router.add_get("/ws", handle)
    app.router.add_get("/ping", handle_ping)
    app.router.add_get("/lyn", handle_lyn)
    app.router.add_post("/lyn/register", handle_lyn_register)
    app.router.add_get("/lyn/lookup/{code}", handle_lyn_lookup)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Relay PRO v3 + Lyn corriendo en puerto {port}")
    asyncio.ensure_future(self_ping(app))
    await asyncio.Future()

asyncio.run(main())
