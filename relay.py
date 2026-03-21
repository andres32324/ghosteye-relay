import asyncio
import websockets
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

async def handle(websocket, path):
    try:
        role = await asyncio.wait_for(websocket.recv(), timeout=10)

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

            clients[code] = websocket
            waiters[code] = asyncio.get_event_loop().create_future()

            await websocket.send(f"CODE:{code}")

            try:
                specter_ws = await asyncio.wait_for(waiters[code], timeout=300)
            except asyncio.TimeoutError:
                clients.pop(code, None)
                waiters.pop(code, None)
                return

            await websocket.send("READY")

            async for data in websocket:
                try:
                    await specter_ws.send(data)
                except Exception:
                    break

        elif role.startswith("JOIN:"):
            code = role[5:].strip()
            if code not in clients:
                await websocket.send("ERROR:INVALID_CODE")
                return

            await websocket.send("OK")

            if code in waiters and not waiters[code].done():
                waiters[code].set_result(websocket)

            await websocket.wait_closed()

    except Exception as e:
        print(f"Error: {e}")
    finally:
        clients.pop(next((k for k,v in clients.items() if v==websocket), None), None)

start_server = websockets.serve(
    handle, "0.0.0.0", int(os.environ.get("PORT", 8765)),
    ping_interval=None,
    ping_timeout=None
)

print(f"Relay corriendo en puerto {os.environ.get('PORT', 8765)}")
asyncio.get_event_loop().run_until_complete(start_server)
asyncio.get_event_loop().run_forever()
