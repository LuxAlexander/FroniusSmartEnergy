"""Minimaler OCPP-1.6-Client (Charge Point). Nur: verbinden, BootNotification, Heartbeat.

Start:  python client.py [ws://localhost:9000/TESTCP01]
Benötigt: pip install websockets
"""
import asyncio
import json
import sys
import uuid

import websockets

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:9000/TESTCP01"


async def call(ws, action, payload):
    """Sendet ein CALL [2, id, action, payload] und gibt das CALLRESULT-Payload zurück."""
    msg_id = str(uuid.uuid4())
    await ws.send(json.dumps([2, msg_id, action, payload]))
    while True:
        msg = json.loads(await ws.recv())
        if msg[0] == 3 and msg[1] == msg_id:
            return msg[2]
        if msg[0] == 4 and msg[1] == msg_id:
            raise RuntimeError(f"{action} -> CALLERROR {msg[2:]}")
        if msg[0] == 2:  # Anfrage vom Server: noch nicht unterstützt
            await ws.send(json.dumps([4, msg[1], "NotImplemented", "", {}]))


async def main():
    async with websockets.connect(URL, subprotocols=["ocpp1.6"]) as ws:
        res = await call(ws, "BootNotification",
                         {"chargePointVendor": "Test", "chargePointModel": "Minimal"})
        print("BootNotification:", res)
        interval = res.get("interval") or 30
        while True:
            await asyncio.sleep(interval)
            print("Heartbeat:", await call(ws, "Heartbeat", {}))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
