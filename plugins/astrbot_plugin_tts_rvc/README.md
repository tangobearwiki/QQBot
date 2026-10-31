# TTS-RVC 语音回复

![TTS-RVC](logo.png)

> 让你的 QQ 机器人用**你自己的 RVC 模型**说话。  
> 大模型负责想，这里负责把文字变成「她的声音」。

配套链路：**DeepSeek 回复 → edge-tts 干声 → RVC 换声 → QQ 语音**。

- 插件很轻：不装 torch，不占显存，只做「拦截回复 / 请求服务 / 失败回退文字」
- 声音服务独立：`rvc-next` + FastAPI 桥接，只监听 `127.0.0.1`
- 支持任意 RVC v2 模型（`.pth` + 可选 `.index`），换音色不用改代码
- 私聊 / 群聊独立开关，会话级音色切换
- 情绪映射：开心 / 兴奋 / 温柔（低落不表达）

> 当前版本：`v1.1.0`　适用：AstrBot 4.17+ / aiocqhttp（SnowLuma、NapCat）

---

## 一、你需要准备什么

| 项目 | 要求 | 说明 |
| --- | --- | --- |
| 系统 | Windows 10 / 11 64 位 | 本文默认 Windows |
| 显卡 | NVIDIA，显存 4GB 起 | 没有也能 CPU 跑，但每句话要十几秒 |
| Python | 3.10 / 3.11 / 3.12 | 推荐 3.12，安装时勾选 **Add to PATH** |
| 机器人 | AstrBot + SnowLuma 或 NapCat | 已经能正常文字聊天 |
| 模型 | RVC v2 的 `.pth`（建议带 `.index`） | 自己训练或下载，版权自负 |

> 磁盘预留 **8GB** 左右（torch 约 2.5~3GB，模型每个几十 MB）。

---

## 二、快速开始

### 第 1 步：安装语音服务

1. 把本项目放到任意目录，例如 `F:\TTS-RVC语音转换插件`（路径可以有中文）。
2. 双击 **`安装语音服务.bat`**。
   - 自动创建 `runtime\.venv`，安装 torch、rvc-next、FastAPI；
   - 下载量约 3GB，第一次请耐心等；中断了重跑即可，会跳过已完成部分。
   - 没有 N 卡：在终端运行 `scripts\install.ps1 -Cpu`。
3. 双击 **`启动语音服务.bat`**，看到「语音服务已启动」就对了。
4. 浏览器打开 <http://127.0.0.1:7868> 进入 rvc-next 面板：
   - 模型库 → 导入 → 选择你的 `.pth` 和 `.index`；
   - 导入后给它一个**纯英文 id**（例如 `xilian`），中文 id 会让 faiss 打不开。
5. 浏览器打开 <http://127.0.0.1:9880/health>，确认返回里 `"ok": true`。

> 服务启动后会预热一次模型（约 60 秒），之后每条语音 3~5 秒（RTX 3050 4GB 实测）。

### 第 2 步：安装 AstrBot 插件

**方式 A：面板上传（推荐新手）**

1. 在项目目录运行 `scripts\package.ps1`，生成 `dist\astrbot_plugin_tts_rvc-1.1.0.zip`；
2. AstrBot 面板 → **插件** → **从文件安装** → 选择这个 zip；
3. 重启 AstrBot，插件列表里出现「TTS-RVC 语音回复」。

**方式 B：市场安装**

插件发布到 AstrBot 市场后，在面板 → 插件 → 市场里搜索「TTS-RVC」即可。

**方式 C：手动放目录**

把本仓库的 `main.py`、`metadata.yaml`、`_conf_schema.json`、`requirements.txt` 复制到：

```
AstrBot\data\plugins\astrbot_plugin_tts_rvc\
```

然后重启 AstrBot。

### 第 3 步：让它开口

- **私聊**：直接发消息，默认就会回语音。
- **群聊**：先发 `/打开语音`，之后这个群才回语音（默认保持文字，避免刷屏）。
- 查看可用音色：`/voice_list`
- 切换音色：`/voice_set xilian`
- 测试一条：`/voice_test 你好呀`

---

## 三、指令一览

| 指令 | 作用 | 说明 |
| --- | --- | --- |
| `/打开语音` | 当前会话开始语音回复 | 群聊里最常用 |
| `/关闭语音` | 当前会话恢复文字 | 设置会保存 |
| `/语音状态` | 看开关、音色、服务健康 | 排错第一步 |
| `/voice_list` | 列出服务里的 RVC 音色 | 标出当前音色 |
| `/voice_set <id>` | 为当前会话切换音色 | id 来自 `/voice_list` |
| `/voice_reset` | 恢复默认音色 | |
| `/voice_test [文字]` | 合成一条测试语音 | 不传文字用默认句子 |
| `/voice_help` | 查看帮助 | |

> 群聊里需要 `/` 前缀；私聊可以直接发指令。开启「只有管理员能切换」后，前三个指令仅管理员可用。

---

## 四、配置

### 插件配置

AstrBot 面板 → 插件 → TTS-RVC 语音回复 → 配置：

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | 开 | 总开关 |
| `service_url` | `http://127.0.0.1:9880/tts` | 声音服务地址 |
| `voice_id` | 空 | 默认音色；空 = 服务端决定 |
| `private_only` | 开 | 私聊默认语音 |
| `group_default` | 关 | 群聊默认语音 |
| `admin_only` | 关 | 只有管理员能切换 |
| `dual_output` | 关 | 文字 + 语音都发 |
| `only_llm` | 开 | 只转换大模型回复 |
| `min_chars` / `max_chars` | 2 / 150 | 过短过长不转语音 |
| `timeout` | 90 | 合成超时（秒） |
| `cache` | 开 | 同文本同音色复用缓存 |

### 服务端配置

`service\.env`（首次安装自动生成，改完重启服务生效）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `RVC_URL` | `http://127.0.0.1:7868` | rvc-next 地址 |
| `VOICE_ID` | 空 | 默认音色；空 = 模型库第一个 |
| `TTS_VOICE` | `zh-CN-XiaoxiaoNeural` | edge-tts 底声 |
| `PITCH` | 0 | 变调半音，同性别 0 |
| `INDEX_RATE` | 0.75 | 索引贴合度 |
| `PROTECT` | 0.33 | 齿音保护 |
| `F0_METHOD` | rmvpe | 音高提取算法 |
| `EMOTION_ENABLED` | 1 | 情绪映射开关 |
| `EMOTION_SCALE` | 1.0 | 情绪强度 |
| `MAX_TEXT_CHARS` | 500 | 单条上限 |
| `WARMUP` | 0 | 启动时预热 |

---

## 五、情绪是怎么做的

不额外调用模型，纯关键词计数，拿最稳的那一档：

| 情绪 | 触发词示例 | 效果 |
| --- | --- | --- |
| 兴奋 | 哇、天哪、太厉害、真的吗 | 语速 +12%，音高 +3Hz |
| 开心 | 开心、好耶、哈哈、喜欢 | 语速 +8%，音高 +2Hz |
| 温柔 | 别怕、晚安、抱抱、辛苦 | 语速 -6%，音高 -1Hz |
| 低落 | —— | **不表达**，按中性处理 |

> 想关掉：`service\.env` 里 `EMOTION_ENABLED=0`，重启服务。

---

## 六、为什么分「插件 + 服务」两段

```
QQ ←→ SnowLuma/NapCat ←→ AstrBot ←→ 本插件
                                      │ HTTP
                                      ▼
                         tts-rvc-service (9880)
                                      │
                                      ▼
                          rvc-next (7868) + edge-tts
```

- **插件不装 torch**：AstrBot 的 venv 保持干净，升级 AstrBot 不会被 CUDA 依赖拖垮；
- **服务独立进程**：崩了只影响语音，机器人照样文字聊天；
- **rvc-next 不进仓库**：它有自己的许可证，由安装脚本从 PyPI 装，边界清楚；
- **只监听 `127.0.0.1`**：默认不暴露到局域网/公网。

---

## 七、常见问题

| 现象 | 先看这里 |
| --- | --- |
| 回复还是文字 | `/语音状态` → 服务是否「正常」；`runtime\logs\bridge.err.log` |
| 服务启动失败 | 双击 `环境自检.bat`，把 `runtime\doctor-report.txt` 发出来 |
| 没有声音/音色为空 | 先导入 RVC 模型；`/voice_list` 确认 |
| 导入模型报错 | 见 [docs/MODELS.md](docs/MODELS.md) |
| 很慢 / 爆显存 | 4GB 显存只能串行；关掉别的占显存程序 |
| 群聊不回复语音 | 群里先 `/打开语音` |
| 想换底声 | 改 `TTS_VOICE`，男声底 → 女声模型要 `PITCH=12` 左右 |

完整排错见 [docs/FAQ.md](docs/FAQ.md)，安装细节见 [docs/INSTALL.md](docs/INSTALL.md)。

---

## 八、卸载

1. 双击 `停止语音服务.bat`；
2. AstrBot 面板卸载插件；
3. 删除本目录（`runtime` 里有几个 GB 的 torch 和模型，记得一起删）。

---

## 九、隐私与免责

- 文字会发送给 **Microsoft edge-tts** 在线服务合成干声；介意的话可以换成 GPT-SoVITS 等本地 TTS，但本插件默认走 edge-tts。
- **RVC 模型版权由使用者自负**：请只使用你有权使用的模型（自己训练、已获授权、或允许二次创作的作品）。
- SnowLuma 属于第三方 QQ 协议方案，存在账号风险，建议用小号。
- 不要把 `7868` / `9880` 暴露到公网；面板与桥接都只绑定本机。

---

## 十、目录结构

```
.
├─ main.py                  # AstrBot 插件入口
├─ metadata.yaml            # 市场元数据
├─ _conf_schema.json        # 插件配置项
├─ 安装语音服务.bat          # 双击安装
├─ 启动语音服务.bat
├─ 停止语音服务.bat
├─ 环境自检.bat
├─ service/
│  ├─ app.py                # TTS + RVC 桥接（FastAPI）
│  ├─ requirements.txt
│  └─ .env.example
├─ scripts/
│  ├─ install.ps1
│  ├─ start-service.ps1
│  ├─ stop-service.ps1
│  ├─ doctor.ps1
│  ├─ package.ps1
│  └─ publish.ps1
├─ docs/
│  ├─ INSTALL.md
│  ├─ MODELS.md
│  ├─ FAQ.md
│  └─ PUBLISH.md
└─ tests/
```

---

## License

插件与桥接代码：MIT。  
`rvc-next`、`edge-tts` 等依赖遵循各自许可证；本项目不打包它们的代码或模型。
