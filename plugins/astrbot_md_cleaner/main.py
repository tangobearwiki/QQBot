# main.py
"""发送前剥离消息文本中的 Markdown 语法（QQ 不渲染 md）"""
import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register

_TABLE_SEP = re.compile(r"^[ \t]*\|(\s*:?-{2,}:?\s*\|)+\s*$", re.M)
_TABLE_ROW = re.compile(r"^[ \t]*\|(.+)\|[ \t]*$", re.M)
_HEADING = re.compile(r"^#{1,6}[ \t]+", re.M)
_BOLD = re.compile(r"\*\*\s*([^*\n]+?)\s*\*\*")
_FENCE = re.compile(r"```+[a-zA-Z0-9]*\n?")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_MD_LINK = re.compile(r"\[([^\]\n]{1,40})\]\((https?://[^)\s]+)\)")
_ULIST = re.compile(r"^[ \t]*[-*+][ \t]+", re.M)
_OLIST = re.compile(r"^[ \t]*(\d{1,3})[.)][ \t]+", re.M)
_QUOTE = re.compile(r"^[ \t]*>[ \t]?", re.M)
_HR = re.compile(r"^(?:[ \t]*[-_*][ \t]*){3,}$", re.M)
_BLANK = re.compile(r"\n{3,}")


def _strip_md(text: str) -> str:
    text = _TABLE_SEP.sub("", text)
    text = _TABLE_ROW.sub(lambda m: m.group(1).replace("|", "  "), text)
    text = _HEADING.sub("", text)
    text = _BOLD.sub(r"\1", text)
    text = _FENCE.sub("", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _MD_LINK.sub(r"\1：\2", text)
    text = _ULIST.sub("· ", text)
    text = _OLIST.sub(r"\1、", text)
    text = _QUOTE.sub("", text)
    text = _HR.sub("", text)
    text = _BLANK.sub("\n\n", text)
    return text


@register("astrbot_md_cleaner", "assistant", "Markdown清理器", "1.0.0")
class MdCleaner(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        result = event.get_result()
        if not result or not result.chain:
            return
        changed = False
        for comp in result.chain:
            if isinstance(comp, Plain) and comp.text:
                cleaned = _strip_md(comp.text)
                if cleaned != comp.text:
                    comp.text = cleaned
                    changed = True
        if changed:
            logger.debug("[md_cleaner] 已剥离 Markdown 语法")

    async def terminate(self):
        pass
