# tts-rvc-service

AstrBot 插件的配套声音服务：**文字 → edge-tts 干声 → RVC 换声 → wav**。

它把 `rvc-next`（RVC 推理）包装成一个简单的 HTTP 接口，插件只认 `/tts`，不关心底层模型。

## 启动

正常情况下用根目录的「启动语音服务.bat」，它会同时拉起 rvc-next 和本服务。

单独调试：

```bat
cd service
..\runtime\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 9880
```

依赖：

```bat
..\runtime\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 服务信息 |
| GET | `/health` | 桥接 + rvc-next 健康状态、音色 id 列表 |
| GET | `/voices` | 音色列表（id、采样率、版本、是否有索引） |
| GET | `/emotions` | 情绪规则与关键词 |
| POST | `/tts` | 合成语音，返回 `audio/wav` |

### POST /tts

```json
{
  "text": "你好呀，今天过得怎么样？",
  "voice_id": "xilian",
  "emotion": null
}
```

- `text`：必填，1 ~ `MAX_TEXT_CHARS` 字；
- `voice_id`：可选，空则用 `VOICE_ID`，再空则用模型库第一个；
- `emotion`：可选，`happy` / `excited` / `gentle` / `neutral`，空则按关键词自动判断。

响应头：

| 头 | 含义 |
| --- | --- |
| `X-TTS-Seconds` | 总耗时 |
| `X-Queue-Seconds` | 排队耗时 |
| `X-Emotion` | 实际使用的情绪 |
| `X-Emotion-Rate` / `X-Emotion-Pitch` | 情绪带来的语速/音高偏移 |
| `X-Voice` | 实际使用的音色 |

示例：

```bat
curl -X POST http://127.0.0.1:9880/tts ^
  -H "Content-Type: application/json" ^
  -d "{\"text\":\"你好呀\"}" ^
  --output test.wav
```

## 错误码

| 状态 | 场景 |
| --- | --- |
| 404 | 指定的 `voice_id` 不存在 |
| 409 | rvc-next 里还没有任何模型 |
| 422 | 文本为空 / 超长 |
| 502 | rvc-next 不可达、TTS 失败、RVC 任务失败 |
| 504 | RVC 任务超时（`JOB_TIMEOUT`） |

## 设计说明

- **串行**：全局 `asyncio.Lock`，同一时间只合成一条。4GB 显存下这是最稳的；代价是多人同时用要排队。
- **情绪**：纯关键词计数，不调用额外模型；平票或没命中按中性；低落不表达。
- **音色解析**：请求 > `VOICE_ID` > 模型库第一个，并在 404 时返回可用列表。
- **预热**：`WARMUP=1` 时启动后自动合成一句，把模型加载进显存（默认关，由启动脚本决定）。
- **无状态**：不保存音频，输出文件由 rvc-next 管理；插件侧另有缓存。

## 环境变量

见 [`.env.example`](.env.example)。改完 `service/.env` 后重启服务生效。
