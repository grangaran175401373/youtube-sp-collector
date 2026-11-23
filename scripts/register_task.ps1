<#
.SYNOPSIS
    YouTube新着動画自動収集バッチを Windows タスクスケジューラへ登録します。

.DESCRIPTION
    開発計画書 Phase 2 準拠の設定で毎日 02:00 自動実行タスクを登録します。
    - 多重起動防止 (IgnoreNew)
    - 実行時間上限 1時間 (PT1H)
    - スケジュール時刻を過ぎた場合はできるだけ早く実行 (StartWhenAvailable)
    - バッテリー駆動時でも実行許可
#>

$ErrorActionPreference = "Stop"

$TaskName = "YouTubeSharePointCollector"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$BatchPath = Join-Path $ProjectDir "run_batch.bat"

if (-not (Test-Path $BatchPath)) {
    throw "実行バッチが見つかりません: $BatchPath"
}

$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$BatchPath`"" `
    -WorkingDirectory $ProjectDir

$Trigger = New-ScheduledTaskTrigger -Daily -At "02:00"

$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "YouTube新着動画を毎日自動収集し、SharePointリスト「YouTube動画管理」へ登録するバッチ" `
    -Force | Out-Null

Write-Host "[OK] タスクスケジューラ登録完了: $TaskName"
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State | Format-Table -AutoSize
Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object NextRunTime, LastRunTime, LastTaskResult | Format-List
