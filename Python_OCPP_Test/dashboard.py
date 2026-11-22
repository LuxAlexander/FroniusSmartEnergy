"""Read-only, in-memory OCPP monitor and local HTTP dashboard (standard library)."""
import copy
import json
import math
import threading
from collections import Counter, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def energy_wh(value, unit="Wh"):
    n = number(value)
    return n * (1000 if unit == "kWh" else 1) if n is not None and unit in ("Wh", "kWh") else None


class Monitor:
    def __init__(self):
        self.lock = threading.RLock()
        self.started_at = utc_now()
        self.devices = {}

    def _device(self, cp_id):
        if cp_id not in self.devices:
            self.devices[cp_id] = {
                "id": cp_id, "connections": set(), "connected_at": None,
                "disconnected_at": None, "last_seen": None, "last_heartbeat": None,
                "boot": {}, "connectors": {}, "samples": {}, "sessions": deque(maxlen=30),
                "history": deque(maxlen=180), "events": deque(maxlen=200),
                "counts": Counter(), "pending": {}, "received": 0, "sent": 0,
                "protocol_errors": 0, "profile": None,
                "configuration": {"status": "waiting", "keys": [], "unknown_keys": [],
                                  "requested_at": None, "received_at": None, "error": None},
            }
        return self.devices[cp_id]

    def connect(self, cp_id, token):
        with self.lock:
            d = self._device(cp_id)
            d["connections"].add(token)
            d["connected_at"] = utc_now()
            d["configuration"] = {"status": "waiting", "keys": [], "unknown_keys": [],
                                  "requested_at": None, "received_at": None, "error": None}

    def configuration(self, cp_id, **updates):
        with self.lock:
            self._device(cp_id)["configuration"].update(copy.deepcopy(updates))

    def disconnect(self, cp_id, token):
        with self.lock:
            d = self._device(cp_id)
            d["connections"].discard(token)
            if not d["connections"]:
                d["disconnected_at"] = utc_now()
                if d["configuration"]["status"] in ("waiting", "loading"):
                    d["configuration"].update(status="error", error="Disconnected before configuration was received.")

    def observe(self, cp_id, direction, raw):
        """Observe the wire without changing protocol handling or accepting new actions."""
        with self.lock:
            d = self._device(cp_id)
            now = utc_now()
            inbound = direction == "in"
            d["received" if inbound else "sent"] += 1
            if inbound:
                d["last_seen"] = now
            raw_text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            event = {"time": now, "direction": direction, "action": "Invalid frame",
                     "kind": "INVALID", "id": "", "raw": raw_text[:12000],
                     "truncated": len(raw_text) > 12000}
            try:
                frame = json.loads(raw_text)
                if not isinstance(frame, list) or len(frame) < 3:
                    raise ValueError("Invalid OCPP frame")
                kind, uid = frame[:2]
                if kind == 2 and len(frame) == 4 and isinstance(frame[3], dict):
                    action, payload = frame[2:]
                    event.update(action=action, kind="CALL", id=uid)
                    d["pending"][(direction, uid)] = (action, payload)
                    if len(d["pending"]) > 256:
                        del d["pending"][next(iter(d["pending"]))]
                    d["counts"][action] += 1
                    if inbound:
                        self._incoming(d, action, payload, now)
                    elif action == "SetChargingProfile":
                        d["profile"] = {"request": payload, "response": None, "time": now}
                elif kind in (3, 4):
                    pending = d["pending"].pop(("out" if inbound else "in", uid), None)
                    action, request = pending if pending else ("Unmatched response", {})
                    event.update(action=action, kind="RESULT" if kind == 3 else "ERROR", id=uid)
                    if kind == 4:
                        d["protocol_errors"] += 1
                    elif len(frame) == 3 and isinstance(frame[2], dict):
                        response = frame[2]
                        if action == "StartTransaction" and not inbound:
                            accepted = response.get("idTagInfo", {}).get("status") == "Accepted"
                            d["sessions"].appendleft({
                                "id": response.get("transactionId"), "connector": request.get("connectorId"),
                                "id_tag": request.get("idTag"), "start": request.get("timestamp", now),
                                "end": None, "start_wh": energy_wh(request.get("meterStart")),
                                "latest_wh": energy_wh(request.get("meterStart")), "energy_wh": 0,
                                "status": "Active" if accepted else "Rejected", "reason": None,
                            })
                        if action == "SetChargingProfile" and inbound and d["profile"]:
                            d["profile"]["response"] = response
                else:
                    raise ValueError("Unsupported frame")
            except (ValueError, TypeError, KeyError, IndexError, AttributeError):
                # Even a malformed frame remains visible. The OCPP library decides its handling.
                pass
            d["events"].appendleft(event)

    def _incoming(self, d, action, p, now):
        if action == "BootNotification":
            d["boot"] = copy.deepcopy(p)
        elif action == "Heartbeat":
            d["last_heartbeat"] = now
        elif action == "StatusNotification":
            d["connectors"][str(p.get("connectorId", 0))] = {**p, "received_at": now}
        elif action == "StopTransaction":
            session = next((s for s in d["sessions"] if s["id"] == p.get("transactionId")), None)
            if session:
                session.update(end=p.get("timestamp", now), status="Completed", reason=p.get("reason"))
                self._session_energy(session, energy_wh(p.get("meterStop")))
        elif action == "MeterValues":
            connector = p.get("connectorId", 0)
            session = next((s for s in d["sessions"] if s["id"] == p.get("transactionId")
                            and s["connector"] == connector and s["status"] == "Active"), None)
            for packet in p.get("meterValue", []):
                samples = []
                for sample in packet.get("sampledValue", []):
                    m = sample.get("measurand", "Energy.Active.Import.Register")
                    unit = sample.get("unit", "Wh" if m == "Energy.Active.Import.Register" else "")
                    item = {**sample, "measurand": m, "unit": unit,
                            "unit_defaulted": "unit" not in sample,
                            "connector": connector, "timestamp": packet.get("timestamp"), "received_at": now}
                    key = (connector, m, sample.get("phase"), sample.get("location"), sample.get("context"))
                    d["samples"][key] = item
                    samples.append(item)
                    if (session and m == "Energy.Active.Import.Register" and not sample.get("phase")
                            and sample.get("format", "Raw") == "Raw"
                            and sample.get("location", "Outlet") == "Outlet"):
                        self._session_energy(session, energy_wh(sample.get("value"), unit))
                d["history"].append({"timestamp": packet.get("timestamp"), "received_at": now,
                                     "connector": connector, "samples": samples})

    @staticmethod
    def _session_energy(session, latest):
        if latest is not None and session["start_wh"] is not None:
            session["latest_wh"] = latest
            delta = latest - session["start_wh"]
            session["energy_wh"] = delta if delta >= 0 else None

    def snapshot(self):
        with self.lock:
            devices = []
            for d in self.devices.values():
                device = {k: copy.deepcopy(v) for k, v in d.items()
                          if k not in ("connections", "pending", "samples", "sessions", "history", "events")}
                device.update(connected=bool(d["connections"]), samples=list(d["samples"].values()),
                              sessions=list(d["sessions"]), history=list(d["history"]), events=list(d["events"]))
                devices.append(copy.deepcopy(device))
            return {"started_at": self.started_at, "server_time": utc_now(), "devices": devices,
                    "retention": {"events": 200, "history": 180, "sessions": 30}}


class ObservedConnection:
    def __init__(self, connection, cp_id, monitor):
        self.connection, self.cp_id, self.monitor = connection, cp_id, monitor

    async def recv(self):
        raw = await self.connection.recv()
        self.monitor.observe(self.cp_id, "in", raw)
        return raw

    async def send(self, raw):
        await self.connection.send(raw)
        self.monitor.observe(self.cp_id, "out", raw)


def start_dashboard(monitor, host="127.0.0.1", port=8080):
    root = Path(__file__).parent / "web"
    assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
              "/style.css": ("style.css", "text/css")}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/state":
                body = json.dumps(monitor.snapshot(), allow_nan=False).encode("utf-8")
                content_type = "application/json"
            elif path in assets:
                name, content_type = assets[path]
                body = (root / name).read_bytes()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    return http
