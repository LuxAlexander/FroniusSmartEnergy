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
import json
import logging
import os
from datetime import datetime, timezone
from urllib.parse import unquote

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint as CP
from ocpp.v16 import call, call_result
from ocpp.v16.enums import (Action, AuthorizationStatus, ChargingProfileKindType,
                            ChargingProfilePurposeType, ChargingRateUnitType, RegistrationStatus)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wattpilot-ocpp")

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
HOST = "0.0.0.0"        # auf allen Netzwerk-Interfaces lauschen
PORT = 9000              # Port, den du auch in der Wattpilot-App einträgst
GOE_PORT = 8081          # HTTP-Schnittstelle für OpenEMS (Baustein Evcs.Goe.ChargerHome)
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
]

_csv_lock = asyncio.Lock()


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


# ---------------------------------------------------------------------------
# Aktueller Zustand der Wallbox (eine Wallbox, Connector 1) und
# Schnittstelle zu OpenEMS im Format der go-e API v1 (GET /status).
# OpenEMS liest das mit dem fertigen Baustein "Evcs.Goe.ChargerHome".
# ---------------------------------------------------------------------------
STATE = {"status": "Available", "meter_start": 0.0, "energy": 0.0, "offered": 0.0,
         "current": [0.0, 0.0, 0.0], "power": [0.0, 0.0, 0.0], "power_sum": 0.0}

# OCPP-Status -> go-e "car": 1 frei, 2 lädt, 3 wartet auf Auto, 4 fertig
GOE_CAR = {"Available": 1, "Preparing": 3, "Charging": 2, "SuspendedEV": 3,
           "SuspendedEVSE": 3, "Finishing": 4}


def update_state(sample):
    """Übernimmt einen sampledValue nach (measurand, phase) in STATE."""
    measurand = sample.get("measurand", "Energy.Active.Import.Register")
    phase = sample.get("phase", "")[:2]                  # "L1-N" -> "L1"
    value = float(sample["value"]) * (1000 if sample.get("unit") in ("kW", "kWh") else 1)
    n = int(phase[1]) - 1 if phase in ("L1", "L2", "L3") else None
    if measurand == "Current.Import" and n is not None:
        STATE["current"][n] = value
    elif measurand == "Power.Active.Import":
        if n is None:
            STATE["power_sum"] = value
        else:
            STATE["power"][n] = value
    elif measurand == "Energy.Active.Import.Register":
        STATE["energy"] = value
    elif measurand == "Current.Offered":
        STATE["offered"] = value


def goe_status():
    """STATE als go-e-API-v1-JSON (Einzelwerte als Strings wie beim echten go-e)."""
    s = STATE
    volt = [round(p / i) if i > 1 else 230 for p, i in zip(s["power"], s["current"])]
    pha = 0b111 | sum(8 << n for n, i in enumerate(s["current"]) if i > 1)  # Bit 3-5: aktive Phasen
    nrg = volt + [0] + [round(i * 10) for i in s["current"]] \
        + [round(p / 100) for p in s["power"]] + [0, round(s["power_sum"] / 10), 0, 0, 0, 0]
    return {"version": "B", "fwv": "sim", "sse": "ocpp", "err": "0", "alw": "1", "cbl": "0",
            "car": str(GOE_CAR.get(s["status"], 1)), "amp": str(round(s["offered"])),
            "pha": str(pha), "eto": str(round(s["energy"] / 100)),
            "dws": str(round(max(0.0, s["energy"] - s["meter_start"]) * 360)), "nrg": nrg}


# ---------------------------------------------------------------------------
# Steuerung OpenEMS -> Wallbox: go-e-Befehl (amp/alw) -> SetChargingProfile
# ---------------------------------------------------------------------------
CHARGE_POINTS = {}                       # verbundene Wallboxen: ID -> WattpilotChargePoint
GOE_CMD = {}                             # zuletzt von OpenEMS gesetzte Werte, z.B. {"amp": "10"}
LAST_LIMIT = None                        # zuletzt an die Wallbox gesendetes Limit (A)


def tx_default_profile(limit_a):
    return {"charging_profile_id": 1, "stack_level": 0,
            "charging_profile_purpose": ChargingProfilePurposeType.tx_default_profile,
            "charging_profile_kind": ChargingProfileKindType.absolute,
            "charging_schedule": {"charging_rate_unit": ChargingRateUnitType.amps,
                                  "charging_schedule_period": [{"start_period": 0, "limit": limit_a}]}}


async def send_limit(limit_a):
    """Schickt das Limit als TxDefaultProfile an alle verbundenen Wallboxen."""
    for cp in list(CHARGE_POINTS.values()):
        try:
            res = await cp.call(call.SetChargingProfile(
                connector_id=1, cs_charging_profiles=tx_default_profile(limit_a)))
            status = res.status
        except Exception as e:                           # Timeout, Verbindung weg, ...
            status = f"Fehler: {e!r}"
        logger.info("SetChargingProfile %s A an '%s': %s", limit_a, cp.id, status)
        await log_row(charge_point_id=cp.id, message_type="SetChargingProfile",
                      meter_value=limit_a, status=status)


async def handle_goe_command(path):
    """'/mqtt?payload=amp=10' bzw. 'alw=0' auswerten; nur bei geändertem Limit senden."""
    global LAST_LIMIT
    key, _, value = path.partition("payload=")[2].partition("=")
    if GOE_CMD.get(key) == value:
        return                                           # nichts Neues
    GOE_CMD[key] = value
    logger.info("Befehl von OpenEMS: %s=%s", key, value)
    await log_row(charge_point_id="openems", message_type="OpenEMS-Befehl", raw_payload=path)
    if GOE_CMD.get("alw") == "0":
        limit = 0.0                                      # Laden gesperrt -> Pause
    elif "amp" in GOE_CMD:
        limit = float(GOE_CMD["amp"])
    else:
        return
    if limit != LAST_LIMIT:
        LAST_LIMIT = limit
        asyncio.create_task(send_limit(limit))           # HTTP-Antwort nicht blockieren


async def goe_http(reader, writer):
    """Minimaler HTTP-Server: jede Anfrage bekommt den aktuellen Status.
    Befehle von OpenEMS (/mqtt?payload=amp=10) gehen als Ladeprofil an die Wallbox."""
    request = (await reader.readline()).decode(errors="ignore").split()
    while (await reader.readline()) not in (b"\r\n", b"\n", b""):
        pass                                             # Header überspringen
    path = unquote(request[1]) if len(request) > 1 else "/"
    if path.startswith("/mqtt"):
        await handle_goe_command(path)
    body = json.dumps(goe_status()).encode()
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                 b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
    await writer.drain()
    writer.close()


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
        if connector_id == 1:
            STATE["status"] = status
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
        STATE["meter_start"] = STATE["energy"] = float(meter_start)
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
        # Nach dem Ende kommen keine MeterValues mehr -> Ist-Werte auf 0 setzen
        STATE.update(energy=float(meter_stop), power_sum=0.0, current=[0.0] * 3, power=[0.0] * 3)
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
                update_state(sample)
                await log_row(
                    charge_point_id=self.id,
                    message_type="MeterValues",
                    connector_id=connector_id,
                    transaction_id=transaction_id,
                    meter_value=sample.get("value"),
                    raw_payload=f"measurand={sample.get('measurand', 'Energy.Active.Import.Register')}, "
                                 f"unit={sample.get('unit', '')}, phase={sample.get('phase', '')}, ts={ts}",
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
    CHARGE_POINTS[charge_point_id] = cp
    try:
        await cp.start()
    except websockets.exceptions.ConnectionClosed:
        logger.info("Verbindung zu '%s' getrennt", charge_point_id)
    finally:
        CHARGE_POINTS.pop(charge_point_id, None)


async def main():
    _ensure_csv_header()
    logger.info("Starte OCPP-Server auf %s:%s ...", HOST, PORT)
    logger.info("CSV-Datei: %s", CSV_FILE)
    logger.info(
        "Trage in der Wattpilot-App/Weboberfläche als OCPP-Backend ein: "
        "ws://<IP-dieses-Rechners>:%s/<CHARGEPOINT-ID>", PORT
    )

    logger.info("go-e-Schnittstelle für OpenEMS: http://<IP-dieses-Rechners>:%s/status", GOE_PORT)

    async with websockets.serve(
        on_connect,
        HOST,
        PORT,
        subprotocols=["ocpp1.6"],
    ), await asyncio.start_server(goe_http, HOST, GOE_PORT):
        await asyncio.Future()  # läuft für immer


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server beendet.")