# 常见问题（FAQ）

> 排错顺序：`/语音状态` → <http://127.0.0.1:9880/health> → `runtime\logs\bridge.err.log` → `runtime\logs\rvc-next.err.log` → 双击 `环境自检.bat`。

---

## 一、回复还是文字

按顺序检查：

1. **会话开关**：群里是否发过 `/打开语音`？发 `/语音状态` 看「语音：开/关」。
2. **插件总开关**：AstrBot 面板 → 插件 → TTS-RVC → `enabled` 是否开着。
3. **文本长度**：默认 2~150 字，太短或太长都会跳过（这是故意的）。
4. **只有模型回复才转**：`only_llm` 开着时，指令回复不会变语音。
5. **服务是否健康**：浏览器开 <http://127.0.0.1:9880/health>，`"ok": true` 才算正常。
6. **看日志**：`AstrBot` 的 `run-stdout.log` 搜 `[tts_rvc]`，会写明失败原因。

> 服务挂掉时插件会「熔断」30 秒，期间直接发文字，不会每条都卡 90 秒。

---

## 二、语音服务起不来

### 双击后窗口一闪而过

用 `cmd` 进项目目录手动跑，能看到完整报错：

```bat
powershell -ExecutionPolicy Bypass -File scripts\start-service.ps1
```

### 报 `No module named torch` / `rvc_next`

虚拟环境没装好。重新双击 `安装语音服务.bat`，或：

```bat
runtime\.venv\Scripts\python.exe -m pip install rvc-next
```

### 报 `CUDA out of memory`

- 4GB 显存很紧：关掉游戏、浏览器硬件加速、其他 AI 程序；
- 确认没有同时跑两个 rvc-next；
- 实在不够就换 CPU 模式：`install.ps1 -Cpu`。

### torch 装了但 `cuda False`

- 显卡驱动太旧：去 NVIDIA 官网更新驱动；
- 装成了 CPU 版：重跑 `install.ps1`（默认 CUDA 镜像）。

---

## 三、端口被占用

### 7868 被占用

改端口要同时改两处：

1. `service\.env` 里 `RVC_URL=http://127.0.0.1:7869`；
2. 启动脚本参数 `-RvcPort 7869`。

最省事的办法：找到占用 7868 的程序结束它。

```bat
netstat -ano | findstr :7868
taskkill /PID <上面的PID> /F
```

### 9880 被占用

同理，改 `service\.env` 的 `PORT`，并让插件 `service_url` 指向新端口。

---

## 四、合成失败 / edge-tts 报错

- edge-tts 需要**联网**（微软在线服务）。断网、公司代理、DNS 污染都会失败；
- 错误日志里出现 `ClientConnectorError` / `403`：先换网络或开代理再试；
- 完全离线环境：本插件默认链路走不通，需要把 TTS 换成 GPT-SoVITS 等本地引擎（不在本项目范围）。

---

## 五、声音不像 / 有杂音

见 [MODELS.md](MODELS.md) 第五节的调参表。最常见的三个：

1. `PITCH` 不对（男声底转女声模型要 +12）；
2. `.index` 没导入或 `INDEX_RATE` 太低；
3. 模型本身质量一般。

---

## 六、太慢

| 场景 | 原因 | 处理 |
| --- | --- | --- |
| 第一条特别慢 | 模型加载/预热 | 正常，等一次就好 |
| 每条都 10 秒+ | CPU 模式 / 显存不足 | 换 CUDA、关占显存程序 |
| 空闲一段时间后变慢 | rvc-next 10 分钟自动卸载模型 | 正常，下一条会重新加载 |
| 群里多人同时用 | 服务串行排队 | 硬件限制，4GB 显存不建议并行 |

---

## 七、群聊刷屏

- 群聊默认**不发**语音，只有 `/打开语音` 后才发；
- 想限制长度：调低插件 `max_chars`（比如 60）；
- 想只发文字：`/关闭语音`；
- 想文字 + 语音：开 `dual_output`（注意消息条数会翻倍）。

---

## 八、缓存

- 同一段文字 + 同一音色会复用缓存，最多保留 200 个文件、24 小时；
- 缓存目录：`%TEMP%\astrbot_plugin_tts_rvc`；
- 想强制重新合成：改一个字，或在插件配置里关掉 `cache`。

---

## 九、安装插件失败

- AstrBot 版本要 **4.17+**；
- 从 zip 安装时，zip 根目录必须直接是 `main.py` / `metadata.yaml`（用 `scripts\package.ps1` 生成的就是对的）；
- 装完要**重启 AstrBot**；
- 看 `AstrBot\run-stdout.log` 里的加载报错。

---

## 十、日志都在哪

| 日志 | 位置 |
| --- | --- |
| 桥接输出 | `runtime\logs\bridge.out.log` |
| 桥接错误 | `runtime\logs\bridge.err.log` |
| rvc-next 错误 | `runtime\logs\rvc-next.err.log` |
| AstrBot | `AstrBot\run-stdout.log` |
| 自检报告 | `runtime\doctor-report.txt` |

---

## 十一、安全提醒

- `7868` / `9880` 只绑定 `127.0.0.1`，不要改成 `0.0.0.0` 暴露到公网；
- 面板密码自己改掉；
- SnowLuma 属于第三方协议方案，账号风险自担，建议小号。
