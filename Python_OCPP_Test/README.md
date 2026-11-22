# OCPP-Prototyp für die Bachelorarbeit

Der Prototyp verbindet einen OCPP-1.6J-Server (`OCPP.py`) mit einer simulierten
Wallbox (`client.py`). Der Server protokolliert Nachrichten in CSV. Mit `--demo`
sendet er zusätzlich die Ladegrenzen **16 → 10 → 0 → 6 A** und wartet nach jeder
Vorgabe auf einen Messwert mit passendem `Current.Offered`.

## Start unter Windows

Benötigt wird Python **3.11 oder neuer**; geprüft mit Python 3.12.14,
`ocpp` 2.1.0 und `websockets` 15.0.1. Die Python-Paketversion ist unabhängig von
der Protokollversion: Die Anwendung verwendet weiterhin OCPP 1.6J.
Alle folgenden Befehle gelten im Ordner `Python_OCPP_Test`.

Eine lokale Umgebung `.venv312` wurde für die Prüfung bereits eingerichtet.
Auf einem neuen Rechner mit installiertem Python 3.12 einmalig:

```powershell
py -3.12 -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install -r requirements.txt
```

Terminal 1: Demo-Server ausschließlich für lokale Simulatorverbindungen starten:

```powershell
.\.venv312\Scripts\python.exe OCPP.py --host 127.0.0.1 --demo --step-seconds 5
```

Terminal 2: Simulator für eine Minute starten:

```powershell
.\.venv312\Scripts\python.exe client.py ws://localhost:9000/TESTCP01 1
```

Nach der Demo bleibt der Server erreichbar und die letzte Grenze von 6 A gilt
bis zum Ende dieser Simulatorverbindung. Der Simulator beendet nach einer Minute
die Transaktion. Den Server mit Strg+C beenden.

## Live-Dashboard

Beim Serverstart läuft zusätzlich eine lokale Webansicht unter
**http://127.0.0.1:8080**. Für die echte Wallbox den Server ohne Demo starten:

```powershell
.\.venv312\Scripts\python.exe OCPP.py --host 0.0.0.0 --port 9000
```

Die Wallbox verbindet sich weiterhin per OCPP auf Port 9000; die Webansicht
verwendet Port 8080. Falls 8080 belegt ist: `--web-port 8081`. `--no-web`
deaktiviert die Ansicht. Standardmäßig ist die Webansicht nur auf diesem
Rechner erreichbar. `--web-host` ändert die Bind-Adresse; die Ansicht besitzt
keine Anmeldung und zeigt auch Gerätekennungen und ID-Tags.

Die Ansicht aktualisiert sich alle zwei Sekunden und zeigt:

- Verbindung, letzte Nachricht, Heartbeat und Status/Fehler je Connector;
- gemeldete Gesamtleistung als Verlauf und Ströme/Leistungen je Phase;
- sämtliche empfangenen `MeterValues`, auch zusätzliche Werte wie Spannung
  oder SoC, falls das Gerät sie tatsächlich sendet, inklusive Einheit,
  Phase, Kontext, Messzeit und Empfangszeit;
- Transaktionsnummer, ID-Tag, Anfang/Ende und Energie als Zählerdifferenz;
- Hersteller, Modell, Firmware und weitere Boot-Daten;
- **Device capabilities:** automatisch nach erfolgreicher Anmeldung einmalig
  mit `GetConfiguration` gelesene Einstellungen, unter anderem gemeldete
  Funktionsprofile, Messintervalle und Ladeprofilgrenzen. Alle zurückgegebenen
  Schlüssel werden mit Wert und Schreibschutz angezeigt. Ein fehlender Wert
  wird von einer leeren Zeichenfolge unterschieden. Nicht unterstützte Abfragen,
  fehlende Antworten nach zehn Sekunden und leere Ergebnisse sind sichtbar;
- Nachrichten in beiden Richtungen mit aufklappbarem JSON, Suchfilter,
  Nachrichtenzählern, Protokollfehlern und Ladeprofilantworten;
- Export des aktuellen Monitorzustands als JSON.

Die Webansicht ist **nur lesend**. Der Server sendet nach der Anmeldung eine
`GetConfiguration`-Abfrage, aber kein `ChangeConfiguration` und keine zusätzlichen
Ladebefehle. Die Antwort steht auch im Nachrichtenstrom und im JSON-Export.
Nach einer erneuten Verbindung wird neu gelesen. Gemeldete Funktionsprofile sind
Herstellerangaben und kein Nachweis, dass jeder einzelne Befehl funktioniert.
Der Simulator beantwortet die Abfrage mit zehn nur lesbaren Modellparametern;
seine Angabe `Core,SmartCharging` bezeichnet die modellierten Funktionsbereiche,
keine vollständige OCPP-Konformität und keine bestätigte Wattpilot-Unterstützung.
Fehlende Messwerte bleiben leer; Einheiten werden nicht aus Zahlen geraten.
Bei fehlender Einheit ist nur beim Energiezähler der Standard Wh hinterlegt.
Ein übernommener Kontext/Ort ohne explizite Angabe entspricht den OCPP-Defaults
`Sample.Periodic` / `Outlet`; der Originalinhalt bleibt im Nachrichtenstrom sichtbar.
Eine offene Verbindung ohne Nachricht seit über 90 Sekunden wird markiert.
Nach Verbindungsabbruch bleiben Werte mit ihren Zeitangaben als Historie sichtbar.

Alle Dashboard-Daten gelten seit diesem Serverstart. Es werden pro Station
200 Nachrichten, 180 Messpakete und 30 Transaktionen im Arbeitsspeicher gehalten;
lange JSON-Anzeigen sind auf 12.000 Zeichen begrenzt. Bestehende CSV-Dateien
werden nicht importiert. Nach einem Serverneustart ist die Ansicht wieder leer.
Eine bereits vorher laufende Transaktion hat ohne beobachteten Start keinen
berechenbaren Anfangszählerstand. Die CSV-Protokollierung läuft wie bisher weiter.

Ohne `--demo` sendet der Server keine Ladegrenzen. `--host` und `--port` sind
einstellbar; der bisherige Standard bleibt `0.0.0.0:9000`.
`--tick-seconds` am Client verändert für kurze Tests das Heartbeat-Intervall;
standardmäßig 30 s, reguläre Messwerte alle zwei Ticks. Änderungen eines
Ladeprofils lösen zusätzlich sofort eine Messmeldung aus.

## Verhalten und Grenzen

| Grenze | Erwarteter Fahrzeugbezug | Leistung bei 230 V |
|---|---|---|
| 16 A | maximal 15 A (Fahrzeuggrenze) | ca. 3450 W |
| 10 A | maximal 10 A | ca. 2300 W |
| 0 A | 0 A, Status `SuspendedEVSE` | 0 W |
| 6 A | maximal 6 A, Status `Charging` | ca. 1380 W |

Akzeptiert wird nur Connector 1, `TxDefaultProfile`, `Absolute`, Stack 0,
Einheit A und genau eine Periode ab Sekunde 0 ohne Phasenvorgabe.
Erlaubt sind 0 A sowie 6–16 A. Andere gültige OCPP-Profile werden mit `Rejected`
beantwortet und verändern die bisherige Grenze nicht. Strukturell ungültige
Nachrichten behandelt die OCPP-Bibliothek über die Schema-Prüfung.
Zeitliche Gültigkeit, mehrere Perioden und weitere Profilzwecke sind noch nicht
implementiert. Es gibt keine Wiederverbindung oder dauerhafte Profilspeicherung.

Das Modell lädt einphasig. `Power.Offered` beschreibt wie bisher das dreiphasige
Angebot der simulierten Box; die tatsächliche Fahrzeugleistung ist geringer.
Inaktive Phasen und Ladepausen melden jetzt exakt 0 A und 0 W; das Rauschen auf
der aktiven Phase überschreitet die Stromgrenze nicht. Der Energiezähler wird
aus idealer Ladeleistung und real verstrichener Zeit integriert; das kleine
Messrauschen wird dabei nicht integriert. Während der Pause steigt er nicht.
Der übertragene Zählerstand ist wie bisher auf 10 Wh abgerundet.

Diese Regeln sind Modellentscheidungen. Welche Profile die echte Wattpilot
annimmt und wie sie Pause/Fortsetzen meldet, muss separat am Gerät geprüft werden.
Die Demo ist ausdrücklich für den Simulator vorgesehen.

## Protokoll und Überprüfung

Jeder Serverstart erzeugt `logs/wattpilot_daten_<Zeitstempel>.csv`.
Neben den bisherigen Feldern gibt es strukturierte Spalten für Messgröße,
Einheit, Phase und Messzeitpunkt. Profilanforderung und Antwort werden getrennt
mit `requested_limit_a` protokolliert. `DemoMeasurement` hält die nach der
Vorgabe beobachtete Leistung fest; eine vollständige Regelgüteauswertung ist
damit noch nicht implementiert. Fehlende passende Messwerte führen nach 15 s
zum Abbruch der Demo, nicht zu einer Erfolgsmeldung.

```powershell
.\.venv312\Scripts\python.exe -m unittest -v test_prototype test_dashboard
```

Die vier Tests prüfen den bisherigen Ladeablauf, die komplette Demo einschließlich
Pause/Fortsetzen, abgelehnte Profile sowie die Energieintegration an einem
Grenzwechsel und während einer Pause. Sie verwenden echte lokale WebSockets
mit OCPP-Schema-Prüfung und benötigen keine Hardware. Die schnellen Testläufe
bilden reale Sekunden ab, keine beschleunigte Modellzeit.
Weitere vier Tests prüfen Dashboard-Zustand, Einheiten und Sitzungsauswertung,
begrenzte Historie sowie HTTP-API und Verbindungsabbruch mit einem echten
lokalen Simulatorlauf. Die Webansicht wurde zusätzlich im Browser mit
simulierten Daten, Nachrichtensuche und JSON-Anzeige geprüft.
Die Konfigurationsabfrage wird im echten lokalen Nachrichtenfluss sowie für
leere Antworten, unbekannte Schlüssel, fehlende Unterstützung und Timeout geprüft.

## Nächste Integration

PV-Regelung, OpenEMS, Docker-Bereitstellung und GUI sind noch nicht angebunden.
Als PV-Quelle ist [EnergyTwin](https://github.com/LuxAlexander/dotnet-energy-twin-alpha)
vorgesehen. Dessen README beschreibt einen .NET-PV-Simulator mit Docker,
Modbus/TCP und eine zusätzliche MQTT-Veröffentlichung über ein Gateway.
Die dortige virtuelle Zeit muss bei einer späteren Integration mit der hier
verwendeten realen Zeit abgestimmt werden. Der konkrete Integrationsweg bleibt offen.

Die älteren Architekturideen stehen in `docs/PLAN.md` und `docs/CLIENT_BAUPLAN.md`;
für den tatsächlich implementierten Stand gilt diese Anleitung.
