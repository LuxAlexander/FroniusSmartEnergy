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
    pip install ocpp websockets --break-system-packages

Start:
    python3 wattpilot_ocpp_logger.py
"""

import asyncio
import csv
import logging
import os
from datetime import datetime, timezone

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint as CP
from ocpp.v16 import call_result
from ocpp.v16.enums import Action, RegistrationStatus, AuthorizationStatus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wattpilot-ocpp")

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
HOST = "0.0.0.0"        # auf allen Netzwerk-Interfaces lauschen
PORT = 9000              # Port, den du auch in der Wattpilot-App einträgst
CSV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wattpilot_daten.csv")

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
]

_csv_lock = asyncio.Lock()


def _ensure_csv_header():
    """Legt die CSV-Datei mit Header an, falls sie noch nicht existiert."""
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
        transaction_id = int(datetime.now().timestamp())  # simple eindeutige ID
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
                    raw_payload=f"measurand={sample.get('measurand', 'Energy.Active.Import.Register')}, "
                                 f"unit={sample.get('unit', '')}, ts={ts}",
                )
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


async def on_connect(websocket):
    """Wird bei jeder neuen Verbindung aufgerufen und startet die
    Nachrichten-Verarbeitung für diesen Charge Point."""
    # Die Charge-Point-ID steckt im Pfad, z.B. ws://host:9000/WATTPILOT01
    path = websocket.request.path if hasattr(websocket, "request") else websocket.path
    charge_point_id = path.strip("/").split("/")[-1] or "unknown"

    # Subprotokoll-Check (Wattpilot sollte "ocpp1.6" anbieten)
    requested = websocket.request.headers.get("Sec-WebSocket-Protocol", "") if hasattr(websocket, "request") else ""
    logger.info("Neue Verbindung von Charge Point '%s' (Subprotokoll: %s)", charge_point_id, requested)

    cp = WattpilotChargePoint(charge_point_id, websocket)
    try:
        await cp.start()
    except websockets.exceptions.ConnectionClosed:
        logger.info("Verbindung zu '%s' getrennt", charge_point_id)


async def main():
    _ensure_csv_header()
    logger.info("Starte OCPP-Server auf %s:%s ...", HOST, PORT)
    logger.info("CSV-Datei: %s", CSV_FILE)
    logger.info(
        "Trage in der Wattpilot-App/Weboberfläche als OCPP-Backend ein: "
        "ws://<IP-dieses-Rechners>:%s/<CHARGEPOINT-ID>", PORT
    )

    async with websockets.serve(
        on_connect,
        HOST,
        PORT,
        subprotocols=["ocpp1.6"],
    ):
        await asyncio.Future()  # läuft für immer


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server beendet.")