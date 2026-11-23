# Dumme Fragen (die gar nicht dumm sind)

Fragen aus der Entwicklung, jeweils mit kurzer Antwort. Stand: 23.11.2026.

## OCPP und Aufbau

**Werden vom Simulator schon Messwerte (Summe, L1, …) gesendet?**
Ja, seit Schritt 3. `client.py` schickt beim Laden `MeterValues` mit 11 Werten: Strom L1–L3, Leistung L1–L3 und Summe, angebotener Strom und angebotene Leistung, Zählerstand, Frequenz. Davor gab es nur BootNotification und Heartbeat.

**Läuft alles über OCPP? Braucht es dafür das Paket `ocpp`?**
OCPP 1.6J ist JSON über Websocket. Das ginge auch ohne Paket. Mit `ocpp` muss man aber weniger selbst bauen, weil es Anfragen vom Server annimmt, beantwortet und gegen das OCPP-Schema prüft. Deshalb nutzen Client und Server es.

**Was ist sinnvoll für später, wenn der Simulator durch die echte Wallbox ersetzt wird?**
Der Client darf nur das können, was die echte Box kann: Limit annehmen, Messwerte melden. Die ganze Logik (PV, Regelung) sitzt im Server bzw. in OpenEMS. Dann ändert sich beim Austausch nur die Adresse in der Wattpilot-App.

**Kann sich der Server mit der Wallbox unter `ws://192.168.88.242:9000/api/91136223` verbinden?**
Nein, umgekehrt: Bei OCPP baut die **Wallbox** die Verbindung zum Server auf. Die Adresse ist die Serveradresse, die in der Box eingetragen ist. Der Rechner muss also diese IP haben, und Port 9000 muss in der Firewall frei sein.

**Was brauche ich, bevor ich die echte Wallbox anschließe?**
Rechner im Netz der Box mit der eingetragenen IP, Port 9000 freigegeben, in der App OCPP an und PV-Überschuss aus. Simulator und echte Box nie gleichzeitig verbinden. Start mit `.\start_all.ps1 -Echt`.

## OpenEMS

**Ist es besser, jetzt schon OpenEMS einzubinden?**
Ja. Die PV-Regelung gehört nach OpenEMS. Der OCPP-Server bleibt dazwischen und übersetzt.

**Läuft die Schnittstelle zu OpenEMS über REST?**
Nur ähnlich: HTTP mit JSON im Format der go-e-API. Lesen geht per `GET /status`, Steuern per `PUT /mqtt?payload=amp=10`. Das Format gibt der fertige OpenEMS-Baustein `Evcs.Goe.ChargerHome` vor.

**Wie oft bekommt OpenEMS Daten?**
OpenEMS fragt den Server alle 10 s ab. Neue Messwerte gibt es so oft, wie die Wallbox sie schickt: früher alle 60 s, jetzt stellt der Server `MeterValueSampleInterval` auf 10 s. Statusänderungen kommen sofort.

**In der OpenEMS-UI steht „Ladestation deaktiviert“, obwohl geladen wird. Ist das richtig?**
Das war so, solange es keinen Lade-Controller (`Controller.Evcs`) gab. Der Text bedeutet „OpenEMS steuert nicht“, nicht „Box aus“. Mit dem Controller ist es behoben.

**Warum zeigt der Energiemonitor nichts?**
Ohne Netzzähler kann OpenEMS den Verbrauch nicht berechnen. Seit es den PV-Simulator und den berechneten Netzzähler gibt, zeigt er Werte.

**Warum lud die Wallbox im PV-Überschussmodus nicht, und war die Phasen-Annahme die richtige Lösung?**
Meldet die Box keine aktive Phase, rechnet OpenEMS dreiphasig: Mindestleistung 4140 W statt 1380 W. Damit gab es nie eine Freigabe. Jetzt meldet der Server ohne Laden eine Phase. Für dreiphasige Autos ist das unkritisch, weil OpenEMS den Strom pro Phase vorgibt.

## Werte und Anzeige

**Was sagt die Autarkie aus?**
Welcher Anteil des Verbrauchs **nicht** aus dem Netz kommt: `1 − Netzbezug / Verbrauch`. Beispiel: 1380 W Laden bei 500 W PV ergibt ca. 36 %. Der **Eigenverbrauch** ist das Gegenstück: welcher Anteil der PV selbst genutzt wird.

**Warum ist das Netz bei −500 W?**
In OpenEMS heißt negativ Einspeisung, positiv Bezug. Das Netz wird derzeit als Wallbox − PV berechnet. Ohne Laden ist das 0 − 500 = −500 W.

**Warum liefert die PV immer 500 W, obwohl sie gerade 0 liefern müsste?**
Die PV ist nicht gemessen, sondern simuliert, fest 500 W (`datasource0.config`). Für echte Werte muss der Wechselrichter oder ein Zähler der PV-Anlage an OpenEMS angebunden werden.

**Was ist der Unterschied zwischen Wallbox und Verbrauch?**
Die Wallbox ist gemessen und zeigt nur das, was ins Auto geht. Der Verbrauch ist berechnet (Netz + PV) und umfasst alles, also Haushalt plus Wallbox. Derzeit sind beide gleich, weil das Netz aus Wallbox − PV berechnet wird. Mit einem echten Netzzähler wird die Differenz zum Haushaltsverbrauch.

## Weboberfläche und Code

**Wann kommt die Weboberfläche, und wie aufwändig ist eine eigene?**
Sie ist fertig: `web/index.html`, eine Datei. Die Daten kommen per Websocket von OpenEMS (Port 8075). Werte, die nur über OCPP ankommen, liefert der Server über `/wallbox` (Port 8081).

**Wie funktioniert `handle_manual_limit()`?**
Sie liest den Wert aus `/limit?a=…` und prüft ihn: 0 oder 6–16 A. Dann merkt sie ihn sich als `MANUAL_LIMIT`, das Vorrang vor OpenEMS hat, und schickt ihn per `SetChargingProfile` an die Wallbox. `a=auto` gibt die Steuerung an OpenEMS zurück.
