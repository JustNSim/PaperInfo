# PaperInfo 后台服务重启脚本
# 修改 .env（如更换 LLM API Key）后需要重启才能生效，运行本脚本即可。
# 普通权限即可运行（只有 install/uninstall 注册任务才需要管理员）。
# 用法（在项目根目录）:
#   powershell -ExecutionPolicy Bypass -File scripts\restart_server.ps1
$ErrorActionPreference = 'Stop'

$taskName = 'PaperInfo'

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Error "任务计划 '$taskName' 不存在，请先运行 scripts\install_autostart.ps1"
    exit 1
}

# 停止任务并清理残留进程
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq 'pythonw.exe' -and $_.CommandLine -match 'run_server\.py|app\.py' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

Start-Sleep -Seconds 2

Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 2

$state = (Get-ScheduledTask -TaskName $taskName).State
Write-Host "已重启任务计划 '$taskName'，当前状态: $state"
Write-Host "访问: http://localhost:5000"
Write-Host "日志: logs\server.log"
