# PaperInfo 开机自启卸载脚本
# 停止后台实例并删除任务计划
# 用法（在项目根目录）:
#   powershell -ExecutionPolicy Bypass -File scripts\uninstall_autostart.ps1
$ErrorActionPreference = 'Stop'

$taskName = 'PaperInfo'

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "任务计划 '$taskName' 不存在，无需卸载。"
    exit 0
}

# 停止正在运行的任务
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

# 清理可能残留的 pythonw 进程（End 任务不一定能杀掉整个进程树）
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -match 'run_server\.py|app\.py' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false

Write-Host "已卸载任务计划 '$taskName'，后台实例已停止。"
