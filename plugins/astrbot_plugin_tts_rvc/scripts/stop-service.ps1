# 停止语音服务：先按 PID，再按端口兜底
# 用法：powershell -ExecutionPolicy Bypass -File scripts\stop-service.ps1

param(
    [int]$RvcPort = 7868,
    [int]$Port = 9880
)

$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path $PSScriptRoot -Parent
$Runtime = Join-Path $Root "runtime"
$PidFile = Join-Path $Runtime "pids.json"

function Stop-Tree([int]$ProcessId) {
    if ($ProcessId -le 0) { return $false }
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    Write-Host "  停止进程 $ProcessId（$($process.ProcessName)）"
    & taskkill /PID $ProcessId /T /F 2>&1 | Out-Null
    return $true
}

Write-Host "停止 TTS-RVC 语音服务" -ForegroundColor Magenta

$stopped = $false
if (Test-Path $PidFile) {
    try {
        $pids = Get-Content $PidFile -Raw | ConvertFrom-Json
        foreach ($name in @("rvc", "bridge")) {
            $id = $pids.$name
            if ($id -and (Stop-Tree ([int]$id))) { $stopped = $true }
        }
    } catch {
        Write-Host "  [!] pids.json 读取失败，改为按端口清理" -ForegroundColor Yellow
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

foreach ($port in @($RvcPort, $Port)) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $conns) {
        if (Stop-Tree ([int]$conn.OwningProcess)) { $stopped = $true }
    }
}

Start-Sleep -Seconds 1
$left = @()
foreach ($port in @($RvcPort, $Port)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        $left += $port
    }
}

if ($left.Count -gt 0) {
    Write-Host ""
    Write-Host "[!] 端口仍被占用：$($left -join ', ')" -ForegroundColor Yellow
    Write-Host "    可能还有别的程序在用这些端口，打开任务管理器结束对应进程即可。" -ForegroundColor DarkGray
} else {
    Write-Host "语音服务已全部停止。" -ForegroundColor Green
}
