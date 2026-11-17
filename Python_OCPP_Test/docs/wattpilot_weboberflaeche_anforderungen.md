# Weboberfläche Wattpilot Flex + OpenEMS – Funktionsübersicht

**Rahmen des Aufbaus**

- 1 × PV (max. 800 W, Ø 500 W), keine Batterie
- 1 × Fronius Wattpilot Flex (`wattpilot_flex_c6`, Firmware 43.4), angebunden per OCPP 1.6J an OpenEMS
- Starkstromanschluss (Netz) für höhere Ladeleistung
- Testfahrzeug lädt **einphasig** (gemessen: ca. 15 A auf L1, L2/L3 ≈ 0 A, obwohl die Box 3-phasig anbietet)
- Mindestladeleistung des Wattpilot: 6 A → ca. 1,35 kW (1-phasig, bei gemessenen ca. 226 V) bzw. ca. 4,1 kW (3-phasig).
  Die PV kann eine Ladung also höchstens zu ca. 55 % (1-phasig) bzw. ca. 20 % (3-phasig) decken; der Rest kommt immer aus dem Netz.
  Mit dem aktuellen Testfahrzeug gilt immer der 1-phasige Fall.

**Legende**

- Priorität: **M** = Muss, **S** = Soll, **K** = Kann
- Umsetzung über OCPP: ✅ möglich · ⚠️ muss am Gerät getestet werden · ❌ nicht über OCPP (nur App oder externe Daten)

---

## Teil 1 – Zusätzliche Funktionen der Weboberfläche (nicht in der App)

### 1.1 Anzeige und Auswertung

| # | Funktion | Prio | Datenquelle / Umsetzung |
|---|----------|------|-------------------------|
| 1 | Live-Energiefluss: PV → Wattpilot, Netz → Wattpilot, PV → Netz (Einspeisung) | M | PV-Messung + Netzzähler in OpenEMS, MeterValues ✅ |
| 2 | Ströme und Leistung je Phase, Gesamtleistung, Netzfrequenz (live + Verlauf) | M | MeterValues ✅ (`Current.Import` ×3, `Power.Active.Import` ×3 + Summe, `Frequency`). Spannung wird **nicht** gesendet → näherungsweise U ≈ P / I je Phase berechnen |
| 2a | Angebotener vs. tatsächlich bezogener Strom (Box-Limit vs. Fahrzeug) | M | `Current.Offered` / `Power.Offered` vs. `Current.Import` ✅ |
| 3 | Solaranteil je Ladevorgang in % und kWh (PV vs. Netz) | M | Berechnung in OpenEMS aus PV- und Ladeleistung |
| 4 | Diagramm: PV-Leistung, Ladeleistung und Sollwert übereinander | M | OpenEMS-Zeitreihen |
| 5 | Kosten je Ladung (Netzbezug × Tarif, entgangene Einspeisevergütung für PV-Anteil) | S | Tarifdaten (fest oder dynamisch) + Messwerte |
| 6 | Ladeplan-Vorschau: wann geladen wird und warum (PV, Preis, Deadline) | S | Ergebnis der eigenen Regel-/Optimierungslogik |
| 7 | Ereignis- und OCPP-Nachrichtenprotokoll (Status, Fehler, Profile) | S | Alle OCPP-Nachrichten loggen ✅ |
| 8 | Verbindungsstatus Wattpilot (Heartbeat, letzte Nachricht, Online/Offline) | S | Heartbeat, StatusNotification ✅ |
| 9 | Regelgüte: Abweichung Soll- zu Ist-Strom, Reaktionszeit auf neue Profile | S | Soll aus EMS, Box-Limit aus `Current.Offered`, Ist aus `Current.Import` ✅; Zeitstempel SetChargingProfile vs. MeterValues (wichtig für die Auswertung der Arbeit) |
| 10 | CO₂-Bilanz je Ladung | K | Externe API für Netz-CO₂-Intensität |
| 11 | Monats-/Jahresbericht als PDF/CSV | K | Export aus OpenEMS |

### 1.2 Steuerung

| # | Funktion | Prio | Datenquelle / Umsetzung |
|---|----------|------|-------------------------|
| 12 | Betriebsart wählen: Sofortladen / PV-optimiert / Preisoptimiert / Zielladen | M | SetChargingProfile ✅ |
| 13 | **PV-Anteil-Regelung:** Ladung mit Mindeststrom, PV deckt so viel wie möglich (realistisch für 800 W) | M | Sollwert = max(6 A, PV-Leistung / 230 V) ✅ |
| 14 | Frei einstellbare Regelparameter: Startschwelle, Stoppschwelle, Hysterese, Glättungszeit, Mindestlade- und Mindestpausendauer | M | Eigene Logik in OpenEMS ✅ |
| 15 | Manueller Sollstrom (6–32 A bzw. Anschlussgrenze) und Start/Stopp aus der Weboberfläche | M | SetChargingProfile, RemoteStart/StopTransaction ✅ |
| 16 | Fallback bei EMS-/Verbindungsausfall (sicheres Standardprofil) | M | TxDefaultProfile ✅ |
| 17 | Phasenwahl 1-phasig / 3-phasig / automatisch | K | numberPhases im Ladeprofil ⚠️ (ConnectorSwitch3to1PhaseSupported prüfen). Mit dem einphasigen Testfahrzeug ohne Nutzen; nur mit 3-phasigem Fahrzeug testbar |
| 18 | Zielladen nach Energie oder SoC bis Uhrzeit | S | kWh: `Energy.Active.Import.Register` ✅; SoC wird **nicht** gesendet → manuelle Eingabe oder Fahrzeug-API |
| 19 | Kombinierte Optimierung PV + Strompreis + Abfahrtszeit | S | Fahrplanberechnung in OpenEMS, z. B. LP/MILP |
| 20 | PV-Skalierungsfaktor / PV-Simulation (z. B. ×10 → 8 kWp) | S | Für Tests echter Überschuss-Szenarien mit dem kleinen Modul |
| 21 | Laden nach PV-Prognose (Wetterdaten) | K | Externe Prognose-API |
| 22 | Leistungsgrenze am Netzanschluss (Peak-Shaving) | K | Netzzähler + Profilbegrenzung ✅ |
| 23 | Budget je Ladung/Monat (kWh oder €) | K | Eigene Logik |

### 1.3 Nutzer, Zugang, Benachrichtigungen

| # | Funktion | Prio | Datenquelle / Umsetzung |
|---|----------|------|-------------------------|
| 24 | Login mit Rollen (Admin / Nutzer / nur Lesen) | S | Weboberfläche |
| 25 | RFID-Freigabe durch das EMS (z. B. zeitabhängig oder mit kWh-Kontingent) | K | Authorize ✅ |
| 26 | Benachrichtigung bei Ladeende, Fehler, Verbindungsverlust | K | E-Mail / Push aus OpenEMS |
| 27 | REST- oder MQTT-Schnittstelle nach außen | K | OpenEMS-Backend |

---

## Teil 2 – Funktionen der Solar.wattpilot App, die in die Weboberfläche übernommen werden sollten

Grundlage: Bedienungsanleitung Wattpilot Flex. Nur die für den Zweck relevanten Punkte.

### 2.1 Anzeige

| # | App-Funktion | Prio | Über OCPP |
|---|--------------|------|-----------|
| A1 | Ladestatus (bereit, lädt, pausiert, beendet, Fehler) | M | StatusNotification ✅ |
| A2 | Aktuelle Ladeleistung, Ladezeit, geladene Energie | M | MeterValues ✅ |
| A3 | Aufteilung der Leistung nach Quelle (PV-Überschuss / Netz) | M | Aus OpenEMS-Messwerten |
| A4 | Ladehistorie mit Zeitraumfilter, Gesamtenergie | M | Start/StopTransaction + MeterValues ✅ |
| A5 | CSV-Export der Ladevorgänge (Start, Ende, Dauer, max. Leistung/Strom, kWh, Zählerstände, ID-Chip) | S | ✅ |

### 2.2 Lademodi

| # | App-Funktion | Prio | Über OCPP |
|---|--------------|------|-----------|
| A6 | **Standard Mode:** fester Strom, lädt sofort | M | SetChargingProfile ✅ |
| A7 | **Eco Mode:** nur mit PV-Überschuss und/oder unter einer Preisgrenze | M | Eigene Logik + Profile ✅ |
| A8 | **Next Trip Mode:** Ziel in kWh oder km bis Abfahrtszeit, inkl. Verbrauch pro 100 km und Option „danach weiterladen“ | M | Eigene Logik + Profile ✅ |
| A9 | Start/Pause-Schaltfläche | M | RemoteStart/StopTransaction ✅ |

### 2.3 Laden

| # | App-Funktion | Prio | Über OCPP |
|---|--------------|------|-----------|
| A10 | Fünf Stromstufen (6–32 A, je nach Variante) | M | Als Voreinstellungen für SetChargingProfile ✅ |
| A11 | Zeitplaner: erlaubte Ladefenster (Start, Ende, Wochentage) | M | Eigene Logik oder ChargingSchedule ✅ |
| A12 | PV-Überschuss: Startwert (kW), Schwelle für Phasenumschaltung (kW) | M | Eigene Logik ✅ / Phasen ⚠️ |
| A13 | Regelverhalten: Netzbezug bevorzugen / Standard / Einspeisung bevorzugen | S | Offset im eigenen Regler ✅ |
| A14 | Nulleinspeisung | K | Nur relevant, wenn am Wechselrichter gefordert |
| A15 | Fahrzeugeinstellungen: Mindeststrom, Ladepause erlauben, Mindestladezeit, Phasenwechsel-Verzögerung und -Intervall | S | Eigene Logik ✅ |
| A16 | Flexibler Stromtarif: aktivieren, Preisgrenze für Eco Mode, Land/Preiszone | S | Externe Preis-API |

### 2.4 Zugang und Sicherheit

| # | App-Funktion | Prio | Über OCPP |
|---|--------------|------|-----------|
| A17 | Authentifizierung: offen oder Freigabe erforderlich | S | Authorize / RemoteStartTransaction ✅ |
| A18 | RFID-Chips verwalten (Liste, Namen, Energie je Chip) | K | Authorize + eigene Datenbank ✅ |
| A19 | Max. Ladestrom / Strombegrenzung des Anschlusses | M | Obergrenze im eigenen Regler ✅ (Gerätegrenze bleibt in der App) |

### 2.5 Gerät

| # | App-Funktion | Prio | Über OCPP |
|---|--------------|------|-----------|
| A20 | Gerät neu starten | S | Reset ✅ |
| A21 | Firmwarestand / Update-Status anzeigen | K | FirmwareStatus ✅, UpdateFirmware ✅ |
| A22 | LED-Helligkeit | K | Konfigurationsschlüssel LightIntensity ⚠️ |
| A23 | Kabel entriegeln | K | UnlockConnector ✅ |

### 2.6 Bleibt in der App (nicht über OCPP)

Diese Punkte werden einmalig in der App eingerichtet und nicht in die Weboberfläche übernommen:

- WLAN/Ethernet/Hotspot, OCPP-Serveradresse, Internet erlauben
- Technikerpasswort, Netzeinstellungen (Land, max. Ladestrom, Schieflast, Unterspannung)
- Dynamic Load Balancing der Box selbst, Erdungsprüfung, Digitaleingang (§14a EnWG)
- Werkseinstellungen, Beta-Firmware, Gerätepasswort

---

## Teil 3 – Gemessene OCPP-Daten (Mitschnitt 26.08.2026, Firmware 43.4)

### 3.1 Empfangene Nachrichten

| Nachricht | Inhalt / Beobachtung |
|-----------|----------------------|
| BootNotification | vendor `fronius`, model `wattpilot_flex_c6`, Seriennummer, Firmware 43.4 |
| StatusNotification | Connector 0 = Station (Available), Connector 1 = Ladepunkt (Charging) |
| StartTransaction | idTag `no-card` (Box steht auf „offen“), meterStart = aktueller Zählerstand in Wh |
| Heartbeat | alle 30 s |
| MeterValues | alle 60 s, 11 Werte pro Paket (siehe 3.2) |
| StopTransaction | im Mitschnitt noch nicht enthalten → testen |

### 3.2 Messgrößen in MeterValues

| Measurand | Anzahl | Einheit | Beispielwert | Bemerkung |
|-----------|--------|---------|--------------|-----------|
| `Current.Import` | 3 (L1, L2, L3) | A | 15,03 / 0,03 / 0,03 | Phase nur über das Feld `phase` unterscheidbar |
| `Current.Offered` | 1 | A | 16,00 | Vom Wattpilot angebotener Strom (Limit) |
| `Energy.Active.Import.Register` | 1 | Wh | 4110 → 4340 | Zählerstand, Auflösung 10 Wh |
| `Frequency` | 1 | – | 49,99–50,01 | Einheit leer, da OCPP 1.6 kein „Hz“ kennt |
| `Power.Active.Import` | 4 (L1, L2, L3, Summe) | W | 3393,60 / 0,06 / −0,19 / 3393,47 | Summe = L1 + L2 + L3; kleine negative Werte = Messrauschen |
| `Power.Offered` | 1 | W | 11 040 | 16 A × 3 × 230 V |

**Nicht gesendet:** Spannung, SoC, Temperatur.

### 3.3 Abgeleitete Erkenntnisse

- Zeitversatz Gerätezeit → Empfang ca. 2 s (Latenz oder Uhrenabweichung; Uhr der Box über `currentTime` in Boot-/Heartbeat-Antwort synchronisieren).
- Transaction-ID wird vom eigenen Server vergeben (derzeit Unix-Zeitstempel → besser fortlaufender Zähler).

### 3.4 Anpassungen am Logger

1. Felder `phase`, `context` und `location` je SampledValue mitspeichern.
2. Werte nicht als Text in `raw_payload`, sondern strukturiert: ein Datensatz pro Zeitstempel, eine Spalte je Messgröße und Phase (passend zu OpenEMS-Kanälen).
3. Negative Kleinstwerte in der Anzeige auf 0 setzen.