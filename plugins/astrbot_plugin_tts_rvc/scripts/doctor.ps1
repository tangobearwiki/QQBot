# 环境自检：把结果贴给别人，比截图猜原因快得多
# 用法：powershell -ExecutionPolicy Bypass -File scripts\doctor.ps1

param(
    [int]$RvcPort = 7868,
    [int]$Port = 9880
)

$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path $PSScriptRoot -Parent
$Runtime = Join-Path $Root "runtime"
$VenvPy = Join-Path $Runtime ".venv\Scripts\python.exe"
$ServiceDir = Join-Path $Root "service"
$LogDir = Join-Path $Runtime "logs"
$Report = Join-Path $Runtime "doctor-report.txt"

$lines = New-Object System.Collections.Generic.List[string]
function Add-Line([string]$Text) {
    Write-Host $Text
    $lines.Add($Text)
}

function Import-EnvFile([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    Get-Content $Path -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { return }
        $kv = $line.Split("=", 2)
        $key = $kv[0].Trim()
        $value = $kv[1].Trim().Trim('"').Trim("'")
        if ($key) { [Environment]::SetEnvironmentVariable($key, $value, "Process") }
    }
}

New-Item -ItemType Directory -Force $Runtime | Out-Null
Import-EnvFile (Join-Path $ServiceDir ".env")

Add-Line "===== TTS-RVC 环境自检 ====="
Add-Line "时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Add-Line "目录：$Root"
Add-Line ""

Add-Line "--- 系统 ---"
Add-Line "PowerShell：$($PSVersionTable.PSVersion)"
Add-Line "系统：$([System.Environment]::OSVersion.VersionString)"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $gpu = & nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>$null
    Add-Line "GPU：$gpu"
} else {
    Add-Line "GPU：没有找到 nvidia-smi（可能是核显/AMD，或驱动没装）"
}

Add-Line ""
Add-Line "--- Python ---"
if (Test-Path $VenvPy) {
    Add-Line "虚拟环境：$VenvPy"
    & $VenvPy -c "import sys, torch; print('Python', sys.version.split()[0]); print('torch', torch.__version__, '| cuda', torch.version.cuda, '| available', torch.cuda.is_available())" 2>&1 | ForEach-Object { Add-Line $_ }
    & $VenvPy -c "import rvc_next, fastapi, httpx, uvicorn; print('rvc_next ok; fastapi', fastapi.__version__, '; httpx', httpx.__version__)" 2>&1 | ForEach-Object { Add-Line $_ }
} else {
    Add-Line "[未安装] 找不到 runtime\.venv，先运行 install.ps1"
}

Add-Line ""
Add-Line "--- 配置 ---"
if (Test-Path (Join-Path $ServiceDir ".env")) {
    Add-Line "service\.env 存在"
} else {
    Add-Line "service\.env 不存在（会使用默认值）"
}
$rvcUrl = if ($env:RVC_URL) { $env:RVC_URL } else { "http://127.0.0.1:7868（默认）" }
$voiceId = if ($env:VOICE_ID) { $env:VOICE_ID } else { "(自动选第一个)" }
$ttsVoice = if ($env:TTS_VOICE) { $env:TTS_VOICE } else { "zh-CN-XiaoxiaoNeural（默认）" }
$pitch = if ($env:PITCH) { $env:PITCH } else { "0（默认）" }
$indexRate = if ($env:INDEX_RATE) { $env:INDEX_RATE } else { "0.75（默认）" }
Add-Line "RVC_URL=$rvcUrl"
Add-Line "VOICE_ID=$voiceId  TTS_VOICE=$ttsVoice  PITCH=$pitch  INDEX_RATE=$indexRate"

Add-Line ""
Add-Line "--- 端口 ---"
foreach ($p in @($RvcPort, $Port)) {
    $conn = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($conn) { Add-Line "端口 $p：监听中（PID $($conn[0].OwningProcess)）" }
    else { Add-Line "端口 $p：没有监听" }
}

Add-Line ""
Add-Line "--- 服务健康 ---"
try {
    $health = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 5
    Add-Line ($health | ConvertTo-Json -Depth 6)
} catch {
    Add-Line "桥接 /health 请求失败：$($_.Exception.Message)"
}
try {
    $rvcHealth = Invoke-RestMethod "http://127.0.0.1:$RvcPort/api/v1/app/health" -TimeoutSec 5
    Add-Line ("rvc-next： " + ($rvcHealth | ConvertTo-Json -Depth 4 -Compress))
} catch {
    Add-Line "rvc-next health 请求失败：$($_.Exception.Message)"
}
try {
    $voices = Invoke-RestMethod "http://127.0.0.1:$Port/voices" -TimeoutSec 5
    if ($voices.Count -eq 0) { Add-Line "音色：0 个（还没导入模型）" }
    else { $voices | ForEach-Object { Add-Line "音色：$($_.id) / $($_.version) / $($_.sample_rate)Hz" } }
} catch {
    Add-Line "音色列表请求失败：$($_.Exception.Message)"
}

Add-Line ""
Add-Line "--- 最近日志 ---"
foreach ($name in @("rvc-next.err.log", "bridge.err.log")) {
    $path = Join-Path $LogDir $name
    Add-Line "[$name]"
    if (Test-Path $path) {
        Get-Content $path -Tail 15 | ForEach-Object { Add-Line $_ }
    } else {
        Add-Line "(没有这个日志)"
    }
}

$lines | Set-Content $Report -Encoding UTF8
Write-Host ""
Write-Host "报告已保存：$Report" -ForegroundColor Green
