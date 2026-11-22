# Startet alles in der richtigen Reihenfolge, jedes Programm in einem eigenen Fenster:
#   1. OCPP-Server   2. OpenEMS (+ OpenEMS-UI)   3. Wallbox-Simulator   4. eigene Weboberfläche
# Aufruf:  .\start_all.ps1            (Ladevorgang 15 Minuten)
#          .\start_all.ps1 -Minuten 60
# Beenden: in jedem Fenster Strg+C bzw. Fenster schließen.
param([int]$Minuten = 15)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Test-Port($port) {
    $c = New-Object Net.Sockets.TcpClient
    try { $c.Connect("127.0.0.1", $port); $true } catch { $false } finally { $c.Dispose() }
}
function Wait-Port($port, $name, $timeoutSec) {
    Write-Host "Warte auf $name (Port $port) ..." -NoNewline
    for ($i = 0; $i -lt $timeoutSec; $i++) {
        if (Test-Port $port) { Write-Host " bereit nach $i s"; return }
        Start-Sleep 1
    }
    throw "$name ist nach $timeoutSec s nicht erreichbar."
}
function Start-Window($title, $command) {
    Start-Process powershell -WorkingDirectory $PSScriptRoot -ArgumentList "-NoExit", "-Command",
        "`$Host.UI.RawUI.WindowTitle = '$title'; $command"
}

if ((Test-Port 9000) -or (Test-Port 8075)) {
    throw "Port 9000 oder 8075 ist belegt - laeuft schon eine Sitzung? Bitte zuerst beenden."
}

Start-Window "1 OCPP-Server" "python OCPP.py"
Wait-Port 9000 "OCPP-Server" 30

Start-Window "2 OpenEMS" "& '.\openems\start.ps1'"
Wait-Port 8075 "OpenEMS" 300

Start-Window "3 Wallbox-Simulator" "python client.py ws://localhost:9000/TESTCP01 $Minuten"

Start-Process (Join-Path $PSScriptRoot "web\index.html")
Write-Host "Alles gestartet. Eigene Oberflaeche: web\index.html, OpenEMS-UI: http://localhost:4200"
