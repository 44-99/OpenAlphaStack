# Register AlphaClaude Feishu service as a Windows Scheduled Task
# Run this script ONCE as Administrator to set up auto-start on boot.
#
# Usage: Right-click PowerShell → Run as Administrator → run this script
# Or:    pwsh -File setup-autostart.ps1
#
# To remove:   schtasks /Delete /TN "AlphaClaudeFeishuService" /F
# To check:    schtasks /Query /TN "AlphaClaudeFeishuService" /V
# Manual run:  schtasks /Run /TN "AlphaClaudeFeishuService"
# Manual stop: schtasks /End /TN "AlphaClaudeFeishuService"

$ErrorActionPreference = "Stop"

$TaskName = "AlphaClaudeFeishuService"
$ScriptPath = Join-Path $PSScriptRoot "start-flyservice.cmd"
$ProjectDir = $PSScriptRoot

$action = New-ScheduledTaskAction -Execute $ScriptPath -WorkingDirectory $ProjectDir

# Trigger 1: at system startup, delay 30s for network readiness
$trigger1 = New-ScheduledTaskTrigger -AtStartup -RandomDelay (New-TimeSpan -Seconds 30)

# Trigger 2: at user logon, run immediately
$trigger2 = New-ScheduledTaskTrigger -AtLogOn

# Settings
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartOnFailureWith (New-TimeSpan -Minutes 1) `
    -RestartCount 5 `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -MultipleInstances IgnoreNew `
    -Compatibility Win8

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger1, $trigger2 `
    -Settings $settings `
    -Principal $principal `
    -Description "AlphaClaude Feishu bot — FastAPI + WebSocket long-connection service. Auto-starts on boot and user logon."

Write-Host "=== Task registered ==="
Write-Host "Task name : $TaskName"
Write-Host "Script    : $ScriptPath"
Write-Host ""
Write-Host "Starting the service now..."
schtasks /Run /TN $TaskName
Start-Sleep -Seconds 3

$info = schtasks /Query /TN $TaskName /FO LIST /V 2>&1 | Select-String "Status|TaskName|Next Run|Logon Mode"
Write-Host "=== Task status ==="
$info | ForEach-Object { $_.Line }

Write-Host ""
Write-Host "Setup complete. The service will auto-start on every boot."
Write-Host "Check logs at: $ProjectDir\data\logs\service.log"
