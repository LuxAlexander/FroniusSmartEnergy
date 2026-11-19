"""Wattpilot-Simulator: OCPP-1.6-Client (Charge Point), siehe docs/CLIENT_BAUPLAN.md.

Stand: Schritt 1–3 – Boot, Heartbeat, Status, Start/StopTransaction,
MeterValues mit festem Limit (I_MAX).

Start:  python client.py [ws://localhost:9000/TESTCP01] [Ladedauer in Minuten]
Benötigt: pip install ocpp websockets
"""
import asyncio
import random
import sys
from datetime import datetime, timezone

import websockets
from ocpp.v16 import ChargePoint, call

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:9000/TESTCP01"
MINUTES = float(sys.argv[2]) if len(sys.argv) > 2 else 15
CP_ID = URL.rstrip("/").split("/")[-1]

U = 230.0            # V je Phase (gemessen ca. 226 V)
PHASES = 1           # Testfahrzeug lädt einphasig
I_MIN = 6.0          # A, darunter pausiert die Ladung
I_MAX = 16.0         # A, Box-/Anschlussgrenze
I_CAR = 15.0         # A, mehr zieht das Fahrzeug nicht
TICK = 30            # s, Heartbeat; MeterValues jeden 2. Tick
METER_START = 4110.0 # Wh


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sv(measurand, unit, value, phase=None):
    """Ein sampledValue-Eintrag; Werte als String wie bei der Wattpilot."""
    s = {"measurand": measurand, "value": f"{value:.2f}"}
    if unit:
        s["unit"] = unit
    if phase:
        s["phase"] = phase
    return s


class Wallbox(ChargePoint):
    """Simulierte Wattpilot. Anfragen vom Server (SetChargingProfile, ...) folgen in Schritt 4;
    bis dahin beantwortet das Paket sie mit NotImplemented."""
    limit_a = I_MAX
    energy = METER_START

    def measure(self, dt):
        """Rechnet Ströme/Leistungen, integriert den Zähler über dt Sekunden
        und gibt die 11 sampledValues in der Reihenfolge der echten Box zurück."""
        i_off = min(self.limit_a, I_MAX) if self.limit_a >= I_MIN else 0.0
        i_load = min(i_off, I_CAR)
        i = [max(0.0, i_load + random.gauss(0, 0.02)) if (n < PHASES and i_load) else 0.03
             for n in range(3)]
        p = [U * x if (n < PHASES and i_load) else random.uniform(-0.3, 0.2)
             for n, x in enumerate(i)]
        self.energy += sum(p) * dt / 3600
        return [
            *(sv("Current.Import", "A", x, f"L{n + 1}") for n, x in enumerate(i)),
            sv("Current.Offered", "A", i_off),
            sv("Energy.Active.Import.Register", "Wh", self.energy // 10 * 10),
            sv("Frequency", None, 50 + random.uniform(-0.01, 0.01)),
            sv("Power.Active.Import", "W", sum(p)),
            *(sv("Power.Active.Import", "W", x, f"L{n + 1}") for n, x in enumerate(p)),
            sv("Power.Offered", "W", i_off * 3 * U),
        ]

    async def status(self, connector, status):
        await self.call(call.StatusNotification(
            connector_id=connector, error_code="NoError", status=status, timestamp=now()))

    async def meter_values(self, tid, values):
        await self.call(call.MeterValues(
            connector_id=1, transaction_id=tid,
            meter_value=[{"timestamp": now(), "sampled_value": values}]))


async def simulate(cp):
    res = await cp.call(call.BootNotification(
        charge_point_vendor="fronius", charge_point_model="wattpilot_flex_c6",
        charge_point_serial_number="SIM0001", firmware_version="sim"))
    print("BootNotification:", res.status)
    await cp.status(0, "Available")
    await cp.status(1, "Preparing")
    tid = (await cp.call(call.StartTransaction(
        connector_id=1, id_tag="no-card", meter_start=int(cp.energy), timestamp=now()))).transaction_id
    print("StartTransaction:", tid)
    await cp.status(1, "Charging")
    await cp.meter_values(tid, cp.measure(0))
    try:
        for tick in range(1, int(MINUTES * 60 / TICK) + 1):
            await asyncio.sleep(TICK)
            values = cp.measure(TICK)
            await cp.call(call.Heartbeat())
            if tick % 2 == 0:
                await cp.meter_values(tid, values)
                print(f"MeterValues: {values[6]['value']} W, {values[4]['value']} Wh")
    finally:                                  # auch bei Strg+C
        try:
            await cp.call(call.StopTransaction(
                meter_stop=int(cp.energy), timestamp=now(), transaction_id=tid, reason="Local"))
            await cp.status(1, "Finishing")
            await cp.status(1, "Available")
            print("StopTransaction gesendet")
        except Exception as e:
            print("StopTransaction nicht gesendet:", e)


async def main():
    async with websockets.connect(URL, subprotocols=["ocpp1.6"]) as ws:
        cp = Wallbox(CP_ID, ws)
        reader = asyncio.create_task(cp.start())   # empfängt Antworten und Server-Anfragen
        try:
            await simulate(cp)
        finally:
            reader.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
