# 本机验证记录

> 记录时间：2026-10-28  
> 机器：Windows 11 / RTX 3050 Laptop 4GB / Python 3.12.1 / AstrBot 4.29.1

## 已实际验证

| 项目 | 结果 |
| --- | --- |
| 插件离线测试（`tests/test_plugin.py`） | 全部通过：会话开关、指令解析、服务不可达时回退文字 |
| 服务离线测试（`tests/test_service.py`） | 全部通过：情绪判定、配置读取、422/502 错误码 |
| `rvc-next` 启动 | 7868 端口就绪，加载 HuBERT 到 GPU |
| 桥接 `/health` | `ok: true`，返回 4 个音色（cyrene1 / cyrene_aiden / elysia / xilian） |
| 端到端合成（`POST /tts`） | 200，3.2 秒，输出 330KB wav |
| 情绪映射 | 文本「好开心」→ 响应头 `X-Emotion: happy`，语速 +8% / 音高 +2Hz |
| 插件调用服务 | 首次 3.1 秒，二次命中缓存 0ms，音色列表与健康检查正常 |
| 停止脚本 | 按端口找到两个进程并结束，端口释放 |
| `doctor.ps1` | 正常输出系统/GPU/端口/健康/日志报告 |
| PowerShell 语法 | 6 个脚本全部通过解析器检查 |
| 插件打包 | 生成 `dist/astrbot_plugin_tts_rvc-1.1.0.zip`（34KB） |

## 未验证 / 需要你确认

| 项目 | 原因 |
| --- | --- |
| 全新机器跑 `install.ps1` | 本机已装好 torch，没有重新下载 3GB 做干净验证；脚本逻辑已审查，镜像地址在本机验证过 |
| `install.ps1 -Cpu` | 没有 CPU 环境实测 |
| 真实 QQ 收发 | 本机没有启动 SnowLuma / QQ，语音段发送依赖 AstrBot 适配器 |
| rvc-next 面板导入路径 | 面板文案可能随版本变化，文档写的是通用步骤 |
| AstrBot 市场审核 | 尚未提交，审核要求以官方页面为准 |
| rvc-next 许可证 | 未逐条核对；本项目不打包其代码，仅运行时 pip 安装 |

## 复现命令

```bat
python tests\test_service.py
set ASTRBOT_ROOT=F:\QQbot\AstrBot
python tests\test_plugin.py
```

（用任意装了依赖的 Python；本机用的是 AstrBot 的 venv。）
