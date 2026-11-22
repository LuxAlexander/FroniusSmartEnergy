# Startet OpenEMS Edge 2026.11.0 mit der Minimal-Konfiguration aus .\config
#   - evcs0:          liest die Wallbox über den OCPP-Server (go-e-Schnittstelle, Port 8081)
#   - ctrlDebugLog0:  schreibt die Werte jede Sekunde in dieses Fenster
# Voraussetzung: Java 21 oder neuer. Weboberfläche zur Konfiguration:
#   http://localhost:8080/system/console/configMgr  (admin / admin)
$ErrorActionPreference = "Stop"
$jar = Join-Path $PSScriptRoot "openems-edge.jar"
if (-not (Test-Path $jar)) {
    Write-Host "Lade openems-edge.jar 2026.11.0 herunter ..."
    Invoke-WebRequest "https://github.com/OpenEMS/openems/releases/download/2026.11.0/openems-edge.jar" -OutFile $jar
}
java "-Dfelix.cm.dir=$PSScriptRoot\config" "-Dopenems.data.dir=$PSScriptRoot\data" -jar $jar
