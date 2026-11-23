"""Wattpilot-Simulator: OCPP-1.6-Client (Charge Point), siehe docs/CLIENT_BAUPLAN.md.

Stand: Schritt 1–4 – Boot, Heartbeat, Status, Start/StopTransaction, MeterValues,
Ladelimit per SetChargingProfile / ClearChargingProfile vom Server.

Start:  python client.py [ws://localhost:9000/TESTCP01] [Ladedauer in Minuten]
Benötigt: pip install ocpp websockets
"""
import asyncio
import random
import sys
from datetime import datetime, timezone

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint, call, call_result
from ocpp.v16.enums import (Action, ChargingProfilePurposeType, ChargingProfileStatus,
                            ChargingRateUnitType, ClearChargingProfileStatus)

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
    """Simulierte Wattpilot. Nimmt ein Ladelimit vom Server an; andere Anfragen
    beantwortet das Paket mit NotImplemented."""
    limit_a = I_MAX      # gilt, solange kein Profil gesetzt ist
    i_off = I_MAX        # angebotener Strom der letzten Messung
    energy = METER_START

    @on(Action.set_charging_profile)
    async def on_set_charging_profile(self, connector_id, cs_charging_profiles, **kwargs):
        """Übernimmt die erste Periode des Profils als Limit. Mehrere Perioden,
        stackLevel und Gültigkeitszeiten werden nicht ausgewertet."""
        profile = cs_charging_profiles
        # Bis zum Test an der echten Wattpilot (Bauplan, Prüfliste Nr. 2): Max-Profil ablehnen
        if profile["charging_profile_purpose"] == ChargingProfilePurposeType.charge_point_max_profile:
            print("SetChargingProfile abgelehnt:", profile["charging_profile_purpose"])
            return call_result.SetChargingProfile(status=ChargingProfileStatus.rejected)
        schedule = profile["charging_schedule"]
        limit = float(schedule["charging_schedule_period"][0]["limit"])
        if schedule["charging_rate_unit"] == ChargingRateUnitType.watts:
            limit /= U * PHASES
        self.limit_a = limit
        print(f"SetChargingProfile: Limit {limit:.1f} A ({profile['charging_profile_purpose']})")
        return call_result.SetChargingProfile(status=ChargingProfileStatus.accepted)

    @on(Action.get_configuration)
    async def on_get_configuration(self, key=None, **kwargs):
        """Meldet die Parameter dieses Simulators (nicht die der echten Wattpilot)."""
        values = {
            "NumberOfConnectors": "1",
            "SupportedFeatureProfiles": "Core,SmartCharging",
            "HeartbeatInterval": str(TICK),
            "MeterValueSampleInterval": str(TICK * 2),
            "MeterValuesSampledData": "Current.Import,Current.Offered,Energy.Active.Import.Register,"
                                      "Frequency,Power.Active.Import,Power.Offered",
            "ChargingScheduleAllowedChargingRateUnit": "Current,Power",
            "ChargingScheduleMaxPeriods": "1",
        }
        requested = key or list(values)
        return call_result.GetConfiguration(
            configuration_key=[{"key": k, "readonly": True, "value": values[k]} for k in requested if k in values],
            unknown_key=[k for k in requested if k not in values])

    @on(Action.clear_charging_profile)
    async def on_clear_charging_profile(self, **kwargs):
        self.limit_a = I_MAX
        print("ClearChargingProfile: Limit zurück auf", I_MAX, "A")
        return call_result.ClearChargingProfile(status=ClearChargingProfileStatus.accepted)

    def measure(self, dt):
        """Rechnet Ströme/Leistungen, integriert den Zähler über dt Sekunden
        und gibt die 11 sampledValues in der Reihenfolge der echten Box zurück."""
        self.i_off = i_off = min(self.limit_a, I_MAX) if self.limit_a >= I_MIN else 0.0
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
    state = "Charging"
    try:
        for tick in range(1, int(MINUTES * 60 / TICK) + 1):
            await asyncio.sleep(TICK)
            values = cp.measure(TICK)
            await cp.call(call.Heartbeat())
            new_state = "Charging" if cp.i_off > 0 else "SuspendedEVSE"   # Limit < I_MIN -> Pause
            if new_state != state:
                state = new_state
                await cp.status(1, state)
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
