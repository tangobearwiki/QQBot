"""AstrBot 的薄入口；纯查询功能请直接导入 wiki_client.query_wikis。"""

import json

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .wiki_client import WikiQueryError, query_wikis, search_wiki


@register("astrbot_plugin_wiki_query", "kaer-nk", "星露谷/SVE Wiki 查询", "1.0.0")
class WikiQueryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    async def query(
        self, word: str, bypass_cloudflare: bool = False, *, source: str = "all"
    ) -> list:
        """供其它插件调用，直接返回 Python list，不构造消息。"""
        if source == "all":
            return await query_wikis(
                word,
                bypass_cloudflare,
                limit=int(self.config.get("result_limit", 5)),
                timeout=float(self.config.get("timeout", 25)),
            )
        return await search_wiki(
            word,
            bypass_cloudflare,
            source=source,
            limit=int(self.config.get("result_limit", 5)),
            timeout=float(self.config.get("timeout", 25)),
        )

    @filter.command("wiki查")
    async def wiki_query(self, event: AstrMessageEvent):
        """/wiki查 [all|stardew|sve] [--cf|--no-cf] 关键词"""
        # 只消费前面的源/开关，关键词可包含空格，不与原有 ss 指令冲突。
        tokens = event.message_str.strip().split()
        args = tokens[1:]
        source = str(self.config.get("default_source", "all"))
        bypass = bool(self.config.get("bypass_cloudflare", False))
        if args and args[0].lower() in ("all", "stardew", "sve"):
            source = args.pop(0).lower()
        if args and args[0] in ("--cf", "--no-cf"):
            bypass = args.pop(0) == "--cf"
        word = " ".join(args)
        if not word:
            yield event.plain_result(
                "用法：/wiki查 [all|stardew|sve] [--cf|--no-cf] 关键词"
            )
            return
        try:
            results = await self.query(word, bypass, source=source)
        except (WikiQueryError, ValueError, TypeError) as exc:
            yield event.plain_result(f"Wiki 查询失败：{exc}")
            return
        # 可直接审查的 JSON list；不包含 QQ 合并转发、头像或艾特逻辑。
        yield event.plain_result(json.dumps(results, ensure_ascii=False, indent=2))
