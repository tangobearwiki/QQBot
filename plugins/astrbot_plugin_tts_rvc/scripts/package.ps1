# 打包成 AstrBot 可手动安装的 zip（不包含语音服务与运行时）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\package.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$Dist = Join-Path $Root "dist"
New-Item -ItemType Directory -Force $Dist | Out-Null

$version = "1.1.0"
$meta = Get-Content (Join-Path $Root "metadata.yaml") -Raw
if ($meta -match "version:\s*([0-9]+\.[0-9]+\.[0-9]+)") {
    $version = $Matches[1]
}

$zip = Join-Path $Dist "astrbot_plugin_tts_rvc-$version.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }

$items = @(
    "main.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "README.md",
    "LICENSE"
)
foreach ($optional in @("logo.png", "logo.jpg")) {
    if (Test-Path (Join-Path $Root $optional)) { $items += $optional }
}

$temp = Join-Path $Dist "_pack"
if (Test-Path $temp) { Remove-Item $temp -Recurse -Force }
New-Item -ItemType Directory -Force $temp | Out-Null

foreach ($item in $items) {
    Copy-Item (Join-Path $Root $item) (Join-Path $temp $item)
}

Compress-Archive -Path (Join-Path $temp "*") -DestinationPath $zip -CompressionLevel Optimal
Remove-Item $temp -Recurse -Force

Write-Host "已生成：$zip" -ForegroundColor Green
Write-Host "在 AstrBot 面板 → 插件 → 从文件安装，选择这个 zip 即可。"
