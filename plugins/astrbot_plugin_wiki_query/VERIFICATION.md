# 验证记录

验证日期：2026-11-02（Asia/Shanghai），在当前 Windows 工作区执行。

## MyBot 原项目

没有启动 Bot、连接 OneBot 或登录 QQ。使用 Gradle 编译，并在 `MyBot/build/wiki-check/` 中创建临时 Java 入口，直接调用现有 Kotlin `search()`；没有修改项目源码、配置或构建文件。

| 原项目方法 | 查询词 | 结果 |
| --- | --- | --- |
| StardewValleySearch.search | 杨桃 | 成功，10 条 |
| StardewValleySVESearch.search | 索菲娅 | 成功，10 条 |
| TrSearch.search | 天顶剑 | 成功，10 条 |
| TrCalamitySearch.search | 星流巨械 | 成功，10 条 |
| StardewValleySearch.search（再次调用） | 石头 | 成功，10 条 |

`compileKotlin` 和独立 `wikiSmoke` 任务均通过。这里验证的是查询方法，不包含 QQ 消息发送和远端服务器的网络环境。MyBot 的所有本轮修复已撤回，用户原有的工作区改动保留。

当前灰机实现随机生成的 `cf_clearance` / `__cf_bm` 不能视为有效 Cloudflare 凭据。本机调用该实现仍然成功，因此不能仅凭普通 Python 的 403 就判断原 Bot 必然失败；也没有复现远端调用报错。HTTP 错误被作为普通字符串传给 JSON 解析、查询参数直接拼接等问题仅作记录，按要求未修复。

## 新模块联网验证

- B 站星露谷：普通请求和 Chrome 模拟请求均成功，返回真实搜索词条。
- SVE：普通请求和 Chrome 模拟请求均成功，返回真实搜索词条。
- 默认双源入口：合并原版和 SVE 的结果，列表项带有来源。
- 灰机灾厄：标准库普通请求返回 Cloudflare HTTP 403；`curl_cffi` Chrome 模拟成功，返回“星流巨械”等词条。灰机仅用于独立测试，没有注册到插件默认源。

原始结果保存在 `tests/live_bilibili.json`、`tests/live_huiji.json`。脚本只把有效的搜索 JSON 当作成功，验证页、API 错误和无词条均不能冒充连通测试通过。结果代表本次请求，不保证不同服务器 IP 或未来 Cloudflare 策略下始终通过。

## 本地检查

离线测试覆盖请求编码、重复调用、精确匹配、HTML 摘要清理、空列表、错误 JSON、HTTP 429、HTTP 403/200 验证页、模式切换、默认源范围及双源合并错误处理。

另执行 Ruff 检查、格式检查和 Python 语法编译。AstrBot 入口依据官方 API 检查；由于本目录没有 AstrBot 本体，未启动 AstrBot 做插件加载或 QQ 发消息验证。
