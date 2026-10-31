# TTS-RVC 语音服务一键安装（Windows）
# 用法：右键「使用 PowerShell 运行」，或在终端执行
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#
# 可选参数：
#   -Cpu            安装 CPU 版 torch（没有 N 卡时用，速度慢很多）
#   -Official       从 pytorch.org 官方源下载 torch（默认走国内镜像）
#   -SkipTorch      已经装过 torch，跳过下载
#   -PythonPath     指定 python.exe 路径
#   -IndexUrl       pip 包源，默认 PyPI
#   -TorchVersion   torch 版本，默认 2.9.1（留空装最新）
#   -RvcVersion     rvc-next 版本，默认 0.2.16（留空装最新）

param(
    [switch]$Cpu,
    [switch]$Official,
    [switch]$SkipTorch,
    [string]$PythonPath = "",
    [string]$IndexUrl = "https://pypi.org/simple",
    [string]$TorchVersion = "2.9.1",
    [string]$RvcVersion = "0.2.16"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$Runtime = Join-Path $Root "runtime"
$Venv = Join-Path $Runtime ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$ServiceDir = Join-Path $Root "service"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}
function Write-Ok([string]$Message) { Write-Host "  [OK] $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "  [!] $Message" -ForegroundColor Yellow }
function Fail([string]$Message) {
    Write-Host ""
    Write-Host "[X] $Message" -ForegroundColor Red
    Write-Host "    把上面的报错截图发出来，或者先运行 scripts\doctor.ps1 看看环境。" -ForegroundColor DarkGray
    exit 1
}

Write-Host "TTS-RVC 语音服务安装程序" -ForegroundColor Magenta
Write-Host "项目目录：$Root"

# ---------------------------------------------------------------- 1. Python
Write-Step "1/6 检查 Python"

$pyExe = ""
if ($PythonPath) {
    if (-not (Test-Path $PythonPath)) { Fail "指定的 Python 不存在：$PythonPath" }
    $pyExe = $PythonPath
} else {
    foreach ($version in @("3.12", "3.11", "3.10")) {
        try {
            $candidate = & py "-$version" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $candidate) {
                $pyExe = $candidate.Trim()
                break
            }
        } catch { }
    }
    if (-not $pyExe) {
        try {
            $candidate = & python -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $candidate) { $pyExe = $candidate.Trim() }
        } catch { }
    }
    if (-not $pyExe) {
        foreach ($fallback in @(
            "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
            "C:\Python312\python.exe",
            "C:\Python311\python.exe"
        )) {
            if (Test-Path $fallback) { $pyExe = $fallback; break }
        }
    }
}

if (-not $pyExe) {
    Fail "没有找到 Python 3.10 / 3.11 / 3.12。请先安装 Python 3.12 并勾选 Add to PATH：https://www.python.org/downloads/"
}

$versionInfo = & $pyExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$versionInfo = $versionInfo.Trim()
if ($versionInfo -notin @("3.10", "3.11", "3.12")) {
    Fail "当前 Python 是 $versionInfo，rvc-next 需要 3.10 / 3.11 / 3.12。请装 3.12 再试。"
}
Write-Ok "Python $versionInfo -> $pyExe"

# ---------------------------------------------------------------- 2. 虚拟环境
Write-Step "2/6 创建虚拟环境"
New-Item -ItemType Directory -Force $Runtime | Out-Null
New-Item -ItemType Directory -Force (Join-Path $Runtime "rvc-data") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $Runtime "logs") | Out-Null

if (-not (Test-Path $VenvPy)) {
    & $pyExe -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Fail "创建虚拟环境失败" }
    Write-Ok "虚拟环境已创建：$Venv"
} else {
    Write-Ok "复用已有虚拟环境：$Venv"
}

& $VenvPy -m pip install --upgrade pip wheel --quiet --index-url $IndexUrl
Write-Ok "pip 已就绪"

# ---------------------------------------------------------------- 3. torch
Write-Step "3/6 安装 PyTorch"
if (-not $Cpu -and -not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    Write-Warn "没有检测到 NVIDIA 驱动。没有 N 卡请改用：scripts\install.ps1 -Cpu"
}
if ($SkipTorch) {
    Write-Warn "已跳过 torch 安装"
} else {
    if ($Cpu) {
        $torchIndex = "https://download.pytorch.org/whl/cpu"
        Write-Warn "CPU 模式：RVC 能跑，但每句话可能要十几秒到几十秒"
    } elseif ($Official) {
        $torchIndex = "https://download.pytorch.org/whl/cu128"
    } else {
        $torchIndex = "https://mirrors.nju.edu.cn/pytorch/whl/cu128"
    }
    Write-Host "  下载源：$torchIndex（约 2.5~3 GB，请耐心等）"
    $torchSpec = if ($TorchVersion) { @("torch==$TorchVersion", "torchaudio==$TorchVersion") } else { @("torch", "torchaudio") }
    & $VenvPy -m pip install @torchSpec --index-url $torchIndex
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "torch 安装失败，可能是镜像临时不可用。"
        Write-Warn "可以重试：scripts\install.ps1 -Official"
        Fail "torch 没有安装成功"
    }
    Write-Ok "torch 安装完成"
}

# ---------------------------------------------------------------- 4. rvc-next
Write-Step "4/6 安装 rvc-next（换声引擎）"
$rvcSpec = if ($RvcVersion) { "rvc-next==$RvcVersion" } else { "rvc-next" }
& $VenvPy -m pip install $rvcSpec --index-url $IndexUrl
if ($LASTEXITCODE -ne 0) {
    Write-Warn "PyPI 直连失败，改用国内镜像重试..."
    & $VenvPy -m pip install $rvcSpec --index-url https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) { Fail "rvc-next 安装失败" }
}
Write-Ok "rvc-next 安装完成"

# ---------------------------------------------------------------- 5. 桥接依赖
Write-Step "5/6 安装语音服务依赖"
& $VenvPy -m pip install -r (Join-Path $ServiceDir "requirements.txt") --index-url $IndexUrl
if ($LASTEXITCODE -ne 0) { Fail "语音服务依赖安装失败" }
Write-Ok "fastapi / uvicorn / httpx 已就绪"

# ---------------------------------------------------------------- 6. 配置
Write-Step "6/6 生成配置并自检"
$envExample = Join-Path $ServiceDir ".env.example"
$envFile = Join-Path $ServiceDir ".env"
if ((-not (Test-Path $envFile)) -and (Test-Path $envExample)) {
    Copy-Item $envExample $envFile
    Write-Ok "已生成 service\.env（默认配置，可直接用）"
} elseif (Test-Path $envFile) {
    Write-Ok "保留已有 service\.env"
}

try {
    & $VenvPy -c "import torch, rvc_next, fastapi, uvicorn, httpx, pydantic; print('torch', torch.__version__, '| cuda', torch.cuda.is_available())"
} catch {
    Fail "依赖自检失败，请把上一行报错发出来"
}

Write-Host ""
Write-Host "安装完成。" -ForegroundColor Green
Write-Host ""
Write-Host "下一步：" -ForegroundColor Cyan
Write-Host "  1. 双击根目录的「启动语音服务.bat」"
Write-Host "  2. 浏览器打开 http://127.0.0.1:7868 ，在模型库里导入 .pth 和 .index"
Write-Host "  3. 浏览器打开 http://127.0.0.1:9880/health ，看到 ok: true 就成了"
Write-Host "  4. 在 AstrBot 面板安装本插件，申请开语音"
Write-Host ""
Write-Host "没有 N 卡？重新运行：scripts\install.ps1 -Cpu" -ForegroundColor DarkGray
