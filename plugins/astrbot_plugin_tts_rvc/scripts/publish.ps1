# 首次发布 / 更新到 GitHub
# 用法：powershell -ExecutionPolicy Bypass -File scripts\publish.ps1
#
# 第一次 push 时，Windows 会弹出 GitHub 登录窗口（浏览器里操作），
# 登录凭据由系统凭据管理器保存，不会经过任何脚本或聊天记录。

param(
    [string]$RepoUrl = "https://github.com/kuqiwang/astrbot_plugin_tts_rvc.git",
    [string]$UserName = "kuqiwang",
    [string]$UserEmail = "770282781@qq.com"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent

$Git = $null
$found = Get-Command git -ErrorAction SilentlyContinue
if ($found) { $Git = $found.Source }
if (-not $Git) {
    foreach ($candidate in @(
        "$env:ProgramFiles\Git\cmd\git.exe",
        "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe",
        "${env:ProgramFiles(x86)}\Git\cmd\git.exe"
    )) {
        if (Test-Path $candidate) { $Git = $candidate; break }
    }
}
if (-not $Git) {
    Write-Host "没有找到 git。请先安装 Git for Windows：" -ForegroundColor Red
    Write-Host "  winget install --id Git.Git -e --source winget" -ForegroundColor Yellow
    Write-Host "或者从 https://git-scm.com/download/win 下载安装，然后重新运行本脚本。"
    exit 1
}

Set-Location $Root

if (-not (Test-Path (Join-Path $Root ".git"))) {
    Write-Host "初始化 git 仓库..." -ForegroundColor Cyan
    & $Git init -b main | Out-Null
}

& $Git config user.name $UserName
& $Git config user.email $UserEmail
& $Git config core.quotepath false

$remotes = & $Git remote
if ($remotes -notcontains "origin") {
    & $Git remote add origin $RepoUrl
    Write-Host "已配置远程仓库：$RepoUrl"
} else {
    $current = & $Git remote get-url origin
    if ($current -ne $RepoUrl) {
        & $Git remote set-url origin $RepoUrl
        Write-Host "已更新远程仓库：$RepoUrl"
    }
}

Write-Host "暂存文件..." -ForegroundColor Cyan
& $Git add -A

$status = & $Git status --porcelain
if ($status) {
    Write-Host "创建提交..." -ForegroundColor Cyan
    $message = "feat: TTS-RVC 语音回复插件 v1.1.0（服务、安装脚本、文档）"
    & $Git commit -m $message
} else {
    Write-Host "没有新的改动，跳过提交。"
}

Write-Host ""
Write-Host "准备推送到 GitHub。" -ForegroundColor Cyan
Write-Host "如果弹出登录窗口，请用浏览器完成 GitHub 登录（只需一次）。" -ForegroundColor Yellow
Write-Host ""

$branch = & $Git rev-parse --abbrev-ref HEAD
& $Git push -u origin $branch

Write-Host ""
if ($LASTEXITCODE -eq 0) {
    Write-Host "发布成功：https://github.com/kuqiwang/astrbot_plugin_tts_rvc" -ForegroundColor Green
} else {
    Write-Host "推送失败。常见原因：" -ForegroundColor Red
    Write-Host "  1. 仓库地址或用户名写错；"
    Write-Host "  2. GitHub 登录窗口被关掉；"
    Write-Host "  3. 网络连不上 github.com（可开代理后重试）。"
    Write-Host "把完整报错发给昔涟就行。"
}
