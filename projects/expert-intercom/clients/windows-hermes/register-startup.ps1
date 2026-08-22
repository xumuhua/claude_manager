# expert-intercom hermes 端 开机自启注册脚本（计划任务）
# 用法（在 hermes 机上、以运行 intercom 的用户打开 PowerShell）：
#   注册：  powershell -ExecutionPolicy Bypass -File register-startup.ps1
#   卸载：  powershell -ExecutionPolicy Bypass -File register-startup.ps1 -Unregister
#   状态：  powershell -ExecutionPolicy Bypass -File register-startup.ps1 -Status
#
# 说明：
# - 注册"用户登录时启动"的计划任务（不需要管理员权限），后台运行 bridge.py。
# - 只新增一个计划任务，不修改任何系统服务/防火墙/其他软件，不影响 hermes 现有飞书网关。
# - token 经用户环境变量 INTERCOM_TOKEN_HERMES 传入（本脚本会引导设置），不落盘到任务定义。
param(
    [switch]$Unregister,
    [switch]$Status
)

$TaskName = "expert-intercom-hermes"
$BridgeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Bridge = Join-Path $BridgeDir "bridge.py"
$Config = Join-Path $BridgeDir "config.json"
$LogDir = Join-Path $BridgeDir "data\logs"

if ($Status) {
    schtasks /Query /TN $TaskName /V /FO LIST 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Host "计划任务 $TaskName 未注册" }
    exit 0
}

if ($Unregister) {
    schtasks /Delete /TN $TaskName /F
    Write-Host "已删除计划任务 $TaskName"
    exit 0
}

# 1. 定位 Python（优先 pythonw，后台无窗口运行）
$PythonW = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $PythonW) {
    $Py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if (-not $Py) { Write-Error "未找到 python.exe，请先安装 Python 3.9+"; exit 1 }
    $PythonW = $Py
}
Write-Host "使用 Python: $PythonW"

# 2. 检查依赖 websockets
& $PythonW -c "import websockets" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "正在安装依赖 websockets（--user，不动系统环境）..."
    & $PythonW -m pip install --user websockets
    if ($LASTEXITCODE -ne 0) { Write-Error "websockets 安装失败"; exit 1 }
}

# 3. 确保 token 用户环境变量存在（不写入任务定义明文）
if (-not [Environment]::GetEnvironmentVariable("INTERCOM_TOKEN_HERMES", "User")) {
    $tok = Read-Host "请输入 hermes 端 token（将存为当前用户的用户环境变量）"
    [Environment]::SetEnvironmentVariable("INTERCOM_TOKEN_HERMES", $tok, "User")
    Write-Host "已写入用户环境变量 INTERCOM_TOKEN_HERMES（重新登录后生效）"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# 4. 注册计划任务：登录时启动，异常退出由任务自身"重启"设置拉起
$cmd = "`"$PythonW`" `"$Bridge`" `"$Config`" >> `"$LogDir\bridge.out.log`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $cmd"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "expert-intercom hermes bridge (F3)" -Force | Out-Null

Write-Host "已注册计划任务 $TaskName（用户登录时启动，崩溃 1 分钟内自动重启）"
Write-Host "立即启动一次：schtasks /Run /TN $TaskName"
Write-Host "查看日志：$LogDir\bridge.out.log 与 $BridgeDir\data\logs\client.log"
