import asyncio
import random
import string

clients = {}  # code -> emisor writer
waiters = {}  # code -> future

def gen_code():
    while True:
        code = ''.join(random.choices(string.digits, k=6))
        if code not in clients:
            return code

async def handle(reader, writer):
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=10)
        role = line.decode().strip()

        if role == "EMIT":
            code = gen_code()
            clients[code] = writer
            waiters[code] = asyncio.get_event_loop().create_future()

            writer.write(f"CODE:{code}\n".encode())
            await writer.drain()

            # Esperar que Specter se conecte (máx 5 minutos)
            try:
                specter_reader = await asyncio.wait_for(waiters[code], timeout=300)
            except asyncio.TimeoutError:
                clients.pop(code, None)
                waiters.pop(code, None)
                writer.close()
                return

            writer.write("READY\n".encode())
            await writer.drain()

            # Relay de audio: Specter → GhostEye (control) y GhostEye → Specter (audio)
            await relay_audio(specter_reader, writer)

        elif role.startswith("JOIN:"):
            code = role[5:].strip()
            if code not in clients:
                writer.write("ERROR:INVALID_CODE\n".encode())
                await writer.drain()
                writer.close()
                return

            writer.write("OK\n".encode())
            await writer.drain()

            # Resolver el future del emisor
            if code in waiters and not waiters[code].done():
                waiters[code].set_result(reader)

            # Mantener conexión abierta
            await reader.read(1)

    except Exception as e:
        print(f"Error: {e}")
    finally:
        writer.close()

async def relay_audio(source, dest):
    try:
        while True:
            data = await source.read(4096)
            if not data:
                break
            dest.write(data)
            await dest.drain()
    except Exception:
        pass

async def main():
    port = 8765
    server = await asyncio.start_server(handle, '0.0.0.0', port)
    print(f"Relay corriendo en puerto {port}")
    async with server:
        await server.serve_forever()

asyncio.run(main())
