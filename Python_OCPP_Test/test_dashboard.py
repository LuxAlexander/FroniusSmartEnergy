"""Dashboard state, HTTP and real OCPP integration checks."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError
from urllib.request import urlopen

import websockets
from ocpp.exceptions import NotImplementedError as OcppNotImplementedError
from ocpp.v16 import call_result

import OCPP as server
from client import Wallbox, simulate
from dashboard import Monitor, start_dashboard


def call_frame(action, payload, uid="1"):
    return json.dumps([2, uid, action, payload])


class MonitorTests(unittest.TestCase):
    def test_all_samples_raw_errors_and_disconnect_are_retained(self):
        monitor = Monitor()
        monitor.connect("BOX", "old")
        monitor.observe("BOX", "in", call_frame("MeterValues", {
            "connectorId": 2, "meterValue": [{"timestamp": "2026-11-22T12:00:00Z", "sampledValue": [
                {"measurand": "Power.Active.Import", "value": "2.3", "unit": "kW"},
                {"measurand": "Voltage", "phase": "L1", "value": "230", "unit": "V"},
                {"measurand": "SoC", "value": "55", "unit": "Percent"},
            ]}]}))
        monitor.observe("BOX", "out", call_frame("UnsupportedAction", {}, "2"))
        monitor.observe("BOX", "in", json.dumps([4, "2", "NotImplemented", "unsupported", {}]))
        monitor.observe("BOX", "in", "not json")
        monitor.connect("BOX", "new")
        monitor.disconnect("BOX", "old")
        d = monitor.snapshot()["devices"][0]
        self.assertTrue(d["connected"])
        self.assertEqual(len(d["samples"]), 3)
        self.assertEqual(d["samples"][0]["unit"], "kW")
        self.assertEqual(d["protocol_errors"], 1)
        self.assertEqual(d["events"][1]["action"], "UnsupportedAction")
        monitor.disconnect("BOX", "new")
        self.assertFalse(monitor.snapshot()["devices"][0]["connected"])

    def test_session_energy_unit_conversion_and_bounded_messages(self):
        monitor = Monitor()
        monitor.observe("BOX", "in", call_frame("StartTransaction", {
            "connectorId": 1, "meterStart": 1000, "idTag": "test", "timestamp": "2026-11-22T12:00:00Z"}))
        monitor.observe("BOX", "out", json.dumps([3, "1", {"transactionId": 7, "idTagInfo": {"status": "Accepted"}}]))
        monitor.observe("BOX", "in", call_frame("MeterValues", {"connectorId": 1, "transactionId": 7,
            "meterValue": [{"sampledValue": [{"value": "1.5", "unit": "kWh"}]}]}, "2"))
        self.assertEqual(monitor.snapshot()["devices"][0]["sessions"][0]["energy_wh"], 500)
        monitor.observe("BOX", "in", call_frame("StopTransaction", {
            "transactionId": 7, "meterStop": 1700, "timestamp": "2026-11-22T12:30:00Z", "reason": "Local"}, "3"))
        for i in range(220):
            monitor.observe("BOX", "in", call_frame("Heartbeat", {}, str(i+10)))
        d = monitor.snapshot()["devices"][0]
        self.assertEqual(d["sessions"][0]["energy_wh"], 700)
        self.assertEqual(d["sessions"][0]["status"], "Completed")
        self.assertEqual(len(d["events"]), 200)
        self.assertEqual(d["counts"]["Heartbeat"], 220)


class DashboardIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_configuration_empty_unknown_unsupported_and_timeout(self):
        monitor = Monitor()
        async def no_reply(*args, **kwargs):
            await asyncio.sleep(1)
        cases = [
            (AsyncMock(return_value=call_result.GetConfiguration(configuration_key=[], unknown_key=["VendorKey"])), "ready"),
            (AsyncMock(side_effect=OcppNotImplementedError(description="not implemented")), "unsupported"),
            (AsyncMock(side_effect=no_reply), "error"),
        ]
        with patch.object(server, "monitor", monitor):
            for index, (send, expected) in enumerate(cases):
                cp = server.WattpilotChargePoint(f"CONFIG-{index}", None)
                cp.booted.set()
                with patch.object(cp, "call", send):
                    await cp.read_configuration(timeout=0.02)
                c = monitor.snapshot()["devices"][index]["configuration"]
                self.assertEqual(c["status"], expected)
                send.assert_awaited_once()
                self.assertFalse(send.call_args.kwargs["suppress"])
                if expected == "ready":
                    self.assertEqual(c["keys"], [])
                    self.assertEqual(c["unknown_keys"], ["VendorKey"])
                else:
                    self.assertTrue(c["error"])

    async def test_http_dashboard_tracks_real_session_and_disconnect(self):
        monitor = Monitor()
        with tempfile.TemporaryDirectory() as folder, patch.object(server, "monitor", monitor), \
                patch.object(server, "LOG_DIR", folder), patch.object(server, "CSV_FILE", str(Path(folder)/"test.csv")):
            server._ensure_csv_header()
            http = start_dashboard(monitor, "127.0.0.1", 0)
            base = f"http://127.0.0.1:{http.server_port}"
            def fetch(path):
                with urlopen(base+path, timeout=3) as response:
                    return response.status, response.read()
            try:
                async with websockets.serve(server.on_connect, "127.0.0.1", 0, subprotocols=["ocpp1.6"]) as listener:
                    port = listener.sockets[0].getsockname()[1]
                    async with websockets.connect(f"ws://127.0.0.1:{port}/REAL-WIRE", subprotocols=["ocpp1.6"]) as ws:
                        box = Wallbox("REAL-WIRE", ws)
                        reader = asyncio.create_task(box.start())
                        try:
                            await asyncio.wait_for(simulate(box, minutes=0.005, tick_seconds=0.04), 5)
                            status, body = await asyncio.to_thread(fetch, "/api/state")
                            d = json.loads(body)["devices"][0]
                            self.assertEqual(status, 200)
                            self.assertTrue(d["connected"])
                            self.assertEqual(d["boot"]["chargePointVendor"], "fronius")
                            self.assertEqual(d["connectors"]["1"]["status"], "Available")
                            self.assertEqual(d["sessions"][0]["status"], "Completed")
                            self.assertEqual(len(d["samples"]), 11)
                            self.assertEqual(d["configuration"]["status"], "ready")
                            configuration = {k["key"]: k for k in d["configuration"]["keys"]}
                            self.assertEqual(configuration["NumberOfConnectors"]["value"], "1")
                            self.assertTrue(configuration["NumberOfConnectors"]["readonly"])
                            self.assertEqual(d["counts"]["GetConfiguration"], 1)
                            self.assertNotIn("ChangeConfiguration", d["counts"])
                            self.assertGreater(d["received"], 0)
                            self.assertGreater(d["sent"], 0)
                            self.assertTrue(any(e["kind"] == "RESULT" for e in d["events"]))
                            for path in ("/", "/app.js", "/style.css"):
                                self.assertEqual((await asyncio.to_thread(fetch, path))[0], 200)
                            with self.assertRaises(HTTPError):
                                await asyncio.to_thread(fetch, "/../OCPP.py")
                        finally:
                            reader.cancel()
                            await asyncio.gather(reader, return_exceptions=True)
                self.assertFalse(monitor.snapshot()["devices"][0]["connected"])
            finally:
                await asyncio.to_thread(http.shutdown)
                http.server_close()


if __name__ == "__main__":
    unittest.main()
