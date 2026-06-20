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
                    if len(msg.data) < 1:
                        continue
                    # Primer byte: 0=video, 1=audio
                    msg_type = msg.data[0]
                    payload = bytes(msg.data[1:])

                    listeners_lyn = lyn_listeners.get(code, {})
                    if msg_type == 0:
                        video_ws = listeners_lyn.get("video")
                        if video_ws and not video_ws.closed:
                            await safe_send_bytes(video_ws, payload)
                    elif msg_type == 1:
                        audio_ws = listeners_lyn.get("audio")
                        if audio_ws and not audio_ws.closed:
                            await safe_send_bytes(audio_ws, payload)

                elif msg.type == aiohttp.WSMsgType.TEXT:
                    # Comandos de control
                    listeners_lyn = lyn_listeners.get(code, {})
                    for ws_listener in listeners_lyn.values():
                        if ws_listener and not ws_listener.closed:
                            await safe_send_str(ws_listener, msg.data)

                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

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

            lyn_emitter = lyn_emitters.get(code)
            if lyn_emitter and not lyn_emitter.closed:
                await safe_send_str(lyn_emitter, "PULSO_READY")

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    # Comandos hacia Lyn (switch cam, etc)
                    lyn_emitter = lyn_emitters.get(code)
                    if lyn_emitter and not lyn_emitter.closed:
                        await safe_send_str(lyn_emitter, msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

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
