# Plan: OCPP-Daten vom Client über den Server zu OpenEMS

## 1. Ist-Zustand

| Datei | Rolle | Was sie tut |
|---|---|---|
| `OCPP.py` | **Server** (Central System) | Hört auf `ws://0.0.0.0:9000/<CP-ID>`, nutzt die Libs `ocpp` + `websockets`. Beantwortet BootNotification, Heartbeat, StatusNotification, Authorize, Start/StopTransaction, MeterValues, Diagnostics/FirmwareStatus (alles "accepted") und hängt **jede Nachricht als Zeile an eine CSV** an. |
| `client.py` | **Client** (Charge Point), minimal | Verbindet sich, sendet BootNotification, danach alle `interval` Sekunden Heartbeat. Eingehende Server-Anfragen werden mit `NotImplemented` beantwortet. Nur `websockets`, rohes OCPP-J. |
| `docs/wattpilot_daten.csv` | Mitschnitt der echten Wattpilot Flex C6 (26.08.2026) | Referenz, wie echte Daten aussehen (siehe 2). |

Lücken heute:
- Der Client sendet keine Lade-Daten (kein Status, keine Transaktion, keine MeterValues).
- Der Server schreibt nur CSV. Es gibt keinen Zwischenspeicher mit dem aktuellen Zustand und keine Schnittstelle nach außen.
- Die CSV verliert Information: `phase` und `location` der Messwerte werden nicht gespeichert. Deshalb sind in der CSV mehrere `Current.Import` / `Power.Active.Import` ohne Zuordnung zu L1/L2/L3/Summe zu sehen.
- `transaction_id` kommt aus `int(time())`. Das ist nicht eindeutig bei zwei Starts in einer Sekunde.

## 2. Aufbau der echten Daten (aus `docs/`)

Ablauf des Mitschnitts:

1. `BootNotification`: vendor=fronius, model=wattpilot_flex_c6, serial, firmware.
2. `StatusNotification` für Connector 0 (Available) und Connector 1 (Charging).
3. `StartTransaction`: `idTag=no-card`, `meterStart=4110` Wh.
4. `MeterValues` etwa alle 60 s, jeweils mit **einem** `meterValue`-Eintrag (ein Zeitstempel) und vielen `sampledValue`:

| Measurand | Einheit | Anzahl pro Paket | Bedeutung |
|---|---|---|---|
| `Current.Import` | A | 3 | L1, L2, L3 |
| `Current.Offered` | A | 1 | freigegebener Strom |
| `Energy.Active.Import.Register` | Wh | 1 | Zählerstand |
| `Frequency` | (leer) | 1 | Netzfrequenz |
| `Power.Active.Import` | W | 4 | L1, L2, L3 + Summe (Reihenfolge prüfen, `phase` auswerten) |
| `Power.Offered` | W | 1 | freigegebene Leistung |

5. `Heartbeat` alle 30 s.

Beispielpaket (OCPP-J, so soll der Client es erzeugen):

```json
[2, "<uuid>", "MeterValues", {
  "connectorId": 1,
  "transactionId": 1,
  "meterValue": [{
    "timestamp": "2026-08-26T13:55:15.678Z",
    "sampledValue": [
      {"measurand": "Energy.Active.Import.Register", "unit": "Wh", "value": "4110.00"},
      {"measurand": "Power.Active.Import", "unit": "W", "value": "3393.60"},
      {"measurand": "Power.Active.Import", "unit": "W", "phase": "L1", "value": "3393.47"},
      {"measurand": "Current.Import", "unit": "A", "phase": "L1", "value": "15.03"},
      {"measurand": "Current.Offered", "unit": "A", "value": "16.00"},
      {"measurand": "Power.Offered", "unit": "W", "value": "11040.00"},
      {"measurand": "Frequency", "value": "49.99"}
    ]
}]}]
```

Die Phasen-Zuordnung in der Tabelle ist aus der CSV nur abgeleitet. **Vor dem Bau des Mappings** einmal das rohe JSON der echten Wallbox mitschneiden (Schritt A1), damit die `phase`-Felder bekannt sind.

## 3. Zielarchitektur

```
 client.py (Simulator)            OCPP.py (Server)                      OpenEMS
 später: Wattpilot Flex           ┌───────────────────────────┐
 ──ws/ocpp1.6──────────────────►  │ OCPP-Handler              │
                                  │   ▼                       │
                                  │ State-Store (aktueller    │  Bridge   ┌────────────────┐
                                  │ Zustand je Charge Point)  │ ───────►  │ Modbus/TCP     │
                                  │   ▼                       │           │ oder REST/MQTT │
                                  │ CSV-Log (bleibt)          │           └────────────────┘
                                  └───────────────────────────┘
                                       ▲  Steuerbefehle (später):
                                       └── SetChargingProfile ──► Wallbox
```

Leitidee: **Der OCPP-Handler kennt OpenEMS nicht.** Er schreibt nur in einen State-Store (ein Dict: CP-ID → letzte Werte + Zeitstempel). Die Bridge zu OpenEMS liest daraus. So lässt sich die OpenEMS-Anbindung austauschen, ohne OCPP anzufassen.

## 4. Schritte

### Phase A: Realistische Daten erzeugen (Client)

- **A1. Rohdaten sichern.** In `OCPP.py` zusätzlich jede eingehende Nachricht unverändert als JSON-Zeile in `raw.jsonl` schreiben (nur zum Mitschnitt, später abschaltbar). Damit stehen `phase`, `location`, `context`, `format` zur Verfügung.
- **A2. Client erweitern, Schritt für Schritt, jeweils lauffähig:**
  1. `StatusNotification` (Connector 0 `Available`, Connector 1 `Preparing` → `Charging`).
  2. `StartTransaction` (`meterStart` = Startzählerstand), `transactionId` aus der Antwort merken.
  3. `MeterValues` alle 60 s mit den Measurands aus Abschnitt 2.
  4. `StopTransaction` und Status `Finishing` → `Available`.
- **A3. Kleines Simulationsmodell im Client.** Parameter: Soll-Strom (A), Phasen (1 oder 3), Spannung 230 V. Daraus: `P = Σ U·I`, der Zählerstand steigt jede Messung um `P · Δt`. Etwas Rauschen (±0,05 A) macht die Werte realistisch. Dazu ein Szenario "Auto steckt an, lädt 15 Minuten, Strom wird auf 10 A reduziert, Stopp".
- **A4. Vergleichstest.** Eine Simulations-CSV neben `docs/wattpilot_daten.csv` legen und prüfen: gleiche `message_type`-Reihenfolge, gleiche Measurands, plausible Größenordnungen.
- **A5. Später: Austausch gegen die Wattpilot.** In der Wattpilot-Weboberfläche die Backend-URL auf `ws://<server-ip>:9000/<id>` stellen. Am Server ändert sich nichts. Der Client bleibt als Testwerkzeug.

### Phase B: Server aufräumen

- **B1. State-Store einführen.** Pro CP: Status je Connector, laufende Transaktion, letzte Messwerte (Leistung gesamt und je Phase, Strom je Phase, Zählerstand, Frequenz, Offered), `last_seen`.
- **B2. MeterValues korrekt parsen.** Nach `(measurand, phase)` auswerten statt nach Reihenfolge. Einheit `kW`/`kWh` beachten. Dann auch in die CSV `phase` aufnehmen.
- **B3. Eindeutige `transaction_id`.** Ein Zähler statt `time()`. Optional in einer Datei halten, damit er einen Neustart überlebt.
- **B4. Datei benennen.** `OCPP.py` ist ein Server, der Docstring nennt `wattpilot_ocpp_logger.py`. Umbenennen in `server.py`.

### Phase C: Schnittstelle zu OpenEMS

Welche Variante passt, hängt davon ab, wie OpenEMS bei dir läuft. **Das muss ich klären (Abschnitt 5).** Optionen:

| Variante | Prinzip | Vorteil | Nachteil |
|---|---|---|---|
| **C1: Modbus/TCP-Slave im Server** | Server stellt Register bereit (Leistung, Zählerstand, Status, Soll-Strom). OpenEMS liest sie über den Modbus/TCP-Master-Baustein und ein generisches Modbus-Gerät (EVCS-Kanäle). | Standard in OpenEMS, kein Eigencode in OpenEMS. Python-Lib `pymodbus`. | Registertabelle muss definiert und gepflegt werden. |
| **C2: MQTT** | Server veröffentlicht JSON auf einem Broker (`openems/evcs/<id>/...`). | Lose Kopplung, gut zum Debuggen. | Benötigt Broker. OpenEMS braucht dafür einen eigenen Baustein. |
| **C3: HTTP/REST** | Server bietet `GET /cp/<id>` mit JSON. Ein kleiner Baustein oder Skript holt die Werte. | Am einfachsten zu bauen und zu testen. | OpenEMS-seitig meist Eigenentwicklung. |
| **C4: Eigener Edge-Baustein** | Java-Component in OpenEMS Edge, die `Evcs` implementiert und den Server abfragt. | Volle Integration (Lademanagement, UI). | Aufwändigste Variante. |

Empfehlung für den Start: **C3 (REST)**, weil es die Daten sofort sichtbar macht, und danach **C1 (Modbus/TCP)**, sobald die Kanalliste feststeht. Beide lesen aus demselben State-Store.

Zuordnung der OCPP-Werte zu OpenEMS-EVCS-Kanälen (Namen gegen die verwendete OpenEMS-Version prüfen):

| OCPP | OpenEMS-Kanal (`Evcs`) |
|---|---|
| `Power.Active.Import` (Summe) | `ChargePower` (W) |
| `Energy.Active.Import.Register` | `ActiveConsumptionEnergy` (Wh) |
| `StatusNotification.status` | `Status` (Enum) |
| `Current.Offered` / `Power.Offered` | `SetChargePowerLimit` bzw. `MaximumHardwarePower` |
| Anzahl aktiver Phasen | `Phases` |

### Phase D: Steuerung zurück zur Wallbox (später, wenn Lesen läuft)

- Server sendet `SetChargingProfile` (Strom-/Leistungsgrenze) an die Wallbox. OpenEMS gibt die Grenze vor, die Bridge reicht sie weiter.
- `client.py` muss dafür eingehende Anfragen beantworten (derzeit `NotImplemented`).
- Zusätzlich `RemoteStartTransaction`, `RemoteStopTransaction`, `ChangeConfiguration`.
- Die Wattpilot kann **nicht alle** OCPP-Profile. In den Fronius-Unterlagen prüfen, welche Features sie unterstützt.

## 5. Offene Fragen

1. Läuft OpenEMS schon (Edge auf einem Pi/PC, Simulator, Backend)? Welche Version?
2. Soll OpenEMS die Wallbox nur **überwachen** oder auch **steuern** (PV-Überschussladen)?
3. Gibt es von deinem Betreuer eine Vorgabe für die Schnittstelle (siehe `Fragen_an_Dominik.docx`)?
4. Läuft der Server dauerhaft (Dienst/Container) oder nur im Test?

## 6. Reihenfolge und Abnahme

| Schritt | Fertig, wenn |
|---|---|
| A1 | `raw.jsonl` enthält die echten MeterValues mit `phase`. |
| A2 + A3 | Server loggt vom Client dieselbe Nachrichtenfolge wie von der Wattpilot. |
| B1 + B2 | State-Store zeigt je Phase Strom und Leistung. Zählerstand wächst. |
| C (REST) | `GET /cp/<id>` liefert beim Laden plausible Werte. |
| C (Modbus oder Edge) | OpenEMS zeigt `ChargePower` und `ActiveConsumptionEnergy` der Wallbox. |
| D | Ein Limit aus OpenEMS ändert den Strom im Client bzw. in der Wattpilot. |

## 7. Bewusst weggelassen

Sicherheit (TLS, Basic Auth, OCPP Security Profile), Datenbank statt CSV, Mehrfach-Wallboxen, Smart Charging in OCPP, Firmware-Update, Reservierung. Das kommt später, wenn die Grundstrecke steht.
