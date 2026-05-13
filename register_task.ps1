# register_task.ps1
# 將 t9fox auto 註冊為 Windows 工作排程器任務
# 每天週一到週五 08:55 自動啟動（在開盤前 5 分鐘，程式會自動等到 09:00）
#
# 使用方式（以系統管理員身份執行 PowerShell）：
#   .\register_task.ps1 -Symbol 6449
#   .\register_task.ps1 -Symbol 6449 -Lots 2 -Lookback 10
#
# 移除任務：
#   Unregister-ScheduledTask -TaskName "T9FOX_Auto" -Confirm:$false

param(
    [string]$Symbol    = "6449",
    [int]   $Lots      = 1,
    [int]   $Lookback  = 20,
    [string]$StartTime = "09:00",
    [string]$SellTime  = "13:20"
)

$TaskName   = "T9FOX_Auto_$Symbol"
$PythonExe  = (Get-Command t9fox -ErrorAction SilentlyContinue)?.Source
if (-not $PythonExe) {
    # fallback: locate via py launcher
    $PythonExe = "py"
    $Arguments = "-3 -m t9fox.cli auto $Symbol --lookback $Lookback --lots $Lots --start-time $StartTime --sell-time $SellTime"
    $WorkDir   = Split-Path -Parent $PSCommandPath
} else {
    $Arguments = "auto $Symbol --lookback $Lookback --lots $Lots --start-time $StartTime --sell-time $SellTime"
    $WorkDir   = Split-Path -Parent $PSCommandPath
}

# Action: run t9fox auto in a minimised window
$Action  = New-ScheduledTaskAction `
    -Execute    $PythonExe `
    -Argument   $Arguments `
    -WorkingDirectory $WorkDir

# Trigger: Mon-Fri at 08:55
$Trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At "08:55"

# Settings
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable

# Run as current user (no password prompt needed)
$Principal = New-ScheduledTaskPrincipal `
    -UserId    ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel  Highest

# Register (overwrite if exists)
Register-ScheduledTask `
    -TaskName  $TaskName `
    -Action    $Action `
    -Trigger   $Trigger `
    -Settings  $Settings `
    -Principal $Principal `
    -Force | Out-Null

Write-Host "Task '$TaskName' registered." -ForegroundColor Green
Write-Host "  Symbol   : $Symbol"
Write-Host "  Lots     : $Lots"
Write-Host "  Lookback : ${Lookback}d"
Write-Host "  Trigger  : Mon-Fri 08:55"
Write-Host "  Sell at  : $SellTime"
Write-Host ""
Write-Host "Check: schtasks /query /tn $TaskName /fo list"
Write-Host "Remove: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
