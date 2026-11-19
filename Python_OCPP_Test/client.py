"""Wattpilot-Simulator: OCPP-1.6-Client (Charge Point), siehe docs/CLIENT_BAUPLAN.md.

Unterstützt ein einfaches TxDefaultProfile in A: 0 A oder 6–16 A.

Start:  python client.py [ws://localhost:9000/TESTCP01] [Ladedauer in Minuten]
Benötigt: pip install ocpp websockets
"""
import asyncio
import argparse
import math
import random
import time
from contextlib import suppress
from datetime import datetime, timezone

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint, call, call_result
from ocpp.v16.enums import Action

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
    """Bewusst begrenztes Testmodell; keine Zusage über reale Gerätefähigkeiten."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.limit_a = I_MAX
        self.energy = METER_START
        self.changed = asyncio.Event()
        self.last_measurement = time.monotonic()
        self.active = False

    def advance_energy(self):
        now_s = time.monotonic()
        if self.active:
            self.energy += U * PHASES * min(self.limit_a, I_CAR) * (now_s - self.last_measurement) / 3600
        self.last_measurement = now_s

    @on(Action.set_charging_profile)
    async def on_set_profile(self, connector_id, cs_charging_profiles, **kwargs):
        profile = cs_charging_profiles
        schedule = profile.get("charging_schedule", {})
        periods = schedule.get("charging_schedule_period", [])
        limit = periods[0].get("limit", -1) if len(periods) == 1 else -1
        supported = (
            connector_id == 1
            and profile.get("charging_profile_purpose") == "TxDefaultProfile"
            and profile.get("charging_profile_kind") == "Absolute"
            and profile.get("stack_level") == 0
            and not any(k in profile for k in ("valid_from", "valid_to", "transaction_id"))
            and schedule.get("charging_rate_unit") == "A"
            and not any(k in schedule for k in ("start_schedule", "duration", "min_charging_rate"))
            and len(periods) == 1 and periods[0].get("start_period") == 0
            and "number_phases" not in periods[0]
            and math.isfinite(limit) and (limit == 0 or I_MIN <= limit <= I_MAX)
        )
        if not supported:
            return call_result.SetChargingProfile(status="Rejected")
        self.advance_energy()  # Vergangene Zeit gehört noch zur alten Grenze.
        self.limit_a = float(limit)
        self.changed.set()
        return call_result.SetChargingProfile(status="Accepted")

    def measure(self):
        """Elf Messwerte; Energie aus idealer Leistung und real verstrichener Zeit."""
        i_off = min(self.limit_a, I_MAX) if self.limit_a >= I_MIN else 0.0
        i_load = min(i_off, I_CAR)
        i = [max(0.0, min(i_load, i_load + random.gauss(0, 0.02))) if (n < PHASES and i_load) else 0.0
             for n in range(3)]
        p = [U * x for x in i]
        self.advance_energy()
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


async def simulate(cp, minutes=15, tick_seconds=TICK):
    res = await cp.call(call.BootNotification(
        charge_point_vendor="fronius", charge_point_model="wattpilot_flex_c6",
        charge_point_serial_number="SIM0001", firmware_version="sim"))
    print("BootNotification:", res.status)
    await cp.status(0, "Available")
    await cp.status(1, "Preparing")
    tid = (await cp.call(call.StartTransaction(
        connector_id=1, id_tag="no-card", meter_start=int(cp.energy), timestamp=now()))).transaction_id
    print("StartTransaction:", tid)
    cp.active = True
    cp.last_measurement = time.monotonic()
    await cp.status(1, "Charging")
    await cp.meter_values(tid, cp.measure())
    current_status = "Charging"
    end = time.monotonic() + minutes * 60
    next_tick = time.monotonic() + tick_seconds
    tick = 0
    try:
        while time.monotonic() < end:
            try:
                await asyncio.wait_for(cp.changed.wait(), max(0, min(next_tick, end) - time.monotonic()))
            except asyncio.TimeoutError:
                pass
            changed = cp.changed.is_set()
            cp.changed.clear()
            if time.monotonic() >= end:
                break
            heartbeat_due = time.monotonic() >= next_tick
            if heartbeat_due:
                tick += 1
                next_tick = time.monotonic() + tick_seconds
                await cp.call(call.Heartbeat())
            status = "SuspendedEVSE" if cp.limit_a == 0 else "Charging"
            if status != current_status:
                await cp.status(1, status)
                current_status = status
            if changed or (heartbeat_due and tick % 2 == 0):
                values = cp.measure()
                await cp.meter_values(tid, values)
                print(f"MeterValues: {values[6]['value']} W, {values[4]['value']} Wh")
    finally:                                  # auch bei Strg+C
        try:
            cp.advance_energy()
            cp.active = False
            await cp.call(call.StopTransaction(
                meter_stop=int(cp.energy), timestamp=now(), transaction_id=tid, reason="Local"))
            await cp.status(1, "Finishing")
            await cp.status(1, "Available")
            print("StopTransaction gesendet")
        except Exception as e:
            print("StopTransaction nicht gesendet:", e)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", default="ws://localhost:9000/TESTCP01")
    parser.add_argument("minutes", nargs="?", type=float, default=15)
    parser.add_argument("--tick-seconds", type=float, default=TICK)
    args = parser.parse_args()
    if not all(math.isfinite(x) and x > 0 for x in (args.minutes, args.tick_seconds)):
        parser.error("Dauer und Tick müssen endlich und positiv sein.")
    async with websockets.connect(args.url, subprotocols=["ocpp1.6"]) as ws:
        cp = Wallbox(args.url.rstrip("/").split("/")[-1], ws)
        reader = asyncio.create_task(cp.start())   # empfängt Antworten und Server-Anfragen
        try:
            await simulate(cp, args.minutes, args.tick_seconds)
        finally:
            reader.cancel()
            with suppress(asyncio.CancelledError):
                await reader


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
