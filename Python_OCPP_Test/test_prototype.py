"""Tests über echte lokale WebSockets und OCPP-Schema-Prüfung, ohne Hardware."""
import asyncio
import copy
import csv
import logging
import tempfile
import unittest
from contextlib import suppress
from pathlib import Path
from unittest.mock import patch

import websockets
from ocpp.v16 import call

import OCPP as server
from client import Wallbox, simulate

logging.getLogger().setLevel(logging.WARNING)


class PrototypeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.csv_path = Path(self.temp.name) / "test.csv"
        self.patches = [patch.object(server, "LOG_DIR", self.temp.name),
                        patch.object(server, "CSV_FILE", str(self.csv_path))]
        for p in self.patches:
            p.start()
        server._ensure_csv_header()
        self.connected = asyncio.get_running_loop().create_future()

        async def accept(ws):
            cp = server.WattpilotChargePoint("TEST", ws)
            cp.demo_enabled = True
            self.connected.set_result(cp)
            try:
                await cp.start()
            except websockets.exceptions.ConnectionClosed:
                pass

        self.listener = await websockets.serve(accept, "127.0.0.1", 0, subprotocols=["ocpp1.6"])
        port = self.listener.sockets[0].getsockname()[1]
        self.ws = await websockets.connect(f"ws://127.0.0.1:{port}/TEST", subprotocols=["ocpp1.6"])
        self.box = Wallbox("TEST", self.ws)
        self.reader = asyncio.create_task(self.box.start())
        self.central = await self.connected

    async def asyncTearDown(self):
        self.reader.cancel()
        with suppress(asyncio.CancelledError):
            await self.reader
        await self.ws.close()
        self.listener.close()
        await self.listener.wait_closed()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def rows(self):
        with self.csv_path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    async def test_existing_lifecycle(self):
        await asyncio.wait_for(simulate(self.box, minutes=0.006, tick_seconds=0.05), 5)
        rows = self.rows()
        messages = [r["message_type"] for r in rows]
        for expected in ("BootNotification", "Heartbeat", "StartTransaction", "MeterValues", "StopTransaction"):
            self.assertIn(expected, messages)
        start = next(r for r in rows if r["message_type"] == "StartTransaction")
        stop = next(r for r in rows if r["message_type"] == "StopTransaction")
        self.assertEqual(start["transaction_id"], stop["transaction_id"])
        self.assertGreaterEqual(float(stop["meter_value"]), float(start["meter_value"]))
        self.assertEqual(rows[-1]["status"], "Available")

    async def test_demo_controls_power_pause_and_resume(self):
        simulation = asyncio.create_task(simulate(self.box, minutes=0.025, tick_seconds=0.05))
        try:
            await asyncio.wait_for(self.central.run_demo(0.05), 5)
            await asyncio.wait_for(simulation, 5)
        finally:
            simulation.cancel()
            await asyncio.gather(simulation, return_exceptions=True)
        rows = self.rows()
        measured = [r for r in rows if r["message_type"] == "DemoMeasurement"]
        self.assertEqual([float(r["requested_limit_a"]) for r in measured], [16, 10, 0, 6])
        for row, expected in zip(measured, (3450, 2300, 0, 1380)):
            self.assertAlmostEqual(float(row["meter_value"]), expected, delta=25)
        statuses = [r["status"] for r in rows if r["message_type"] == "StatusNotification"]
        self.assertIn("SuspendedEVSE", statuses)
        self.assertIn("Charging", statuses[statuses.index("SuspendedEVSE") + 1:])
        currents = [r for r in rows if r["measurand"] == "Current.Import" and r["phase"] == "L1"]
        self.assertTrue(currents)
        self.assertEqual(len([r for r in rows if r["message_type"] == "SetChargingProfileResponse"
                              and r["status"] == "Accepted"]), 4)

    async def test_invalid_or_unsupported_profiles_leave_limit_unchanged(self):
        await self.central.set_limit(10)
        for limit in (-1, 3, 5.9, 16.1, 32):
            with self.subTest(limit=limit):
                self.assertEqual((await self.central.set_limit(limit)).status, "Rejected")
                self.assertEqual(self.box.limit_a, 10)
        base = {"chargingProfileId": 2, "stackLevel": 0,
                "chargingProfilePurpose": "TxDefaultProfile", "chargingProfileKind": "Absolute",
                "chargingSchedule": {"chargingRateUnit": "A",
                                     "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 6}]}}
        variants = []
        profile = copy.deepcopy(base)
        profile["chargingSchedule"]["chargingRateUnit"] = "W"
        variants.append((1, profile))
        profile = copy.deepcopy(base)
        profile["chargingSchedule"]["chargingSchedulePeriod"].append({"startPeriod": 60, "limit": 10})
        variants.extend(((1, profile), (0, base)))
        for connector, profile in variants:
            response = await self.central.call(call.SetChargingProfile(
                connector_id=connector, cs_charging_profiles=profile))
            self.assertEqual(response.status, "Rejected")
            self.assertEqual(self.box.limit_a, 10)

    async def test_energy_integrates_old_limit_and_stays_constant_during_pause(self):
        self.box.active = True
        with patch("client.time") as clock:
            clock.monotonic.return_value = 100
            self.box.last_measurement = 40
            await self.central.set_limit(0)
            self.assertAlmostEqual(self.box.energy, 4110 + 3450 / 60)
        paused_energy = self.box.energy
        with patch("client.time") as clock:
            clock.monotonic.return_value = 160
            self.box.measure()
            self.assertEqual(self.box.energy, paused_energy)


if __name__ == "__main__":
    unittest.main()
