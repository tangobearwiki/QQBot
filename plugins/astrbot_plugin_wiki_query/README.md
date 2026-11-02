# 星露谷 Wiki 查询

只预置 **B 站星露谷原版** 和 **星露谷 SVE** 两个源。核心函数不依赖 AstrBot，输入关键词和绕过开关，输出 Python `list`；没有 QQ 合并转发、头像或艾特逻辑。

## 独立使用

Python 3.10+；普通请求仅用标准库，Chrome 模拟模式需要依赖：

```powershell
python -m pip install -r requirements.txt
```

在插件目录内：

```python
import asyncio
from wiki_client import query_wikis, search_wiki


async def main():
    # 默认同时查询两个预置源，输入仅需“词 + 绕过开关”。
    results = await query_wikis("杨桃", False)
    vanilla = await search_wiki("杨桃", False)
    sve = await search_wiki("索菲娅", False, source="sve")
    chrome = await search_wiki("杨桃", True)
    print(results)


asyncio.run(main())
```

从 QQBot 仓库中的其它代码调用：

```python
from plugins.astrbot_plugin_wiki_query.wiki_client import query_wikis

results = await query_wikis("杨桃", False, limit=5)
```

每个列表项的结构：

```json
{
  "source": "星露谷",
  "title": "词条标题",
  "pageid": 123,
  "url": "https://wiki.biligame.com/stardewvalley/index.php?curid=123",
  "snippet": "已移除 HTML 标签的搜索摘要"
}
```

这是搜索结果列表，`snippet` 是摘要。无匹配返回 `[]`，HTTP/网络/API 错误抛出 `WikiQueryError`，Cloudflare 验证页抛出其子类 `CloudflareBlockedError`。精确标题排在最前，其余保留 API 的相关性顺序。

`query_wikis` 同时查询原版/SVE，`limit` 是每源上限；任何一个源请求失败都会抛错。`search_wiki` 用于指定单个源，默认原版。

`bypass_cloudflare=False` 使用普通 HTTPS 请求；`True` 使用 `curl_cffi` 的 Chrome TLS/HTTP 指纹，不需要账号、密码或手工 Cookie。模拟请求不保证通过所有 Cloudflare 挑战；失败会明确报错，不会返回假空列表。

## AstrBot 安装与入口

将本目录复制到 AstrBot 的 `data/plugins/astrbot_plugin_wiki_query/`，安装 `requirements.txt` 后重载插件。当前 `C:\workspace\QQBot` 是插件集合，未包含 AstrBot 本体。

```text
/wiki查 杨桃
/wiki查 sve 索菲娅
/wiki查 stardew --cf 杨桃
/wiki查 sve --no-cf 索菲娅
```

入口默认同时查询两个源，仅将列表序列化为 JSON 文本。其他插件可以调用 `WikiQueryPlugin.query(word, bypass_cloudflare, source=...)` 获取原生列表。可在插件配置中设置默认源（`all/stardew/sve`）、绕过开关、结果数和超时；不会占用现有 `sdv_wiki_query` 的 `ss` 指令。

## 两个独立联网测试脚本

这两个脚本只导入查询模块，不读取 Bot 配置、不登录、不启动 Bot。

```powershell
# 从插件目录执行；请使用安装了 requirements.txt 的 Python。
python scripts/test_bilibili.py
python scripts/test_huiji.py
```

- `test_bilibili.py`：分别用普通/Chrome 模式查询 B 站星露谷和 SVE，必须返回真实词条。
- `test_huiji.py`：比较灰机灾厄 Wiki 的普通/Chrome 请求。普通请求可以被 CF 拦截，Chrome 模式必须返回词条才成功。灰机源只在这个测试脚本中临时构造，没有注入生产模块。

可加 `--output result.json` 保存报告；全部通过退出码为 0，异常退出码为 1。灰机脚本可加 `--word 星流巨械`。

离线回归：

```powershell
python -m unittest discover -s tests -v
```

## 实现参考

查询参数遵循 [MediaWiki Search API](https://www.mediawiki.org/wiki/API:Search)，Chrome 模拟使用 [curl_cffi 官方文档](https://curl-cffi.readthedocs.io/en/latest/impersonate.html)。AstrBot 入口参考 [AstrBot 插件指南](https://docs.astrbot.app/dev/star/plugin-new.html)。查询思路来自 MyBot 的 Wiki 功能；本次未保留对 MyBot 的任何代码修复。
