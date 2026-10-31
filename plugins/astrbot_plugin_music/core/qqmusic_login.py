from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
import time
import urllib.parse
from collections.abc import Iterator

import aiohttp
from yarl import URL

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain

from .config import PluginConfig


class QQMusicLogin:
    """Manage the single active QQ Music QR login flow."""

    _USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    _QR_URL = "https://ssl.ptlogin2.qq.com/ptqrshow"
    _POLL_URL = "https://ssl.ptlogin2.qq.com/ptqrlogin"
    _OAUTH_URL = "https://graph.qq.com/oauth2.0/authorize"
    _MUSIC_LOGIN_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"

    def __init__(self, config: PluginConfig):
        self.config = config
        self._task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None
        self._start_lock = asyncio.Lock()

    @staticmethod
    def _hash33(value: str, seed: int = 0) -> int:
        for char in value:
            seed += (seed << 5) + ord(char)
        return seed & 0x7FFFFFFF

    @staticmethod
    def _cookie_values(session: aiohttp.ClientSession, url: str) -> dict[str, str]:
        return {
            key: morsel.value
            for key, morsel in session.cookie_jar.filter_cookies(URL(url)).items()
        }

    def _cookie_header(self, session: aiohttp.ClientSession, *urls: str) -> str:
        values = {}
        for url in urls:
            values.update(self._cookie_values(session, url))
        return "; ".join(f"{key}={value}" for key, value in values.items())

    @staticmethod
    def _nested_values(value) -> Iterator[tuple[str, object]]:
        if isinstance(value, dict):
            for key, item in value.items():
                yield key, item
                yield from QQMusicLogin._nested_values(item)
        elif isinstance(value, list):
            for item in value:
                yield from QQMusicLogin._nested_values(item)

    async def _exchange_login_code(
        self, session: aiohttp.ClientSession, callback_url: str, qrsig: str
    ) -> tuple[dict[str, str], str]:
        async with session.get(
            callback_url,
            headers={
                "User-Agent": self._USER_AGENT,
                "Cookie": self._cookie_header(
                    session, self._QR_URL, self._POLL_URL, callback_url
                )
                or f"qrsig={qrsig}",
            },
            allow_redirects=False,
        ) as response:
            await response.read()
        cookies = self._cookie_values(session, callback_url)
        p_skey = cookies.get("p_skey")
        if not p_skey:
            raise RuntimeError("QQ 登录回调没有返回 p_skey")

        form = {
            "response_type": "code",
            "client_id": "100497308",
            "redirect_uri": (
                "https://y.qq.com/portal/wx_redirect.html?login_type=1"
                "&surl=https://y.qq.com/"
            ),
            "scope": "get_user_info,get_app_friends",
            "state": "state",
            "switch": "",
            "from_ptlogin": "1",
            "src": "1",
            "update_auth": "1",
            "openapi": "1010_1030",
            "g_tk": str(self._hash33(p_skey, 5381)),
            "auth_time": str(int(time.time() * 1000)),
            "ui": hashlib.sha256(qrsig.encode()).hexdigest()[:32],
        }
        async with session.post(
            self._OAUTH_URL,
            data=form,
            headers={
                "User-Agent": self._USER_AGENT,
                "Cookie": self._cookie_header(
                    session, self._QR_URL, self._POLL_URL, callback_url, self._OAUTH_URL
                ),
            },
            allow_redirects=False,
        ) as response:
            location = response.headers.get("Location", "")
        code = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query).get(
            "code", [""]
        )[0]
        if not code:
            raise RuntimeError("QQ OAuth 没有返回授权码")

        payload = {
            "comm": {
                "ct": 24,
                "cv": 4747474,
                "platform": "yqq.json",
                "chid": "0",
                "uin": 0,
                "g_tk": 5381,
                "g_tk_new_20200303": 5381,
                "format": "json",
                "inCharset": "utf-8",
                "outCharset": "utf-8",
                "notice": 0,
                "need_new_code": 1,
            },
            "req_0": {
                "module": "QQConnectLogin.LoginServer",
                "method": "QQLogin",
                "param": {"code": code},
            },
        }
        async with session.post(
            self._MUSIC_LOGIN_URL,
            json=payload,
            headers={
                "User-Agent": self._USER_AGENT,
                "Referer": "https://y.qq.com/",
                "Origin": "https://y.qq.com",
                "Cookie": self._cookie_header(
                    session,
                    self._QR_URL,
                    self._POLL_URL,
                    callback_url,
                    self._OAUTH_URL,
                    "https://y.qq.com/",
                    self._MUSIC_LOGIN_URL,
                ),
            },
        ) as response:
            if response.status >= 400:
                raise RuntimeError(f"QQ 音乐登录接口返回 HTTP {response.status}")
            body = await response.json(content_type=None)

        req0 = body.get("req_0") or {}
        if req0.get("code") not in (None, 0):
            raise RuntimeError(f"QQ 音乐登录接口返回错误码 {req0['code']}")

        result_cookies = {}
        for url in (
            callback_url,
            self._OAUTH_URL,
            "https://y.qq.com/",
            self._MUSIC_LOGIN_URL,
        ):
            result_cookies.update(self._cookie_values(session, url))
        login_data = req0.get("data") or {}
        for key, value in self._nested_values(login_data):
            if value is None or isinstance(value, (dict, list)):
                continue
            if key in {"musickey", "qm_keyst", "qqmusic_key"} and str(value):
                result_cookies["qm_keyst"] = str(value)
                result_cookies["qqmusic_key"] = str(value)
            elif key in {"musicid", "uin", "qqmusic_uin"} and str(value):
                result_cookies["uin"] = str(value)
                result_cookies["qqmusic_uin"] = str(value)
            elif key in {"refresh_token", "refresh_key", "openid", "access_token"}:
                result_cookies[key] = str(value)

        result_cookies["guid"] = hashlib.sha256(qrsig.encode()).hexdigest()[:32]
        return result_cookies, str(req0.get("code", ""))

    async def start(self, event: AstrMessageEvent) -> None:
        """Request a QR code and start polling for login confirmation.

        Args:
            event: The command event.
        """
        async with self._start_lock:
            if self._task and not self._task.done():
                await event.send(
                    event.plain_result("已有 QQ 音乐登录会话正在等待扫码")
                )
                event.stop_event()
                return

            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                headers={"User-Agent": self._USER_AGENT},
            )
            self._session = session
            try:
                query = urllib.parse.urlencode(
                    {
                        "appid": 716027609,
                        "e": 2,
                        "l": "M",
                        "s": 3,
                        "d": 72,
                        "v": 4,
                        "t": time.time(),
                        "daid": 383,
                        "pt_3rd_aid": 100497308,
                        "u1": "https://graph.qq.com/oauth2.0/login_jump",
                    }
                )
                qr_url = f"{self._QR_URL}?{query}"
                async with session.get(
                    qr_url, headers={"Referer": "https://xui.ptlogin2.qq.com/"}
                ) as response:
                    if response.status >= 400:
                        raise RuntimeError(f"QQ 二维码接口返回 HTTP {response.status}")
                    qr_bytes = await response.read()
                qrsig = self._cookie_values(session, qr_url).get("qrsig", "")
                if not qrsig or not qr_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise RuntimeError("QQ 二维码接口未返回有效图片或 qrsig")
                chain = [Plain("用QQ扫码登录:"), Image.fromBytes(qr_bytes)]
                await event.send(MessageChain(chain))
                self._task = asyncio.create_task(
                    self._poll_login(event, session, qrsig, self._hash33(qrsig))
                )
            except Exception as exc:
                await session.close()
                self._session = None
                logger.warning(f"[music] Could not start QQ Music QR login: {exc}")
                await event.send(event.plain_result(f"无法开始 QQ 音乐登录：{exc}"))
            finally:
                event.stop_event()

    async def _poll_login(
        self,
        event: AstrMessageEvent,
        session: aiohttp.ClientSession,
        qrsig: str,
        ptqrtoken: int,
    ) -> None:
        current_task = asyncio.current_task()
        expires_at = time.monotonic() + 180
        last_status = ""
        try:
            while time.monotonic() < expires_at:
                query = urllib.parse.urlencode(
                    {
                        "u1": "https://graph.qq.com/oauth2.0/login_jump",
                        "ptqrtoken": ptqrtoken,
                        "ptredirect": 0,
                        "h": 1,
                        "t": 1,
                        "g": 1,
                        "from_ui": 1,
                        "ptlang": 2052,
                        "action": f"0-0-{int(time.time() * 1000)}",
                        "js_ver": 20102616,
                        "js_type": 1,
                        "pt_uistyle": 40,
                        "aid": 716027609,
                        "daid": 383,
                        "pt_3rd_aid": 100497308,
                        "has_onekey": 1,
                    }
                )
                async with session.get(
                    f"{self._POLL_URL}?{query}",
                    headers={
                        "User-Agent": self._USER_AGENT,
                        "Referer": "https://xui.ptlogin2.qq.com/",
                        "Cookie": f"qrsig={qrsig}",
                    },
                ) as response:
                    poll_text = await response.text()
                values = re.findall(r"'([^']*)'", poll_text)
                status = values[0] if values else "unknown"
                if status != last_status:
                    status_messages = {
                        "0": "QQ 已扫码，正在交换音乐登录凭据",
                        "65": "QQ 登录二维码已过期",
                        "66": "等待 QQ 确认",
                        "67": "等待 QQ 扫描",
                    }
                    if status in status_messages:
                        logger.info(status_messages[status])
                    last_status = status
                if status == "65":
                    return
                if status != "0":
                    await asyncio.sleep(2)
                    continue

                callback_url = next(
                    (value for value in values if value.startswith("http")), ""
                )
                if not callback_url:
                    raise RuntimeError("QQ 登录状态没有返回回调地址")
                cookies, response_code = await self._exchange_login_code(
                    session, callback_url, qrsig
                )
                key = cookies.get("qm_keyst") or cookies.get("qqmusic_key")
                uin = re.sub(
                    r"\D", "", cookies.get("uin") or cookies.get("qqmusic_uin") or ""
                )
                if not key or not uin:
                    raise RuntimeError(
                        f"登录流程结束但未取得有效 Cookie/UIN（CGI code={response_code or 'n/a'}）"
                    )

                cookies.pop("qrsig", None)
                cookie_string = "; ".join(
                    f"{name}={value}"
                    for name, value in cookies.items()
                    if value and not re.search(r"[;\r\n]", f"{name}{value}")
                )
                qqmusic_config = self.config.players.qqmusic
                qqmusic_config.cookies = cookie_string
                qqmusic_config.uin = uin
                self.config.save_config()
                await event.send(
                    event.plain_result(
                        f"QQ 音乐登录成功，Cookie 已写入音源配置。UIN：{uin}"
                    )
                )
                return
            await event.send(event.plain_result("QQ 登录二维码已超时，请重新登录"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"[music] QQ Music QR login failed: {exc}")
            with contextlib.suppress(Exception):
                await event.send(event.plain_result(f"QQ 音乐登录失败：{exc}"))
        finally:
            await session.close()
            if self._task is current_task:
                self._task = None
                self._session = None

    async def close(self) -> None:
        """Cancel the active login task and release its HTTP session."""
        task = self._task
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        elif self._session and not self._session.closed:
            await self._session.close()
        self._task = None
        self._session = None
