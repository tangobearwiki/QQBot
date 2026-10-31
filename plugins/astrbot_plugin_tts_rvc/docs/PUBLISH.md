# 发布到 GitHub 与 AstrBot 市场

仓库地址：<https://github.com/kuqiwang/astrbot_plugin_tts_rvc>

## 一、发布前检查清单

- [ ] `metadata.yaml` 里的 `repo`、`social_link`、`version` 正确；
- [ ] `main.py` 里的 `@register` 版本号和 `metadata.yaml` 一致；
- [ ] `README.md` 能独立看懂（市场页面会直接展示它）；
- [ ] `logo.png` 存在（512×512，市场列表会用）；
- [ ] 没有提交 `service/.env`、`runtime/`、日志、模型文件；
- [ ] 本地测试通过：`tests/test_service.py`、`tests/test_plugin.py`。

## 二、推送到 GitHub

第一次：

1. 确认仓库已创建：<https://github.com/kuqiwang/astrbot_plugin_tts_rvc>（public、空白仓库）；
2. 双击根目录 **`发布到GitHub.bat`**；
3. 如果弹出 GitHub 登录窗口，在浏览器里点授权（只需一次，凭据存进 Windows 凭据管理器）；
4. 看到 `发布成功` 即可。

之后每次更新：

1. 改版本号（`metadata.yaml` 和 `main.py` 的 `@register` 一起改）；
2. 再双击一次 `发布到GitHub.bat`。

> 脚本做的事：`git init`（首次）→ 配置用户名 → `git add -A` → `git commit` → `git push`。  
> 不会碰你的密码；认证由 Windows 凭据管理器完成。

## 三、提交 AstrBot 插件市场

1. 打开 AstrBot 插件市场（面板 → 插件 → 市场，或 <https://cloud.astrbot.app>）；
2. 登录后找到「发布插件 / 提交插件」入口；
3. 填写：
   - 插件名称：`astrbot_plugin_tts_rvc`
   - 显示名：`TTS-RVC 语音回复`
   - 仓库地址：`https://github.com/kuqiwang/astrbot_plugin_tts_rvc`
   - 分类：语音 / TTS
4. 提交，等待审核（通常 1~3 个工作日）。

> 市场页面可能更新，具体以页面提示为准。审核被退回时，会说明原因，改完重新提交即可。

## 四、审核最容易卡住的点

| 问题 | 处理 |
| --- | --- |
| README 太短/只有代码 | 面向用户写清楚「这是什么、怎么装、怎么用」 |
| 仓库里带模型或密钥 | 模型由用户自备；`.env` 已在 `.gitignore` |
| 许可证缺失 | 仓库根目录有 `LICENSE`（MIT） |
| 版本号不合法 | 用 `1.1.0` 这种三段式，更新时递增 |
| 不支持声明的平台 | `metadata.yaml` 只声明 `aiocqhttp` |
| 仓库私有/打不开 | 必须是 public |

## 五、版本号约定

- 修 bug：`1.1.1`
- 加功能：`1.2.0`
- 不兼容改动：`2.0.0`

发版时同步更新：

1. `metadata.yaml` → `version`
2. `main.py` → `@register(..., "1.1.0", ...)`
3. `README.md` → 顶部版本号
4. 重新打 zip（`scripts/package.ps1`）

## 六、市场之外的发布方式

- **AstrBot 面板从文件安装**：上传 `dist/astrbot_plugin_tts_rvc-<版本>.zip`；
- **GitHub Releases**：把 zip 作为 release 附件，用户在面板里也能装；
- **手动复制**：把四个文件放进 `data/plugins/astrbot_plugin_tts_rvc/`。
