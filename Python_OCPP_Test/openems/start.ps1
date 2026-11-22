# Startet OpenEMS Edge 2026.11.0 mit der Minimal-Konfiguration aus .\config
# und die fertige OpenEMS-UI aus .\ui (zum Ansehen der Daten):
#   - evcs0:              liest die Wallbox über den OCPP-Server (go-e-Schnittstelle, Port 8081)
#   - ctrlDebugLog0:      schreibt die Werte jede Sekunde in dieses Fenster
#   - ctrlApiWebsocket0:  Schnittstelle für die UI (Port 8075)
#   - rrd4j0:             speichert Verläufe (für Diagramme)
#
#   UI:          http://localhost:4200        (Passwort: user  oder  admin)
#   Konfiguration: http://localhost:8080/system/console/configMgr  (admin / admin)
# Voraussetzung: Java 21 oder neuer, Python (für den UI-Webserver).
$ErrorActionPreference = "Stop"
$release = "https://github.com/OpenEMS/openems/releases/download/2026.11.0"
$jar = Join-Path $PSScriptRoot "openems-edge.jar"
$ui  = Join-Path $PSScriptRoot "ui\openems-ui"

if (-not (Test-Path $jar)) {
    Write-Host "Lade openems-edge.jar 2026.11.0 herunter ..."
    Invoke-WebRequest "$release/openems-edge.jar" -OutFile $jar
}
if (-not (Test-Path $ui)) {
    Write-Host "Lade openems-ui 2026.11.0 herunter ..."
    $archive = Join-Path $PSScriptRoot "ui.tar.xz"
    Invoke-WebRequest "$release/openems-ui.tar.xz" -OutFile $archive
    New-Item -ItemType Directory -Force (Join-Path $PSScriptRoot "ui") | Out-Null
    tar -xJf $archive -C (Join-Path $PSScriptRoot "ui")
    Remove-Item $archive
}

$web = Start-Process python -ArgumentList "-m", "http.server", "4200", "--bind", "127.0.0.1", "--directory", "`"$ui`"" -NoNewWindow -PassThru
Write-Host "OpenEMS-UI: http://localhost:4200"
try {
    java "-Dfelix.cm.dir=$PSScriptRoot\config" "-Dopenems.data.dir=$PSScriptRoot\data" -jar $jar
} finally {
    Stop-Process -Id $web.Id -ErrorAction SilentlyContinue
}
