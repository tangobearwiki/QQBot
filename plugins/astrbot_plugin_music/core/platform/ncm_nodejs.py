from typing import ClassVar

from astrbot.api import logger

from ..config import PluginConfig
from ..model import Platform, Song
from .base import BaseMusicPlayer


class NetEaseMusicNodeJS(BaseMusicPlayer):
    """
    网易云音乐 NodeJS API
    """

    platform: ClassVar[Platform] = Platform(
        name="netease_nodejs",
        display_name="网易云NodeJS版",
    )
    DEFAULT_BASE_URL = "https://zm.wwoyun.cn"
    base_url: str

    def __init__(self, config: PluginConfig):
        super().__init__(config)
        self.base_url = (self.platform_cfg.base_url or self.DEFAULT_BASE_URL).rstrip(
            "/"
        )
        self.api_headers = dict(self.HEADERS)
        api_token = (self.platform_cfg.api_token or "").strip()
        if api_token:
            self.api_headers["Authorization"] = f"Bearer {api_token}"

    async def fetch_songs(self, keyword: str, limit: int = 5, extra=None) -> list[Song]:
        result = await self._request(
            url=f"{self.base_url}/search",
            method="POST",
            data={"keywords": keyword, "limit": limit, "type": 1, "offset": 0},
            headers=self.api_headers,
        )
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("result"), dict)
            or not isinstance(result["result"].get("songs"), list)
        ):
            logger.error(f"返回了意料之外数据：{result}")
            return []

        songs = result.get("result", {}).get("songs", [])[:limit]
        parsed_songs = []
        for item in songs:
            if not isinstance(item, dict):
                continue
            artists = item.get("artists") or item.get("ar") or []
            album = item.get("album") or item.get("al") or {}
            parsed_songs.append(
                Song(
                    id=str(item.get("id") or ""),
                    name=item.get("name"),
                    artists="、".join(
                        artist["name"]
                        for artist in artists
                        if isinstance(artist, dict) and artist.get("name")
                    ),
                    duration=item.get("duration") or item.get("dt"),
                    cover_url=album.get("picUrl") if isinstance(album, dict) else None,
                    page_url=(
                        f"https://music.163.com/song?id={item['id']}"
                        if item.get("id")
                        else None
                    ),
                )
            )
        return parsed_songs

    async def fetch_comments(self, song: Song) -> Song:
        if song.comments:
            return song
        result = await self._request(
            url=f"{self.base_url}/comment/hot",
            method="POST",
            data={"id": song.id, "type": 0},
            headers=self.api_headers,
        )
        if not isinstance(result, dict) or "hotComments" not in result:
            logger.error(f"返回了意料之外数据：{result}")
            return song
        if comments := result.get("hotComments"):
            song.comments = comments
        return song

    async def fetch_lyrics(self, song: Song) -> Song:
        if song.lyrics:
            return song
        result = await self._request(
            f"{self.base_url}/lyric?id={song.id}", headers=self.api_headers
        )
        if not isinstance(result, dict) or "lrc" not in result:
            logger.error(f"返回了意料之外数据：{result}")
            return song
        lyric = result["lrc"].get("lyric")
        if lyric:
            song.lyrics = lyric
        return song

    async def fetch_extra(self, song: Song) -> Song:
        try:
            result = await self._request(
                url=f"{self.base_url}/song/url?id={song.id}",
                method="GET",
                headers=self.api_headers,
            )
            if not isinstance(result, dict):
                logger.error(f"返回了意料之外数据：{result}")
                return song
        except Exception as e:
            logger.warning(f"{self.__class__.__name__} fetch_extra 失败: {e}")
            return song

        # NodeJS API 返回结构示例:
        # { "data": [ { "url": "...", ... } ] }
        data = result.get("data")
        if not isinstance(data, list) or not data:
            return song

        info = data[0]
        if not isinstance(info, dict):
            return song
        if info.get("id") is not None and str(info["id"]) != str(song.id):
            logger.warning("NodeJS API returned audio for an unexpected song")
            return song

        audio_url = info.get("url")
        trial = info.get("freeTrialInfo")
        short_audio = (
            isinstance(info.get("time"), (int, float))
            and song.duration is not None
            and song.duration > 60000
            and 0 < info["time"] < min(song.duration * 0.75, 65000)
        )
        if info.get("preview") or (trial and trial != "null") or short_audio:
            song.note = "当前音源仅提供试听片段，并非完整版。"
        elif info.get("unblocked"):
            source = str(info.get("source") or "未知")[:60]
            song.note = f"使用第三方替代音源（{source}），完整性及版本未核验。"
        if audio_url and song.audio_url is None:
            song.audio_url = audio_url

        return song
