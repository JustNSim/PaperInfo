# PaperInfo 开机自启安装脚本
# 注册一个 Windows 任务计划：用户登录时自动后台启动 PaperInfo 服务
# 用法（在项目根目录，普通权限即可）:
#   powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
# 可重复执行（-Force 覆盖旧任务）。
$ErrorActionPreference = 'Stop'

$taskName = 'PaperInfo'
$projectRoot = Split-Path -Parent $PSScriptRoot

# 优先使用项目 venv 里的 pythonw.exe（GUI 子系统，天然无控制台窗口）
$pythonw = Join-Path $projectRoot 'venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) {
    $pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source
    Write-Host "未找到 venv\Scripts\pythonw.exe，改用系统 pythonw: $pythonw"
}

# 动作：pythonw.exe run_server.py（工作目录 = 项目根目录）
$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument 'run_server.py' -WorkingDirectory $projectRoot

# 触发器：当前用户登录时启动
$trigger = New-ScheduledTaskTrigger -AtLogOn

# Interactive 登录类型：当前用户登录后运行，注册不需要管理员权限
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
    -LogonType Interactive -RunLevel Limited

# 设置：
# - 使用电池时也运行（笔记本关键）
# - 进程异常退出后自动重启 3 次，间隔 1 分钟
# - 不限制运行时长
# - 忽略重复实例（防止 5000 端口被抢）
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName `
    -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description 'PaperInfo 论文抓取服务（登录自动启动，后台无窗口运行）' `
    -Force | Out-Null

# 立即启动一次，无需重新登录
Start-ScheduledTask -TaskName $taskName

Write-Host "已注册任务计划 '$taskName' 并立即启动。"
Write-Host "访问: http://localhost:5000"
Write-Host "日志: logs\server.log"
Write-Host "卸载: powershell -ExecutionPolicy Bypass -File scripts\uninstall_autostart.ps1"
