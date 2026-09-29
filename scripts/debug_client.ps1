<#
  debug_client.ps1 – Spektrum Universal Adapter debug helper
  ============================================================
  Run with no arguments for interactive menu.
  Or call sub-commands directly:

    .\debug_client.ps1 state
    .\debug_client.ps1 channels
    .\debug_client.ps1 frame
    .\debug_client.ps1 debug
    .\debug_client.ps1 inject-neutral
    .\debug_client.ps1 inject-throttle-full
    .\debug_client.ps1 inject-full-deflection
    .\debug_client.ps1 inject-switch-on          # feed-cut switch ON (pass-through enabled)
    .\debug_client.ps1 inject-switch-off         # feed-cut switch OFF (pass-through blocked)
    .\debug_client.ps1 inject-raw CH1=1500 CH2=32768   # raw channel values (0-indexed)
    .\debug_client.ps1 clear-inject
    .\debug_client.ps1 passthrough-on
    .\debug_client.ps1 passthrough-off
    .\debug_client.ps1 controller-start
    .\debug_client.ps1 controller-stop
    .\debug_client.ps1 watch               # poll /debug every second
    .\debug_client.ps1 profile             # dump active profile inputs

  DX6 Bodycam channel mapping (from calibration):
    CH1 = Throttle  → left_y     (min=10944, max=54592)
    CH2 = Roll      → right_x    (min=54560, max=10976)  ← calibration-inverted
    CH3 = Pitch     → right_y    (min=10976, max=54560)
    CH4 = Yaw       → left_x     (min=54560, max=10976)  ← calibration-inverted
    CH5 = Switch A  → multi_state (no output mapped)
    CH6 = Switch B  → multi_state (state1=dpad_up, state2=none, state3=left_shoulder)
#>

param([string]$Command = "menu", [string[]]$Args)

$BASE = "http://127.0.0.1:8765/api/v1"

# ── known calibrated channel centers / extremes for DX6_Bodycam ──────────────
$DX6 = @{
    # channel index -> [min, center, max]
    # center estimated as midpoint of calibration range
    "CH1_THROTTLE_MIN"   = 10944  # throttle bottom
    "CH1_THROTTLE_MID"   = 32768  # throttle mid
    "CH1_THROTTLE_MAX"   = 54592  # throttle top
    "CH2_ROLL_LEFT"      = 54560  # roll left (inverted calibration)
    "CH2_ROLL_CENTER"    = 32768
    "CH2_ROLL_RIGHT"     = 10976
    "CH3_PITCH_DOWN"     = 10976
    "CH3_PITCH_CENTER"   = 32768
    "CH3_PITCH_UP"       = 54560
    "CH4_YAW_LEFT"       = 54560  # yaw left (inverted calibration)
    "CH4_YAW_CENTER"     = 32768
    "CH4_YAW_RIGHT"      = 10976
    "CH5_SWITCH_A_UP"    = 43648
    "CH5_SWITCH_A_DOWN"  = 21888
    "CH6_SWITCH_B_UP"    = 54592  # → left_shoulder
    "CH6_SWITCH_B_MID"   = 32768  # → none
    "CH6_SWITCH_B_DOWN"  = 17728  # → dpad_up
}

function Invoke-Api {
    param([string]$Method = "GET", [string]$Path, $Body = $null)
    $uri = "$BASE$Path"
    try {
        if ($Body) {
            $json = $Body | ConvertTo-Json -Depth 10
            return Invoke-RestMethod -Method $Method -Uri $uri -ContentType "application/json" -Body $json
        }
        return Invoke-RestMethod -Method $Method -Uri $uri
    } catch {
        Write-Host "  ERROR: $($_.Exception.Message)" -ForegroundColor Red
        return $null
    }
}

function Show-State {
    $s = Invoke-Api -Path "/state"
    if (-not $s) { return }
    $r = $s.receiver
    $c = $s.controller
    Write-Host ""
    Write-Host "── Receiver ─────────────────────────────────────" -ForegroundColor Cyan
    Write-Host ("  Connected     : {0}" -f $r.connected)
    Write-Host ("  Port          : {0}" -f $r.port)
    Write-Host ("  Packet rate   : {0:F1} hz" -f $r.packet_rate)
    Write-Host ("  Bad packets   : {0}" -f $r.usb_bad)
    Write-Host ("  Missing seq   : {0}" -f $r.sequence_missing)
    Write-Host ""
    Write-Host "── Controller ───────────────────────────────────" -ForegroundColor Cyan
    Write-Host ("  Active        : {0}" -f $c.active)
    Write-Host ("  Watchdog      : {0}" -f $c.watchdog_engaged)
    Write-Host ("  Passthrough   : {0}" -f $c.profile_input_passthrough.enabled)
    Write-Host ""
}

function Show-Channels {
    $ch = Invoke-Api -Path "/channels"
    if (-not $ch) { return }
    Write-Host ""
    Write-Host "── Live Channels ────────────────────────────────" -ForegroundColor Cyan
    $ch.PSObject.Properties | Sort-Object { [int]$_.Name } | ForEach-Object {
        Write-Host ("  CH{0,-3}  raw={1}" -f $_.Name, $_.Value)
    }
    Write-Host ""
}

function Show-Frame {
    $f = Invoke-Api -Path "/debug"
    if (-not $f) { return }
    $frame = $f.last_frame
    if (-not $frame) {
        Write-Host "  (no frame yet — controller may not be started)" -ForegroundColor Yellow
        return
    }
    Write-Host ""
    Write-Host "── Last Virtual Frame ───────────────────────────" -ForegroundColor Cyan
    Write-Host "  Axes:"
    $frame.axes.PSObject.Properties | ForEach-Object {
        $bar = [math]::Round(($_.Value + 1) / 2 * 20)
        $vis = ("[" + ("=" * $bar).PadRight(20) + "]")
        Write-Host ("    {0,-12} {1,7:F3}  {2}" -f $_.Name, $_.Value, $vis)
    }
    Write-Host "  Triggers:"
    $frame.triggers.PSObject.Properties | ForEach-Object {
        $bar = [math]::Round($_.Value * 20)
        $vis = ("[" + ("=" * $bar).PadRight(20) + "]")
        Write-Host ("    {0,-18} {1,5:F3}  {2}" -f $_.Name, $_.Value, $vis)
    }
    Write-Host ("  Buttons: {0}" -f (($frame.buttons -join ", "), "(none)" | Select-Object -First 1))
    Write-Host ""
}

function Show-Debug {
    $d = Invoke-Api -Path "/debug"
    if (-not $d) { return }
    Write-Host ""
    Write-Host "── Debug Snapshot ───────────────────────────────" -ForegroundColor Cyan
    Write-Host ("  Controller active : {0}" -f $d.state.controller.active)
    Write-Host ("  Watchdog          : {0}" -f $d.state.controller.watchdog_engaged)
    Write-Host ("  Passthrough       : {0}" -f $d.state.controller.profile_input_passthrough.enabled)
    Write-Host ("  Injected channels : {0}" -f ($null -ne $d.injected))
    if ($d.injected) {
        $d.injected.PSObject.Properties | Sort-Object { [int]$_.Name } | ForEach-Object {
            Write-Host ("    CH{0}={1}" -f $_.Name, $_.Value)
        }
    }
    Write-Host ""
    Show-Frame
    if ($d.debug_log -and $d.debug_log.Count -gt 0) {
        Write-Host "── Recent Debug Events ──────────────────────────" -ForegroundColor DarkGray
        $d.debug_log | Select-Object -Last 8 | ForEach-Object {
            Write-Host ("  {0}" -f ($_ -join "  ")) -ForegroundColor DarkGray
        }
        Write-Host ""
    }
}

function Inject-Channels([hashtable]$channels) {
    $body = @{ channels = $channels }
    $r = Invoke-Api -Method POST -Path "/test/inject-channels" -Body $body
    if ($r) {
        Write-Host "  Injected channels:" -ForegroundColor Green
        $r.injected.PSObject.Properties | Sort-Object { [int]$_.Name } | ForEach-Object {
            Write-Host ("    CH{0} = {1}" -f $_.Name, $_.Value)
        }
    }
}

function Clear-Inject {
    $r = Invoke-Api -Method DELETE -Path "/test/inject-channels"
    if ($r) { Write-Host "  Injection cleared." -ForegroundColor Green }
}

function Inject-Neutral {
    Write-Host "  Injecting neutral / centered channels..." -ForegroundColor Yellow
    Inject-Channels @{
        "1" = $DX6["CH1_THROTTLE_MIN"]    # throttle stick to bottom (safe)
        "2" = $DX6["CH2_ROLL_CENTER"]
        "3" = $DX6["CH3_PITCH_CENTER"]
        "4" = $DX6["CH4_YAW_CENTER"]
        "5" = $DX6["CH5_SWITCH_A_UP"]
        "6" = $DX6["CH6_SWITCH_B_MID"]
    }
}

function Inject-ThrottleFull {
    Write-Host "  Injecting full throttle, sticks neutral..." -ForegroundColor Yellow
    Inject-Channels @{
        "1" = $DX6["CH1_THROTTLE_MAX"]
        "2" = $DX6["CH2_ROLL_CENTER"]
        "3" = $DX6["CH3_PITCH_CENTER"]
        "4" = $DX6["CH4_YAW_CENTER"]
        "5" = $DX6["CH5_SWITCH_A_UP"]
        "6" = $DX6["CH6_SWITCH_B_MID"]
    }
}

function Inject-FullDeflection {
    Write-Host "  Injecting full stick deflection on all axes..." -ForegroundColor Yellow
    Inject-Channels @{
        "1" = $DX6["CH1_THROTTLE_MAX"]
        "2" = $DX6["CH2_ROLL_RIGHT"]
        "3" = $DX6["CH3_PITCH_UP"]
        "4" = $DX6["CH4_YAW_RIGHT"]
        "5" = $DX6["CH5_SWITCH_A_UP"]
        "6" = $DX6["CH6_SWITCH_B_DOWN"]  # triggers dpad_up
    }
}

function Inject-SwitchOn {
    # CH5 at state-1 value — this is the "feed ON" switch state
    Write-Host "  Injecting Switch A ON position (feed pass-through)..." -ForegroundColor Yellow
    Inject-Channels @{
        "1" = $DX6["CH1_THROTTLE_MIN"]
        "2" = $DX6["CH2_ROLL_CENTER"]
        "3" = $DX6["CH3_PITCH_CENTER"]
        "4" = $DX6["CH4_YAW_CENTER"]
        "5" = $DX6["CH5_SWITCH_A_UP"]
        "6" = $DX6["CH6_SWITCH_B_MID"]
    }
}

function Inject-SwitchOff {
    # CH5 at state-2 value — "feed OFF"
    Write-Host "  Injecting Switch A OFF position..." -ForegroundColor Yellow
    Inject-Channels @{
        "1" = $DX6["CH1_THROTTLE_MIN"]
        "2" = $DX6["CH2_ROLL_CENTER"]
        "3" = $DX6["CH3_PITCH_CENTER"]
        "4" = $DX6["CH4_YAW_CENTER"]
        "5" = $DX6["CH5_SWITCH_A_DOWN"]
        "6" = $DX6["CH6_SWITCH_B_MID"]
    }
}

function Inject-Raw([string[]]$pairs) {
    $channels = @{}
    foreach ($p in $pairs) {
        if ($p -match "^CH?(\d+)=(\d+)$") {
            $channels[$Matches[1]] = [int]$Matches[2]
        } else {
            Write-Host "  Bad format: $p  (use CH1=1500 or 1=1500)" -ForegroundColor Red
            return
        }
    }
    Write-Host "  Injecting raw channels..." -ForegroundColor Yellow
    Inject-Channels $channels
}

function Set-Passthrough([bool]$enabled) {
    $r = Invoke-Api -Method PUT -Path "/controller/passthrough" -Body @{ enabled = $enabled }
    if ($r) { Write-Host ("  Passthrough: {0}" -f $r.enabled) -ForegroundColor Green }
}

function Start-Controller {
    $r = Invoke-Api -Method POST -Path "/controller/start"
    if ($r) { Write-Host "  Controller started." -ForegroundColor Green }
}

function Stop-Controller {
    $r = Invoke-Api -Method POST -Path "/controller/stop"
    if ($r) { Write-Host "  Controller stopped." -ForegroundColor Green }
}

function Show-Profile {
    $p = Invoke-Api -Path "/profile"
    if (-not $p) { return }
    Write-Host ""
    Write-Host ("── Profile: {0} ─────────────────────────────────" -f $p.name) -ForegroundColor Cyan
    foreach ($inp in $p.inputs) {
        $ch = $inp.source.channel
        $out = $inp.output.target
        $outType = $inp.output.type
        $enabled = if ($inp.enabled) { "ON" } else { "OFF" }
        Write-Host ("  [{0}] {1,-16} CH{2}  {3,-14} -> {4}:{5}" -f $enabled, $inp.name, $ch, $inp.type, $outType, $out)
    }
    Write-Host ""
}

function Watch-Debug {
    Write-Host "  Watching /debug — press Ctrl+C to stop..." -ForegroundColor Yellow
    Write-Host ""
    while ($true) {
        $host.UI.RawUI.CursorPosition = @{ X=0; Y=[Console]::CursorTop }
        Show-Frame
        Start-Sleep -Seconds 1
    }
}

function Show-Menu {
    Write-Host ""
    Write-Host "  Spektrum Universal Adapter – Debug Client" -ForegroundColor White
    Write-Host "  ──────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host "  1) Show state"
    Write-Host "  2) Show live channels"
    Write-Host "  3) Show last virtual frame"
    Write-Host "  4) Full debug snapshot"
    Write-Host "  5) Show active profile"
    Write-Host "  ─"
    Write-Host "  6) Inject neutral (throttle low, sticks center)"
    Write-Host "  7) Inject full throttle"
    Write-Host "  8) Inject full deflection (all axes)"
    Write-Host "  9) Inject switch A ON  (feed-cut: pass)"
    Write-Host " 10) Inject switch A OFF (feed-cut: block)"
    Write-Host " 11) Clear injection"
    Write-Host "  ─"
    Write-Host " 12) Start controller"
    Write-Host " 13) Stop controller"
    Write-Host " 14) Passthrough ON"
    Write-Host " 15) Passthrough OFF"
    Write-Host " 16) Watch frame (live loop)"
    Write-Host "  0) Exit"
    Write-Host ""
}

# ── Main dispatch ──────────────────────────────────────────────────────────────

switch ($Command.ToLower()) {
    "state"                { Show-State }
    "channels"             { Show-Channels }
    "frame"                { Show-Frame }
    "debug"                { Show-Debug }
    "profile"              { Show-Profile }
    "inject-neutral"       { Inject-Neutral; Start-Sleep -Milliseconds 300; Show-Frame }
    "inject-throttle-full" { Inject-ThrottleFull; Start-Sleep -Milliseconds 300; Show-Frame }
    "inject-full-deflection"{ Inject-FullDeflection; Start-Sleep -Milliseconds 300; Show-Frame }
    "inject-switch-on"     { Inject-SwitchOn; Start-Sleep -Milliseconds 300; Show-Frame }
    "inject-switch-off"    { Inject-SwitchOff; Start-Sleep -Milliseconds 300; Show-Frame }
    "inject-raw"           { Inject-Raw $Args; Start-Sleep -Milliseconds 300; Show-Frame }
    "clear-inject"         { Clear-Inject }
    "passthrough-on"       { Set-Passthrough $true }
    "passthrough-off"      { Set-Passthrough $false }
    "controller-start"     { Start-Controller }
    "controller-stop"      { Stop-Controller }
    "watch"                { Watch-Debug }
    "menu" {
        do {
            Show-Menu
            $choice = Read-Host "  Choice"
            switch ($choice) {
                "1"  { Show-State }
                "2"  { Show-Channels }
                "3"  { Show-Frame }
                "4"  { Show-Debug }
                "5"  { Show-Profile }
                "6"  { Inject-Neutral; Start-Sleep -Milliseconds 300; Show-Frame }
                "7"  { Inject-ThrottleFull; Start-Sleep -Milliseconds 300; Show-Frame }
                "8"  { Inject-FullDeflection; Start-Sleep -Milliseconds 300; Show-Frame }
                "9"  { Inject-SwitchOn; Start-Sleep -Milliseconds 300; Show-Frame }
                "10" { Inject-SwitchOff; Start-Sleep -Milliseconds 300; Show-Frame }
                "11" { Clear-Inject }
                "12" { Start-Controller }
                "13" { Stop-Controller }
                "14" { Set-Passthrough $true }
                "15" { Set-Passthrough $false }
                "16" { Watch-Debug }
                "0"  { break }
                default { Write-Host "  Unknown choice." -ForegroundColor Red }
            }
        } while ($choice -ne "0")
    }
    default {
        Write-Host "Unknown command: $Command" -ForegroundColor Red
        Write-Host "Run without arguments for interactive menu."
    }
}
