# 离线测试怎么跑

这些测试**不需要显卡、不需要启动语音服务**。

## 服务端

```bat
python tests\test_service.py
```

用任意装了 `fastapi / httpx / pydantic` 的 Python 都行（比如 AstrBot 的 venv）。

## 插件

插件测试要能 import 到 AstrBot：

```bat
set ASTRBOT_ROOT=F:\QQbot\AstrBot
F:\QQbot\AstrBot\.venv\Scripts\python.exe tests\test_plugin.py
```

脚本会用 `ASTRBOT_ROOT` 环境变量（默认 `F:\QQbot\AstrBot`）找到 AstrBot，
并把会话状态写到临时目录，不会污染真实配置。

## 说明

- 这里只验证纯逻辑：情绪判定、配置解析、会话开关、指令参数、失败回退；
- 真正的 TTS + RVC 链路需要 GPU 和模型，见根目录 README 的端到端验证步骤。
