# Wattpilot-Simulator, OCPP-Server und OpenEMS

```
client.py ──OCPP 1.6──► OCPP.py ──HTTP (go-e-Format)──► OpenEMS Edge 2026.11.0 ──► OpenEMS-UI
(Wallbox)   Port 9000             Port 8081               Port 8075 / 8080          Port 4200
```

- `client.py` simuliert die Fronius Wattpilot Flex (später ersetzt durch die echte Box).
- `OCPP.py` ist der OCPP-Server. Er schreibt alles in `logs/` und gibt den Zustand an OpenEMS weiter. Ladelimits von OpenEMS schickt er als `SetChargingProfile` an die Wallbox.
- OpenEMS regelt das Laden. PV (500 W) ist simuliert, der Netzbezug wird als Wallbox − PV berechnet.

## Voraussetzungen

- Python 3 mit `pip install ocpp websockets`
- Java 21 oder neuer

## Starten

Drei Fenster (PowerShell), alle im Ordner `Python_OCPP_Test`, in dieser Reihenfolge:

```powershell
python OCPP.py                                        # 1. OCPP-Server
.\openems\start.ps1                                   # 2. OpenEMS + UI (Start dauert 1–3 Minuten)
python client.py ws://localhost:9000/TESTCP01 15      # 3. Wallbox-Simulator, 15 Minuten Ladevorgang
```

Beim ersten Start lädt `start.ps1` OpenEMS (`openems-edge.jar`) und die UI herunter.

## Ansehen

| Was | Adresse |
|---|---|
| OpenEMS-UI (Energiemonitor, Ladestation) | http://localhost:4200, Passwort `user` |
| OpenEMS-Konfiguration | http://localhost:8080/system/console/configMgr, `admin` / `admin` |
| Zustand der Wallbox, wie OpenEMS ihn liest | http://localhost:8081/status |
| Alle OCPP-Nachrichten | `logs/wattpilot_daten_<Datum>_<Uhrzeit>.csv` (je Serverstart eine Datei) |

## Einstellen

| Was | Wo |
|---|---|
| Ladeleistung, die OpenEMS vorgibt | `openems/config/Controller/Evcs/ctrlEvcs0.config`: `forceChargeMinPower` (W pro Phase, 2300 = 10 A) |
| PV-Leistung | `openems/config/Simulator/Datasource/Single/Direct/datasource0.config`: `values` |
| Ladedauer | zweites Argument von `client.py` (Minuten) |
| Fahrzeug, Spannung, Takt | Konstanten oben in `client.py` |

OpenEMS liest `openems/config` nur beim Start. Nach Änderungen OpenEMS neu starten. Alternativ in der OpenEMS-Konfiguration (Port 8080) ändern, das wirkt sofort.

## Beenden

In jedem Fenster `Strg+C`. Der Client sendet dabei noch `StopTransaction`.

## Zeitverhalten

- Die Wallbox sendet Messwerte alle 60 s, Statusänderungen sofort.
- OpenEMS fragt den Server alle 10 s ab.
- Ein neues Limit wirkt in der Wallbox nach höchstens 30 s.
