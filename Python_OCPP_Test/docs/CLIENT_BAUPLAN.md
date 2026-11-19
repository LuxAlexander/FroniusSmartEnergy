# Bauplan: Wattpilot-Simulator (`client.py`) und PV-Regel im Server

> Stand 19.11.2026: Der erste Steuerungsschritt ist implementiert, siehe
> [README](../README.md). Der Simulator unterstützt bewusst nur ein einfaches
> TxDefaultProfile in A mit 0 oder 6–16 A und lehnt weitere Profile ab.
> Profiländerungen melden sich sofort als Messwerte; Pausen liefern exakt 0 W.
> Die PV-Regel dieses Bauplans und ClearChargingProfile sind noch nicht umgesetzt.
> Die folgenden Abschnitte bleiben Planungsunterlagen, keine Beschreibung
> durchgehend implementierter oder am echten Gerät bestätigter Funktionen.

Ziel: `client.py` verhält sich nach außen wie die echte Fronius Wattpilot Flex. Der Server berechnet aus der PV-Leistung ein Ladelimit und schickt es per OCPP an die Wallbox. Später wird der Client durch die echte Wattpilot ersetzt, **ohne dass sich am Server etwas ändert**.

Leitplanken:
- Alles läuft über OCPP 1.6J. Client und Server nutzen das Paket `ocpp` (Version 2.1.0 ist installiert, der Server nutzt es schon).
- Der Client bleibt klein: eine Datei, keine Konfigurationsdatei, Zielgröße etwa 100 Zeilen.
- Der Client kann **nicht mehr** als die echte Box. Was die Wattpilot ablehnt, lehnt auch der Client ab (Abschnitt 7).

---

## 1. Rollen

| | Client (Simulator / Wattpilot) | Server (Central System, später OpenEMS) |
|---|---|---|
| Kennt die PV-Leistung | nein | ja |
| Entscheidet über den Ladestrom | nein, nimmt ein Limit an | ja, berechnet und sendet das Limit |
| Misst und meldet | Status, Transaktion, MeterValues | – |
| Protokolliert | – | CSV, später State-Store |

```
                 heute: simuliert, später: Fronius-Wechselrichter / OpenEMS
                              │ PV-Leistung
                              ▼
 ┌──────────────┐  SetChargingProfile (Limit in A)  ┌───────────────────────┐
 │ client.py    │ ◄──────────────────────────────── │ OCPP.py               │
 │ später:      │                                   │  PV-Regel             │
 │ Wattpilot    │ ────────────────────────────────► │  CSV-Log              │
 └──────────────┘  Boot, Status, Start/Stop,        └───────────────────────┘
                   MeterValues, Heartbeat
```

Beim Austausch ändert sich nur die linke Box.

## 2. Client: Aufbau

```python
from ocpp.v16 import ChargePoint, call, call_result
from ocpp.routing import on
from ocpp.v16.enums import Action, ChargingProfileStatus, ...

class Wallbox(ChargePoint):
    limit_a = I_MAX                    # gilt, solange kein Profil gesetzt ist

    @on(Action.set_charging_profile)
    async def on_set_profile(self, connector_id, cs_charging_profiles): ...

    @on(Action.clear_charging_profile)
    async def on_clear_profile(self, **kw): ...  # limit_a = I_MAX

async def main():
    async with websockets.connect(URL, subprotocols=["ocpp1.6"]) as ws:
        cp = Wallbox(CP_ID, ws)
        await asyncio.gather(cp.start(), simulate(cp))
```

- `cp.start()` liest alle eingehenden Nachrichten und leitet Server-Anfragen an die `@on`-Methoden weiter.
- `simulate(cp)` ist die Ablauf-Schleife (Abschnitt 4).
- Das Paket sorgt dafür, dass immer nur ein eigener `call` gleichzeitig unterwegs ist. Deshalb braucht es keine eigene Sperre.
- Nicht unterstützte Server-Anfragen beantwortet das Paket automatisch mit `NotImplemented`.

Mit Paket wird `client.py` nicht größer als die Variante ohne Paket. Die eigene `call()`-Funktion fällt weg, und jede Nachricht wird gegen die offiziellen OCPP-Schemas geprüft.

### Parameter (Konstanten oben in der Datei)

```python
CP_ID       = "TESTCP01"  # aus der URL
U           = 230.0       # V je Phase (gemessen ca. 226 V)
PHASES      = 1           # Testfahrzeug lädt einphasig
I_MIN       = 6.0         # A, darunter pausiert die Ladung
I_MAX       = 16.0        # A, Box-/Anschlussgrenze (Current.Offered im Mitschnitt)
I_CAR       = 15.0        # A, mehr zieht das Fahrzeug nicht
TICK        = 30          # s, Heartbeat; MeterValues jeden 2. Tick
METER_START = 4110.0      # Wh
```

Ladedauer als zweites Argument: `python client.py ws://localhost:9000/TESTCP01 15` (Minuten).

### `SetChargingProfile` annehmen

Ausgewertet wird nur das, was für ein Limit nötig ist:

```
profil   = cs_charging_profiles
zweck    = profil["charging_profile_purpose"]     # tx_default_profile / tx_profile
periode  = profil["charging_schedule"]["charging_schedule_period"][0]
einheit  = profil["charging_schedule"]["charging_rate_unit"]   # "A" oder "W"
limit_a  = periode["limit"]            (bei "W": / (U · PHASES))
→ status accepted
```

Abgelehnt (`rejected`) wird alles, was die Wattpilot laut Test (Abschnitt 7) nicht kann. Bis zum Test: `ChargePointMaxProfile` ablehnen, die anderen annehmen. Mehrere Perioden, `stackLevel` und Gültigkeitszeiten werden nicht ausgewertet. Es gilt immer die erste Periode.

### Fahrzeug- und Messmodell (pro Tick, `dt` = 30 s)

```
I_off  = 0                      wenn limit_a < I_MIN   (Pause)
       = min(limit_a, I_MAX)    sonst
I_L1   = min(I_off, I_CAR) + Rauschen(±0,05 A)
I_L2,3 = 0,03 A                 (bei PHASES = 3: wie L1)
P_Lx   = U · I_Lx               (L2/L3 mit Rauschen ±0,3 W, darf leicht negativ sein)
P_sum  = P_L1 + P_L2 + P_L3
E     += P_sum · dt / 3600      (Wh, intern float; gesendet auf 10 Wh abgerundet)
f      = 50,00 ± 0,01
P_off  = I_off · 3 · U          (16 A → 11 040 W wie im Mitschnitt)
```

Ein neues Limit wirkt ab dem nächsten Tick. Das entspricht ungefähr einer Reaktionszeit von 0–30 s. Die echte Reaktionszeit wird an der Box gemessen (Abschnitt 7).

Fällt `I_off` auf 0, sendet der Client `StatusNotification SuspendedEVSE`. Steigt es wieder, sendet er `Charging`. Ob die Wattpilot das genauso meldet, steht in der Prüfliste.

### MeterValues-Inhalt (11 Werte, jeweils mit `phase`)

| measurand | phase | unit |
|---|---|---|
| `Current.Import` | L1, L2, L3 | A |
| `Current.Offered` | – | A |
| `Energy.Active.Import.Register` | – | Wh |
| `Frequency` | – | *(weglassen, wie im Mitschnitt)* |
| `Power.Active.Import` | – (Summe), L1, L2, L3 | W |
| `Power.Offered` | – | W |

Werte als String mit 2 Nachkommastellen. Die Schlüssel schreibt man im Paket in snake_case (`sampled_value`), das Paket wandelt sie in camelCase um.

## 3. Ablauf im Client (`simulate`)

```
BootNotification   vendor "fronius", model "wattpilot_flex_c6",
                   charge_point_serial_number "SIM0001", firmware_version "sim"
StatusNotification 0 Available
StatusNotification 1 Preparing
StartTransaction   connector 1, id_tag "no-card", meter_start → transaction_id merken
StatusNotification 1 Charging
MeterValues        sofort

try:
    alle TICK s bis Ladedauer um:
        Messmodell rechnen (immer, damit der Zähler genau bleibt)
        Heartbeat
        jeder 2. Tick: MeterValues
        Status geändert (Charging ↔ SuspendedEVSE)? → StatusNotification
finally:                                  # auch bei Strg+C
    StopTransaction  meter_stop, reason "Local"
    StatusNotification 1 Finishing
    StatusNotification 1 Available
```

Der `finally`-Block läuft auch bei Strg+C, weil `asyncio.run` die Aufgabe abbricht. Ist die Verbindung dann schon zu, wird der Fehler verschluckt.

## 4. Server: PV-Regel und Limit senden (Ergänzung in `OCPP.py`)

Etwa 40 Zeilen. Die Regel ist bewusst ein eigener Block, der später nach OpenEMS umzieht. Der Server reicht dann nur noch das Limit von OpenEMS weiter.

**PV-Quelle** (vorerst simuliert, später echte Messung):

```python
def pv_power():                     # W, später: Fronius Solar API / OpenEMS
    return min(800.0, max(0.0, random.gauss(500, 150)))
```

**Regel** (Anforderung Nr. 13, PV-Anteil-Regelung):

```
I_soll = clamp( P_pv / (230 · PHASES),  6 A,  16 A )
```

Bei maximal 800 W PV kommt immer 6 A heraus, also etwa 1,38 kW. Die PV deckt davon höchstens etwa 58 %. Ein Schalter `MODE = "pv" | "fix"` erlaubt zum Vergleich mit dem Mitschnitt ein festes Limit von 16 A.

**Senden:** Pro Verbindung läuft neben `cp.start()` eine zweite Aufgabe `regler(cp)`:

```python
async def regler(cp):
    letztes = None
    while True:
        await asyncio.sleep(30)
        soll = round(regel(pv_power()), 1)
        if letztes is None or abs(soll - letztes) >= 0.5:   # Hysterese gegen Flattern
            res = await cp.call(call.SetChargingProfile(connector_id=1, cs_charging_profiles=PROFIL(soll)))
            await log_row(charge_point_id=cp.id, message_type="SetChargingProfile", meter_value=soll, status=res.status)
            letztes = soll
```

Profil (zuerst `TxDefaultProfile`, weil es keine `transactionId` braucht):

```json
{"chargingProfileId": 1, "stackLevel": 0,
 "chargingProfilePurpose": "TxDefaultProfile", "chargingProfileKind": "Absolute",
 "chargingSchedule": {"chargingRateUnit": "A",
                      "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 6.0}]}}
```

In `on_connect`: `await asyncio.gather(cp.start(), regler(cp))`. Im CSV-Log stehen dann gesendetes Limit, Antwort der Box (`accepted`/`rejected`) und Zeitstempel. Das ist die Grundlage für die Auswertung der Regelgüte (Anforderung Nr. 9).

## 5. Umsetzungsschritte (jeder Schritt lauffähig)

| # | Schritt | Fertig, wenn |
|---|---|---|
| 1 | Client auf Paket `ocpp` umstellen, Boot + Heartbeat wie bisher | Server-CSV zeigt `vendor=fronius`, Heartbeat alle 30 s |
| 2 | Status + Start/Stop | Start- und Stop-Zeile mit derselben `transaction_id` |
| 3 | Messmodell + MeterValues mit festem Limit 16 A | Werte wie im Mitschnitt (Abschnitt 6) |
| 4 | Client: `SetChargingProfile` / `ClearChargingProfile` annehmen | Test per Hand: Server schickt 10 A → `Current.Offered` = 10 |
| 5 | Server: `pv_power()`, Regel, `regler()` | `Current.Offered` = 6 A, Zählerstand + ca. 23 Wh/min |
| 6 | Server: Limit < 6 A testen (z. B. `MODE = "fix"` mit 0 A) | Client meldet `SuspendedEVSE`, Leistung ≈ 0 |

## 6. Abnahme gegen den Mitschnitt

Mit `MODE = "fix"` und 16 A fünf Minuten laufen lassen, Server-CSV neben `docs/wattpilot_daten.csv` legen:

| Prüfung | Erwartung |
|---|---|
| Nachrichtenfolge | Boot, Status 0, Status 1, StartTransaction, MeterValues, Heartbeat ×2, MeterValues … |
| Zeilen pro MeterValues | 11 |
| `Current.Import` | ca. 15,0 / 0,03 / 0,03 A |
| `Power.Active.Import` Summe | ca. 3 450 W (Mitschnitt 3 394 W bei 226 V; mit `U = 226` fast gleich) |
| Zählerstand | + ca. 57 Wh/min, Schritte zu 10 Wh (Mitschnitt: 4110 → 4340 in 4 min) |
| `Power.Offered` | 11 040 W |

Zusätzlich neu (gibt es im Mitschnitt noch nicht): Zeilen `SetChargingProfile` mit Status `Accepted`.

## 7. Prüfliste an der echten Wattpilot

Diese Punkte einmal an der Box testen und das Ergebnis im Client nachbilden. Am einfachsten geht das, indem der Server die Anfragen per Hand oder per kleinem Skript schickt und die Antworten im Log stehen.

| # | Frage | Wie testen | Folge für den Client |
|---|---|---|---|
| 1 | Welche `phase`-Felder und welche Reihenfolge haben die MeterValues? | rohes JSON mitschneiden (`PLAN.md` A1) | Reihenfolge und Phasen übernehmen |
| 2 | Welche Profilzwecke werden angenommen? | je ein `SetChargingProfile` mit `TxDefaultProfile`, `TxProfile`, `ChargePointMaxProfile` | nicht unterstützte ablehnen |
| 3 | Einheit `A` und/oder `W`? | Profil mit `chargingRateUnit` = W | ggf. W ablehnen |
| 4 | Wird `numberPhases` beachtet? | Profil mit `numberPhases` = 1 | nur relevant mit 3-phasigem Fahrzeug |
| 5 | Was passiert bei Limit < 6 A bzw. 0 A? | Profil mit 0 A | Status (`SuspendedEVSE`?) und Werte übernehmen |
| 6 | Reaktionszeit auf ein neues Limit | Zeitstempel Profil vs. erste MeterValues mit neuem `Current.Offered`; dafür `MeterValueSampleInterval` kurz stellen | `TICK`/Verzögerung anpassen |
| 7 | Was bleibt nach Ende der Transaktion / nach Neustart? | `TxDefaultProfile` setzen, Box neu starten | Limit zurücksetzen oder behalten |
| 8 | Konfiguration | `GetConfiguration` ohne Schlüssel | Werte merken; ggf. `GetConfiguration` im Client nachbilden |
| 9 | Eigene PV-Regelung der Box | Eco Mode / PV-Überschuss in der App **aus** schalten | sonst regeln zwei Stellen gleichzeitig |
| 10 | Was passiert bei Verbindungsabbruch zum Server? | Server beenden während des Ladens | Fallback-Verhalten, Anforderung Nr. 16 |

## 8. Austausch Client → echte Wattpilot

1. Prüfliste (Abschnitt 7) abarbeiten, Client anpassen, erneut gegen den Server testen.
2. In der Wattpilot-App: OCPP aktivieren, Backend-URL `ws://<server-ip>:9000/<id>`, PV-Überschussladen aus.
3. Server unverändert starten. Ablauf und Abnahme wie in Abschnitt 6, diesmal mit der Box.
4. Der Client bleibt als Testwerkzeug für Fälle, die mit der Box schwer gehen: Verbindungsabbruch, 3-phasiges Fahrzeug, Fahrzeug zieht weniger als angeboten, Langzeitlauf im Zeitraffer (`TICK` kleiner).

## 9. Bewusst weggelassen

RemoteStart/StopTransaction, Authorize/RFID, Reset, mehrere Perioden im Ladeprofil, Wiederverbinden nach Abbruch, Sicherheit (TLS, Basic Auth). Das kommt erst dazu, wenn die Grundstrecke PV → Limit → Ladestrom läuft.
