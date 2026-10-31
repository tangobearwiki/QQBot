from dataclasses import dataclass


@dataclass(slots=True)
class Song:
    id: str
    """歌曲ID"""

    source: str | None = None
    """歌曲来源平台标识"""

    name: str | None = None
    """歌曲原始名称"""

    artists: str | None = None
    """歌手/艺人"""

    duration: int | None = None
    """时长（毫秒）"""

    cover_url: str | None = None
    """封面图 URL"""

    page_url: str | None = None
    """音源平台的歌曲页面 URL，用于卡片跳转"""

    audio_url: str | None = None
    """音频播放 URL"""

    path: str | None = None
    """音频文件路径(预留给持久化)"""

    lyrics: str | None = None
    """歌词"""

    comments: list | None = None
    """评论列表"""

    note: str | None = None
    """备注，例如来源或额外信息"""

    def to_lines(self) -> str:
        """将 Song 信息整理成多行文本"""
        lines = [
            f"ID: {self.id}",
            f"名称: {self.name or '未知'}",
            f"艺人: {self.artists or '未知'}",
        ]
        if self.duration:
            mins, secs = divmod(self.duration // 1000, 60)
            lines.append(f"时长: {mins}:{secs:02d}")
        if self.audio_url:
            lines.append(f"播放链接: {self.audio_url}")
        if self.cover_url:
            lines.append(f"封面: {self.cover_url}")
        if self.page_url:
            lines.append(f"歌曲页面: {self.page_url}")
        if self.note:
            lines.append(f"备注: {self.note}")
        return "\n".join(lines)


@dataclass(slots=True)
class Platform:
    """平台信息"""

    name: str
    """ 平台名称 """
    display_name: str
    """ 平台显示名称 """
