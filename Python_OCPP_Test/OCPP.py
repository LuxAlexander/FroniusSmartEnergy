"""
OCPP-1.6-Central-System-Server, der sich Verbindungen von einer
Fronius Wattpilot Flex (oder jedem anderen OCPP-1.6-fähigen Wallbox-Client)
annehmen lässt und alle empfangenen Daten in eine CSV-Datei schreibt.

WICHTIG: Bei OCPP ist die Wallbox immer der CLIENT, nicht der Server.
Du musst in der Wattpilot-Konfiguration (App / lokales Webinterface)
die OCPP-Backend-URL auf dieses Skript zeigen lassen, z.B.:

    ws://<IP-dieses-Rechners>:9000/WATTPILOT01

("WATTPILOT01" ist hier die frei wählbare Charge-Point-ID, siehe unten)

Installation:
    python -m pip install -r requirements.txt

Start:
    python OCPP.py [--demo]
"""

import asyncio
import argparse
import csv
import itertools
import json
import logging
import math
import os
from datetime import datetime, timezone

import websockets
from ocpp.routing import on, after
from ocpp.exceptions import OCPPError
from ocpp.v16 import ChargePoint as CP
from ocpp.v16 import call, call_result
from ocpp.v16.enums import Action, RegistrationStatus, AuthorizationStatus
from dashboard import Monitor, ObservedConnection, start_dashboard

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wattpilot-ocpp")

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
HOST = "0.0.0.0"        # auf allen Netzwerk-Interfaces lauschen
PORT = 9000              # Port, den du auch in der Wattpilot-App einträgst
# Jeder Serverstart schreibt in eine eigene Datei, z.B. logs/wattpilot_daten_20261119_101500.csv
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
CSV_FILE = os.path.join(LOG_DIR, f"wattpilot_daten_{datetime.now():%Y%m%d_%H%M%S}.csv")

CSV_FIELDS = [
    "timestamp",
    "charge_point_id",
    "message_type",
    "connector_id",
    "status",
    "error_code",
    "transaction_id",
    "id_tag",
    "meter_value",       # Wh (Energieregister, sofern vorhanden)
    "raw_payload",
    "measurand", "unit", "phase", "sample_timestamp", "requested_limit_a",
]

_csv_lock = asyncio.Lock()
_transaction_ids = itertools.count(int(datetime.now().timestamp()))
monitor = Monitor()


def _ensure_csv_header():
    """Legt den Ordner logs/ und die CSV-Datei mit Header an."""
    os.makedirs(LOG_DIR, exist_ok=True)
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()


async def log_row(**kwargs):
    """Schreibt eine Zeile thread-/task-sicher in die CSV-Datei."""
    row = {field: "" for field in CSV_FIELDS}
    row["timestamp"] = datetime.now(timezone.utc).isoformat()
    row.update(kwargs)
    async with _csv_lock:
        with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writerow(row)


class WattpilotChargePoint(CP):
    """Repräsentiert die verbundene Wattpilot Flex und verarbeitet alle
    OCPP-1.6-Nachrichten, die sie an das Central System schickt."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.charging = asyncio.Event()
        self.measurements = asyncio.Queue()
        self.requested_limit = None
        self.demo_enabled = False
        self.booted = asyncio.Event()

    @after(Action.boot_notification)
    def after_boot(self, **kwargs):
        # Only query after the Accepted boot response has been sent.
        self.booted.set()

    async def read_configuration(self, timeout=10):
        await self.booted.wait()
        monitor.configuration(self.id, status="loading", requested_at=datetime.now(timezone.utc).isoformat(),
                              keys=[], unknown_keys=[], error=None, received_at=None)
        try:
            response = await asyncio.wait_for(self.call(call.GetConfiguration(), suppress=False), timeout)
            monitor.configuration(self.id, status="ready", keys=response.configuration_key or [],
                                  unknown_keys=response.unknown_key or [],
                                  received_at=datetime.now(timezone.utc).isoformat())
        except asyncio.TimeoutError:
            monitor.configuration(self.id, status="error", error=f"No configuration response within {timeout:g} seconds.")
        except OCPPError as exc:
            status = "unsupported" if exc.code in ("NotImplemented", "NotSupported") else "error"
            monitor.configuration(self.id, status=status, error=f"{exc.code}: {exc.description}")
        except Exception as exc:
            monitor.configuration(self.id, status="error", error=f"Configuration could not be read: {exc}")

    async def set_limit(self, limit):
        profile = {
            "chargingProfileId": 1, "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile", "chargingProfileKind": "Absolute",
            "chargingSchedule": {"chargingRateUnit": "A", "chargingSchedulePeriod": [
                {"startPeriod": 0, "limit": limit}]},
        }
        # Vor dem Senden loggen, damit auch Timeouts nachvollziehbar bleiben.
        await log_row(charge_point_id=self.id, message_type="SetChargingProfileRequest",
                      connector_id=1, requested_limit_a=limit, raw_payload=json.dumps(profile))
        response = await self.call(call.SetChargingProfile(connector_id=1, cs_charging_profiles=profile))
        await log_row(charge_point_id=self.id, message_type="SetChargingProfileResponse",
                      connector_id=1, requested_limit_a=limit, status=response.status)
        if response.status == "Accepted":
            self.requested_limit = limit
        return response

    async def run_demo(self, step_seconds):
        await self.charging.wait()
        for limit in (16, 10, 0, 6):
            while not self.measurements.empty():
                self.measurements.get_nowait()
            response = await self.set_limit(limit)
            if response.status != "Accepted":
                raise RuntimeError(f"Ladegrenze {limit} A abgelehnt: {response.status}")
            async def matching_measurement():
                while True:
                    values = await self.measurements.get()
                    offered = values.get(("Current.Offered", None))
                    if offered is not None and float(offered) == limit:
                        return values
            values = await asyncio.wait_for(matching_measurement(), timeout=15)
            power = values.get(("Power.Active.Import", None), "?")
            logger.info("DEMO: %s A bestätigt, gemeldete Leistung %s W", limit, power)
            await log_row(charge_point_id=self.id, message_type="DemoMeasurement",
                          requested_limit_a=limit, meter_value=power, unit="W")
            await asyncio.sleep(step_seconds)
        logger.info("DEMO abgeschlossen: 16 -> 10 -> 0 -> 6 A")

    # --- Grundlegende Anmeldung ------------------------------------------------
    @on(Action.boot_notification)
    async def on_boot_notification(self, charge_point_vendor, charge_point_model, **kwargs):
        logger.info(
            "BootNotification von %s: Hersteller=%s Modell=%s Extra=%s",
            self.id, charge_point_vendor, charge_point_model, kwargs,
        )
        await log_row(
            charge_point_id=self.id,
            message_type="BootNotification",
            raw_payload=f"vendor={charge_point_vendor}, model={charge_point_model}, {kwargs}",
        )
        return call_result.BootNotification(
            current_time=datetime.now(timezone.utc).isoformat(),
            interval=30,  # Heartbeat-Intervall in Sekunden
            status=RegistrationStatus.accepted,
        )

    @on(Action.heartbeat)
    async def on_heartbeat(self):
        await log_row(charge_point_id=self.id, message_type="Heartbeat")
        return call_result.Heartbeat(current_time=datetime.now(timezone.utc).isoformat())

    # --- Status / Fehler ---------------------------------------------------
    @on(Action.status_notification)
    async def on_status_notification(self, connector_id, error_code, status, **kwargs):
        if connector_id == 1 and status == "Charging":
            self.charging.set()
        logger.info("StatusNotification: connector=%s status=%s error=%s", connector_id, status, error_code)
        await log_row(
            charge_point_id=self.id,
            message_type="StatusNotification",
            connector_id=connector_id,
            status=status,
            error_code=error_code,
            raw_payload=str(kwargs),
        )
        return call_result.StatusNotification()

    # --- Authentifizierung / Ladevorgänge -----------------------------------
    @on(Action.authorize)
    async def on_authorize(self, id_tag, **kwargs):
        await log_row(
            charge_point_id=self.id,
            message_type="Authorize",
            id_tag=id_tag,
        )
        return call_result.Authorize(id_tag_info={"status": AuthorizationStatus.accepted})

    @on(Action.start_transaction)
    async def on_start_transaction(self, connector_id, id_tag, meter_start, timestamp, **kwargs):
        transaction_id = next(_transaction_ids)
        await log_row(
            charge_point_id=self.id,
            message_type="StartTransaction",
            connector_id=connector_id,
            id_tag=id_tag,
            meter_value=meter_start,
            transaction_id=transaction_id,
        )
        return call_result.StartTransaction(
            transaction_id=transaction_id,
            id_tag_info={"status": AuthorizationStatus.accepted},
        )

    @on(Action.stop_transaction)
    async def on_stop_transaction(self, meter_stop, timestamp, transaction_id, **kwargs):
        await log_row(
            charge_point_id=self.id,
            message_type="StopTransaction",
            meter_value=meter_stop,
            transaction_id=transaction_id,
        )
        return call_result.StopTransaction(id_tag_info={"status": AuthorizationStatus.accepted})

    # --- Messwerte (Leistung, Energie, Spannung, Strom, ...) ----------------
    @on(Action.meter_values)
    async def on_meter_values(self, connector_id, meter_value, transaction_id=None, **kwargs):
        for mv in meter_value:
            ts = mv.get("timestamp", "")
            for sample in mv.get("sampled_value", []):
                await log_row(
                    charge_point_id=self.id,
                    message_type="MeterValues",
                    connector_id=connector_id,
                    transaction_id=transaction_id,
                    meter_value=sample.get("value"),
                    measurand=sample.get("measurand", "Energy.Active.Import.Register"),
                    unit=sample.get("unit", ""), phase=sample.get("phase", ""), sample_timestamp=ts,
                    raw_payload=f"measurand={sample.get('measurand', 'Energy.Active.Import.Register')}, "
                                 f"unit={sample.get('unit', '')}, phase={sample.get('phase', '')}, ts={ts}",
                )
            if self.demo_enabled and connector_id == 1:
                self.measurements.put_nowait({
                    (s.get("measurand", "Energy.Active.Import.Register"), s.get("phase")): s["value"]
                    for s in mv.get("sampled_value", [])
                })
        return call_result.MeterValues()

    # --- Diagnose / Firmware-Meldungen (optional, werden nur geloggt) ------
    @on(Action.diagnostics_status_notification)
    async def on_diagnostics_status_notification(self, status, **kwargs):
        await log_row(charge_point_id=self.id, message_type="DiagnosticsStatusNotification", status=status)
        return call_result.DiagnosticsStatusNotification()

    @on(Action.firmware_status_notification)
    async def on_firmware_status_notification(self, status, **kwargs):
        await log_row(charge_point_id=self.id, message_type="FirmwareStatusNotification", status=status)
        return call_result.FirmwareStatusNotification()


async def on_connect(websocket, demo=False, step_seconds=5):
    """Wird bei jeder neuen Verbindung aufgerufen und startet die
    Nachrichten-Verarbeitung für diesen Charge Point."""
    # Die Charge-Point-ID steckt im Pfad, z.B. ws://host:9000/WATTPILOT01
    path = websocket.request.path if hasattr(websocket, "request") else websocket.path
    charge_point_id = path.strip("/").split("/")[-1] or "unknown"

    # Subprotokoll-Check (Wattpilot sollte "ocpp1.6" anbieten)
    requested = websocket.request.headers.get("Sec-WebSocket-Protocol", "") if hasattr(websocket, "request") else ""
    logger.info("Neue Verbindung von Charge Point '%s' (Subprotokoll: %s)", charge_point_id, requested)

    token = object()
    monitor.connect(charge_point_id, token)
    cp = WattpilotChargePoint(charge_point_id, ObservedConnection(websocket, charge_point_id, monitor))
    cp.demo_enabled = demo
    reader = asyncio.create_task(cp.start())
    configuration_task = asyncio.create_task(cp.read_configuration())
    demo_task = asyncio.create_task(cp.run_demo(step_seconds)) if demo else None
    try:
        if demo_task:
            done, _ = await asyncio.wait((reader, demo_task), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        await reader
    except websockets.exceptions.ConnectionClosed:
        logger.info("Verbindung zu '%s' getrennt", charge_point_id)
    finally:
        monitor.disconnect(charge_point_id, token)
        tasks = [reader, configuration_task] + ([demo_task] if demo_task else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Testfolge 16, 10, 0, 6 A senden")
    parser.add_argument("--step-seconds", type=float, default=5, help="Wartezeit nach bestätigtem Messwert")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--web-host", default="127.0.0.1", help="Dashboard-Adresse (Standard: nur dieser Rechner)")
    parser.add_argument("--web-port", type=int, default=8080)
    parser.add_argument("--no-web", action="store_true", help="Dashboard nicht starten")
    args = parser.parse_args()
    if not math.isfinite(args.step_seconds) or args.step_seconds <= 0:
        parser.error("Schrittdauer muss endlich und positiv sein.")
    _ensure_csv_header()
    logger.info("Starte OCPP-Server auf %s:%s ...", args.host, args.port)
    logger.info("CSV-Datei: %s", CSV_FILE)
    logger.info(
        "Trage in der Wattpilot-App/Weboberfläche als OCPP-Backend ein: "
        "ws://<IP-dieses-Rechners>:%s/<CHARGEPOINT-ID>", args.port
    )

    http = None
    try:
        async with websockets.serve(
            lambda ws: on_connect(ws, args.demo, args.step_seconds),
            args.host, args.port, subprotocols=["ocpp1.6"],
        ):
            if not args.no_web:
                http = start_dashboard(monitor, args.web_host, args.web_port)
                logger.info("OCPP-Dashboard: http://%s:%s", args.web_host, http.server_port)
            await asyncio.Future()  # läuft für immer
    finally:
        if http:
            await asyncio.to_thread(http.shutdown)
            http.server_close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server beendet.")
