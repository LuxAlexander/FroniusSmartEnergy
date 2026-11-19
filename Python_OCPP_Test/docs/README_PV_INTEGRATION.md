# Nächste Schritte zur PV-Anbindung

Ziel: **EnergyTwin-PV → Modbus/TCP → OpenEMS → OCPP → Wallbox-Simulator**.
Stand: geplant; die Verbindung zu OpenEMS ist noch nicht umgesetzt oder geprüft.

1. **PV starten:** Im EnergyTwin-Projekt die Anleitung „PV-Simulator im Container“
   verwenden. Master und Wetterdienst starten, Master fortsetzen und genau eine
   PV-Instanz betreiben. Für den realen Aufbau `--peak 0.8` setzen; größere
   PV-Leistungen ausdrücklich als eigenes Testszenario kennzeichnen.
2. **Erreichbarkeit prüfen:** Zunächst OpenEMS auf Windows gegen
   `127.0.0.1:1502`, Unit-ID `1`, planen. Der dokumentierte PV-Container
   veröffentlicht diesen Port nur auf Windows-Loopback. Für OpenEMS in Docker
   ein gemeinsames Netzwerk und den PV-Containernamen als Adresse konfigurieren;
   `localhost` im OpenEMS-Container erreicht die PV nicht.
3. **PV-Werte einlesen:** Modbus-Verbindung und passende OpenEMS-Gerätekomponente
   konfigurieren beziehungsweise einen Adapter ergänzen. Holding Registers mit
   FC03 lesen, Adressen **0-basiert**, Float32 **ABCD / höherwertiges Wort zuerst**:

   | Register | Inhalt | Zuordnung |
   |---|---|---|
   | 4–5 | Wirkleistung, kW; Erzeugung negativ | Für positive PV-Erzeugungsanzeige: `−Wert × 1000` W |
   | 6–7 | Erzeugte Gesamtenergie, kWh seit Prozessstart | `Wert × 1000` Wh; Neustarts berücksichtigen |
   | 2 / 3 | Betriebszustand / Fehlercode | Status und Störung übernehmen |

4. **Zeit und Ausfälle behandeln:** Für erste gemeinsame Versuche Tempo `1`
   wählen. Modellzeit (8–9), Lauf (10), Sequenz (11), Zeitstatus (12) und
   Läuft-Flag (13) berücksichtigen. Pausen, Modellschrittweite und Neustarts
   beachten; bei Verbindungsausfall eine definierte Ladegrenze verwenden.
5. **Regelkreis prüfen:** Erst PV-Leistung in OpenEMS anzeigen, dann Ladegrenzen
   über OCPP senden. Tag/Nacht, PV-Ausfall und Pause/Fortsetzen prüfen.
   800 W reichen nicht für rein solares Laden bei 6 A; Netzunterstützung vorsehen.

Quellen im EnergyTwin-Projekt auf GitHub:

- [PV und Registerplan](https://github.com/LuxAlexander/dotnet-energy-twin-alpha/blob/main/src/Devices/PvSimulator/README.md)
- [Docker und Startfolge](https://github.com/LuxAlexander/dotnet-energy-twin-alpha/blob/main/docker/README.md)
- [Modbus-Datenvertrag](https://github.com/LuxAlexander/dotnet-energy-twin-alpha/blob/main/src/Modbus.Contracts/README.md)
