# 启动语音服务：rvc-next（7868）+ 桥接（9880）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\start-service.ps1

param(
    [switch]$SkipWarmup,
    [int]$RvcPort = 7868,
    [int]$Port = 9880
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$Runtime = Join-Path $Root "runtime"
$Venv = Join-Path $Runtime ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$RvcExe = Join-Path $Venv "Scripts\rvc-next.exe"
$ServiceDir = Join-Path $Root "service"
$LogDir = Join-Path $Runtime "logs"
$PidFile = Join-Path $Runtime "pids.json"

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host "  [OK] $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "  [!] $Message" -ForegroundColor Yellow }

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

function Test-Port([int]$Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return $null -ne $conn
}

function Wait-Url([string]$Url, [int]$Seconds, [string]$Name) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-RestMethod $Url -TimeoutSec 3 | Out-Null
            return $true
        } catch {
            Start-Sleep -Milliseconds 800
        }
    }
    Write-Warn "$Name 在 $Seconds 秒内没有就绪"
    return $false
}

Write-Host "启动 TTS-RVC 语音服务" -ForegroundColor Magenta

if (-not (Test-Path $VenvPy)) {
    Write-Host "还没有安装。请先双击「安装语音服务.bat」。" -ForegroundColor Red
    exit 1
}
New-Item -ItemType Directory -Force $LogDir | Out-Null
New-Item -ItemType Directory -Force (Join-Path $Runtime "rvc-data") | Out-Null

# 读取 service/.env，作为两个子进程的环境变量
Import-EnvFile (Join-Path $ServiceDir ".env")
$env:RVC_NEXT_DATA_DIR = Join-Path $Runtime "rvc-data"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:RVC_URL = "http://127.0.0.1:$RvcPort"

$pids = @{}

# ---------------------------------------------------------------- rvc-next
if (Test-Port $RvcPort) {
    Write-Ok "rvc-next 已在运行（端口 $RvcPort）"
} else {
    if (-not (Test-Path $RvcExe)) {
        Write-Host "找不到 rvc-next.exe，请重新运行安装脚本。" -ForegroundColor Red
        exit 1
    }
    Write-Step "启动 rvc-next（端口 $RvcPort）"
    $rvc = Start-Process -FilePath $RvcExe `
        -ArgumentList @("webui", "--host", "127.0.0.1", "--port", "$RvcPort", "--no-open", "--strict-port") `
        -WorkingDirectory $Runtime `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "rvc-next.out.log") `
        -RedirectStandardError (Join-Path $LogDir "rvc-next.err.log") `
        -PassThru
    $pids["rvc"] = $rvc.Id
    Write-Host "  等待模型服务就绪（首次启动要加载依赖，约 30~90 秒）..."
    if (-not (Wait-Url "http://127.0.0.1:$RvcPort/api/v1/app/health" 120 "rvc-next")) {
        Write-Warn "查看日志：runtime\logs\rvc-next.err.log"
    } else {
        Write-Ok "rvc-next 已就绪"
    }
}

# ---------------------------------------------------------------- 桥接
if (Test-Port $Port) {
    Write-Ok "语音桥接已在运行（端口 $Port）"
} else {
    Write-Step "启动语音桥接（端口 $Port）"
    $bridge = Start-Process -FilePath $VenvPy `
        -ArgumentList @("-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "$Port") `
        -WorkingDirectory $ServiceDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "bridge.out.log") `
        -RedirectStandardError (Join-Path $LogDir "bridge.err.log") `
        -PassThru
    $pids["bridge"] = $bridge.Id
    if (-not (Wait-Url "http://127.0.0.1:$Port/health" 40 "语音桥接")) {
        Write-Warn "查看日志：runtime\logs\bridge.err.log"
    } else {
        Write-Ok "语音桥接已就绪"
    }
}

# 合并已有 PID
if (Test-Path $PidFile) {
    try {
        $old = Get-Content $PidFile -Raw | ConvertFrom-Json
        foreach ($name in @("rvc", "bridge")) {
            if ($old.$name) { $pids[$name] = $old.$name }
        }
    } catch { }
}
$pids | ConvertTo-Json | Set-Content $PidFile -Encoding UTF8

# ---------------------------------------------------------------- 预热
if (-not $SkipWarmup) {
    Write-Step "预热语音模型（第一次约 60 秒，之后每条 3~5 秒）"
    try {
        $body = @{ text = "你好呀，语音服务已经准备就绪。" } | ConvertTo-Json
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/tts" -Method Post `
            -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body)) `
            -TimeoutSec 180 -UseBasicParsing
        if ($resp.StatusCode -eq 200) {
            $emotion = $resp.Headers["X-Emotion"]
            $seconds = $resp.Headers["X-TTS-Seconds"]
            Write-Ok "预热成功（情绪：$emotion，耗时 ${seconds}s）"
        }
    } catch {
        Write-Warn "预热失败：$($_.Exception.Message)"
        Write-Warn "多半是还没导入 RVC 模型，先打开 http://127.0.0.1:7868 导入模型。"
    }
}

Write-Host ""
Write-Host "语音服务已启动：" -ForegroundColor Green
Write-Host "  rvc-next  http://127.0.0.1:$RvcPort"
Write-Host "  桥接      http://127.0.0.1:$Port/health"
Write-Host "  日志      $LogDir"
Write-Host ""
Write-Host "这个窗口可以关掉，服务在后台运行。停止请双击「停止语音服务.bat」。" -ForegroundColor DarkGray
