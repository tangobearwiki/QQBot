# 安装指南（从零开始）

> 目标是「照着点，不写代码」。全程大约 20~40 分钟，大部分时间在等下载。

## 0. 先确认三件事

1. 你的 Windows 是 64 位（Win10 / Win11 都行）；
2. 有 NVIDIA 显卡（任务管理器 → 性能 → GPU 里能看到 NVIDIA 字样）；
3. 机器人现在能正常回文字。

没有 N 卡也能装，只是要在安装时加 `-Cpu`，每句话会慢很多。

---

## 1. 安装 Python 3.12

1. 打开 <https://www.python.org/downloads/release/python-31210/>，下载 **Windows installer (64-bit)**；
2. 运行安装包，**第一页最下面勾选 `Add python.exe to PATH`**；
3. 点 `Install Now`，等它完成；
4. 按 `Win + R`，输入 `cmd` 回车，在黑窗口里输入：

```bat
python --version
```

看到 `Python 3.12.x` 就对了。如果提示「不是内部或外部命令」，说明 PATH 没勾上，重新运行安装包选 `Modify` 补勾。

> 已经装过 3.10 / 3.11 也可以，安装脚本会自动找。

---

## 2. 拿到项目文件

两种方式任选：

**方式 A：git 克隆（以后更新方便）**

```bat
git clone https://github.com/kuqiwang/astrbot_plugin_tts_rvc.git
```

**方式 B：下载 zip**

GitHub 仓库页面 → 绿色 `Code` 按钮 → `Download ZIP` → 解压到例如：

```
F:\TTS-RVC语音转换插件
```

> 路径可以有中文和空格，不影响使用。但 rvc-next 里的**模型 id 必须是英文**。

---

## 3. 安装语音服务

双击 **`安装语音服务.bat`**。

脚本会依次做这些事：

| 步骤 | 做什么 | 大概耗时 |
| --- | --- | --- |
| 1 | 检查 Python 版本 | 几秒 |
| 2 | 建 `runtime\.venv` 虚拟环境 | 30 秒 |
| 3 | 下载 torch（CUDA 版） | 10~30 分钟（看网速） |
| 4 | 安装 rvc-next | 2~5 分钟 |
| 5 | 安装 FastAPI 等 | 1 分钟 |
| 6 | 生成 `service\.env` 并自检 | 几秒 |

### 没有 N 卡 / 想用 CPU

打开 `cmd`，进入项目目录后运行：

```bat
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Cpu
```

### 下载太慢

- 默认走南京大学 PyTorch 镜像；如果镜像抽风，可以改官方源：

```bat
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Official
```

- pip 包源也可以换：

```bat
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -IndexUrl https://pypi.tuna.tsinghua.edu.cn/simple
```

### 安装中断了

直接重新双击 `安装语音服务.bat`。已经装好的部分会跳过，不会从头下载。

---

## 4. 启动并确认

1. 双击 **`启动语音服务.bat`**；
2. 看到下面几行就算成功：

```
[OK] rvc-next 已就绪
[OK] 语音桥接已就绪
[OK] 预热成功（...）
语音服务已启动：
```

3. 浏览器打开：

| 地址 | 应该看到 |
| --- | --- |
| <http://127.0.0.1:7868> | rvc-next 面板 |
| <http://127.0.0.1:9880/health> | `"ok": true`，以及 `voices` 列表 |
| <http://127.0.0.1:9880/voices> | 音色数组 |

> 第一次启动会慢 30~90 秒（加载依赖 + 预热模型）；之后每条语音 3~5 秒。

---

## 5. 导入 RVC 模型

详见 [MODELS.md](MODELS.md)。最短路径：

1. 浏览器打开 <http://127.0.0.1:7868>；
2. 左侧 **模型库** → **导入**；
3. 把 `.pth` 和 `.index` 拖进去；
4. **id 填纯英文**（如 `xilian`），提交；
5. 回到 <http://127.0.0.1:9880/voices> 能看到它。

---

## 6. 安装 AstrBot 插件

**方式 A：面板上传 zip**

在项目目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\package.ps1
```

生成 `dist\astrbot_plugin_tts_rvc-1.1.0.zip`，然后在 AstrBot 面板：

```
插件 → 从文件安装 → 选择 zip → 重启 AstrBot
```

**方式 B：手动复制**

把下面 4 个文件复制到 `AstrBot\data\plugins\astrbot_plugin_tts_rvc\`：

```
main.py
metadata.yaml
_conf_schema.json
requirements.txt
```

重启 AstrBot。

**方式 C：市场安装**

等插件上架后，在 AstrBot 面板 → 插件 → 市场搜索「TTS-RVC」。

---

## 7. 第一次说话

1. 在 AstrBot 面板里确认插件已启用；
2. 用另一个 QQ 号私聊机器人：发「你好」，应收到一条语音；
3. 群聊里先发 `/打开语音`；
4. 发 `/语音状态`，应该显示「服务：正常，N 个音色可用」。

如果还是文字，看 [FAQ.md](FAQ.md) 的「回复还是文字」。

---

## 8. 可选：开机自启

**方法：任务计划程序**

1. `Win + R` → `taskschd.msc`；
2. 右侧「创建任务」；
3. 常规：名称 `TTS-RVC 语音服务`，勾选「不管用户是否登录都要运行」；
4. 触发器：新建 → 「登录时」；
5. 操作：新建 → 程序填 `powershell.exe`，参数填：

```
-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "F:\TTS-RVC语音转换插件\scripts\start-service.ps1" -SkipWarmup
```

6. 条件：取消「只有在计算机使用交流电源时才启动」；
7. 保存。重启后服务会在后台静默启动，日志仍在 `runtime\logs`。

> 路径按你实际目录改。开机自启时机器人可能比服务先起来，插件会自动回退文字，服务好了自然恢复语音。

---

## 9. 升级 / 卸载

**升级**

```bat
git pull
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

然后再双击「启动语音服务.bat」。

**卸载**

1. 双击 `停止语音服务.bat`；
2. AstrBot 面板卸载插件；
3. 删掉整个项目目录（`runtime\.venv` 和模型占空间最大）。
