import re
import time
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

SONG_DIR = Path("/opt/astrbot/data/songs")
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".silk"}
COOLDOWN_SECONDS = 10
MAX_CANDIDATES = 5


def _normalize(text: str) -> str:
    """小写并去掉空格/分隔符，便于模糊匹配歌名。"""
    return re.sub(r"[\s\-_·・~～]", "", (text or "").lower())


@register(
    "astrbot_plugin_songspot",
    "xiaonvpu",
    "点播服务器本地歌曲（RVC 翻唱发布渠道），语音发送",
    "1.0.0",
)
class SongSpotPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._last_spot: dict[str, float] = {}

    def _list_songs(self) -> list[Path]:
        if not SONG_DIR.exists():
            return []
        try:
            return sorted(
                (p for p in SONG_DIR.iterdir()
                 if p.is_file() and p.suffix.lower() in AUDIO_EXTS),
                key=lambda p: p.name,
            )
        except OSError as e:
            logger.error(f"[songspot] 扫描曲库失败: {e}")
            return []

    @filter.command("点播列表")
    async def list_songs(self, event: AstrMessageEvent):
        """列出曲库里全部歌曲。"""
        songs = self._list_songs()
        if not songs:
            yield event.plain_result("🎵 曲库空空如也～把 mp3 放进 data/songs/ 再来吧")
            return
        lines = [f"{i}. {p.stem}" for i, p in enumerate(songs[:30], 1)]
        more = f"\n...（共 {len(songs)} 首）" if len(songs) > 30 else ""
        yield event.plain_result("🎵 当前曲库：\n" + "\n".join(lines) + more)

    @filter.command("点播")
    async def spot(self, event: AstrMessageEvent, keyword: str = ""):
        """点播歌曲：/点播 歌名关键词，命中唯一即以语音发送。"""
        if not keyword:
            yield event.plain_result("用法：/点播 歌名关键词")
            return

        songs = self._list_songs()
        if not songs:
            yield event.plain_result("曲库还是空的，让主人先把歌放进 data/songs/ 吧～")
            return

        gid = str(event.get_group_id() or event.unified_msg_origin)
        now = time.time()
        if now - self._last_spot.get(gid, 0.0) < COOLDOWN_SECONDS:
            yield event.plain_result("点播太频繁啦，休息一下再来～")
            return

        key = _normalize(keyword)
        hits = [p for p in songs if key in _normalize(p.stem)]
        if not hits:
            yield event.plain_result(f"没找到「{keyword}」～ 发 /点播列表 看看有什么吧")
            return
        if len(hits) > 1:
            cand = "\n".join(f"· {p.stem}" for p in hits[:MAX_CANDIDATES])
            yield event.plain_result(
                f"匹配到 {len(hits)} 首，说清楚一点嘛：\n{cand}"
            )
            return

        path = hits[0]
        self._last_spot[gid] = now
        try:
            yield event.chain_result([Comp.Record.fromFileSystem(str(path))])
            logger.info(f"[songspot] 已点播: {path.name}")
        except Exception as e:
            logger.error(f"[songspot] 发送语音失败 {path.name}: {e}")
            yield event.plain_result(f"「{path.stem}」发送失败了：{e}")
