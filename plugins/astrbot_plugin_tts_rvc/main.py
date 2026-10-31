"""AstrBot 插件：把机器人回复转成你自己的 RVC 音色语音。

链路：
    大模型回复 -> 本插件拦截 -> tts-rvc-service（edge-tts + RVC）-> Record 语音段

插件不依赖 torch / GPU，所有重活都在本机的声音服务里。服务不可达、超时、
返回异常时一律保留原文字回复，不影响正常聊天。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import time
from typing import Any

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain, Record
from astrbot.api.platform import MessageType
from astrbot.api.star import Context, Star, StarTools, register

PLUGIN_NAME = "astrbot_plugin_tts_rvc"
DEFAULT_SERVICE = "http://127.0.0.1:9880/tts"
CACHE_KEEP = 200
CACHE_MAX_AGE = 24 * 60 * 60
FAIL_BACKOFF = 30.0

HELP_TEXT = """语音回复指令：
/打开语音 —— 当前会话开始语音回复
/关闭语音 —— 当前会话恢复文字
/语音状态 —— 查看开关、音色和服务状态
/voice_list —— 列出服务里可用的 RVC 音色
/voice_set <id> —— 为当前会话切换音色
/voice_reset —— 恢复默认音色
/voice_test [文字] —— 合成一条测试语音"""


@register(
    PLUGIN_NAME,
    "cyrene",
    "用你自己的 RVC 模型，把机器人回复变成语音",
    "1.1.0",
    "",
)
class TTSRVCPlugin(Star):
    """轻量插件：拦截回复、调用本机声音服务、失败回退文字。"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config: dict[str, Any] = dict(config or {})

        # 串行合成，避免同一时间抢显存
        self._lock = asyncio.Lock()
        self._cache_dir = os.path.join(tempfile.gettempdir(), PLUGIN_NAME)
        os.makedirs(self._cache_dir, exist_ok=True)

        self._data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self._state_file = os.path.join(self._data_dir, "sessions.json")
        self._sessions: dict[str, dict] = self._load_state()

        # 服务熔断：失败后短时间直接跳过，避免每条消息都等超时
        self._down_until = 0.0
        self._down_reason = ""

    # ------------------------------------------------------------------ 配置

    def _cfg(self, key: str, default: Any = None) -> Any:
        value = self.config.get(key, default)
        return default if value is None else value

    def _service_base(self) -> str:
        """把配置的服务地址归一化成根地址（去掉末���的 /tts）。"""
        url = str(self._cfg("service_url", DEFAULT_SERVICE)).strip()
        if not url:
            url = DEFAULT_SERVICE
        if url.endswith("/"):
            url = url[:-1]
        if url.endswith("/tts"):
            url = url[: -len("/tts")]
        return url.rstrip("/")

    # -------------------------------------------------------------- 会话状态

    def _load_state(self) -> dict[str, dict]:
        """读取会话状态，兼容早期「只存 bool」的格式。"""
        try:
            with open(self._state_file, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        state: dict[str, dict] = {}
        for key, value in raw.items():
            if isinstance(value, bool):
                state[str(key)] = {"enabled": value}
            elif isinstance(value, dict):
                state[str(key)] = value
        return state

    def _save_state(self) -> None:
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            tmp = self._state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._sessions, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._state_file)
        except OSError:
            logger.exception("[tts_rvc] 保存会话状态失败")

    def _session(self, event: AstrMessageEvent) -> dict:
        return self._sessions.setdefault(event.unified_msg_origin, {})

    def _session_voice(self, event: AstrMessageEvent) -> str | None:
        """当前会话的音色：会话设置 > 插件默认 > None（服务端决定）。"""
        entry = self._sessions.get(event.unified_msg_origin) or {}
        return entry.get("voice") or self._cfg("voice_id") or None

    def _voice_enabled(self, event: AstrMessageEvent) -> bool:
        """当前会话是否发语音：显式开关优先，否则按私聊/群聊默认。"""
        entry = self._sessions.get(event.unified_msg_origin)
        if entry is not None and "enabled" in entry:
            return bool(entry["enabled"])
        if event.get_message_type() == MessageType.FRIEND_MESSAGE:
            return bool(self._cfg("private_only", True))
        return bool(self._cfg("group_default", False))

    # -------------------------------------------------------------- 服务调用

    def _mark_down(self, reason: Any) -> None:
        self._down_until = time.monotonic() + FAIL_BACKOFF
        self._down_reason = str(reason)
        logger.warning(
            "[tts_rvc] 声音服务暂时不可用（%.0f 秒内跳过语音）：%s",
            FAIL_BACKOFF,
            reason,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        timeout: float | None = None,
    ) -> tuple[int, bytes]:
        base = self._service_base()
        if not base.startswith(("http://", "https://")):
            raise RuntimeError(f"service_url 不是合法地址：{base}")
        total = float(timeout if timeout is not None else self._cfg("timeout", 90.0))
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=total)) as session:
            async with session.request(method, base + path, json=json_body) as resp:
                data = await resp.read()
                return resp.status, data

    async def _fetch_voices(self, timeout: float = 3.0) -> list[dict] | None:
        """获取音色列表；服务不可用返回 None，方便调用方区分「空列表」。"""
        try:
            status, data = await self._request("GET", "/voices", timeout=timeout)
            if status != 200:
                return None
            voices = json.loads(data)
            return [v for v in voices if isinstance(v, dict)] if isinstance(voices, list) else None
        except Exception:
            return None

    def _cache_path(self, text: str, voice_id: str | None) -> str:
        digest = hashlib.sha256(f"{voice_id or '-'}\x00{text}".encode("utf-8")).hexdigest()[:24]
        return os.path.join(self._cache_dir, f"{digest}.wav")

    @staticmethod
    def _cache_ok(path: str) -> bool:
        try:
            return (
                os.path.isfile(path)
                and os.path.getsize(path) > 1000
                and time.time() - os.path.getmtime(path) < CACHE_MAX_AGE
            )
        except OSError:
            return False

    def _cleanup_cache(self) -> None:
        try:
            files = [
                os.path.join(self._cache_dir, name)
                for name in os.listdir(self._cache_dir)
                if name.endswith(".wav")
            ]
            files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            now = time.time()
            for old in files[CACHE_KEEP:]:
                os.remove(old)
            for old in files[:CACHE_KEEP]:
                if now - os.path.getmtime(old) > CACHE_MAX_AGE:
                    os.remove(old)
        except OSError:
            pass

    async def _synthesize(self, text: str, voice_id: str | None) -> str:
        """请求声音服务并落盘为 wav，返回本地绝对路径。"""
        if time.monotonic() < self._down_until:
            raise RuntimeError(self._down_reason or "声音服务暂时不可用")

        path = self._cache_path(text, voice_id)
        if self._cfg("cache", True) and self._cache_ok(path):
            return path

        payload: dict[str, Any] = {"text": text}
        if voice_id:
            payload["voice_id"] = voice_id

        async with self._lock:
            try:
                status, data = await self._request("POST", "/tts", json_body=payload)
            except Exception as exc:
                self._mark_down(exc)
                raise
            if status != 200:
                message = data[:300].decode("utf-8", "replace")
                error = RuntimeError(f"HTTP {status}：{message}")
                if status >= 500:
                    self._mark_down(error)
                raise error
            if len(data) < 1000:
                raise RuntimeError("服务返回的音频为空")

            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)

        self._down_until = 0.0
        self._cleanup_cache()
        return path

    async def _service_health(self) -> dict | None:
        try:
            status, data = await self._request("GET", "/health", timeout=3.0)
            if status != 200:
                return None
            payload = json.loads(data)
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    # ---------------------------------------------------------- 发送前钩子

    @filter.on_decorating_result()
    async def convert_reply_to_voice(self, event: AstrMessageEvent):
        """把纯文本回复替换成语音；任何失败都保留文字，不影响聊天。"""
        if not self._cfg("enabled", True):
            return

        result = event.get_result()
        if result is None or not getattr(result, "chain", None):
            return
        if self._cfg("only_llm", True) and not (
            result.is_llm_result() or result.is_model_result()
        ):
            return
        if not self._voice_enabled(event):
            return

        text = "".join(
            comp.text for comp in result.chain if isinstance(comp, Plain) and comp.text
        ).strip()
        if not text:
            return
        if len(text) < int(self._cfg("min_chars", 2)):
            return
        if len(text) > int(self._cfg("max_chars", 150)):
            logger.info("[tts_rvc] 文本 %d 字超过上限，保持文字", len(text))
            return

        try:
            path = await self._synthesize(text, self._session_voice(event))
        except Exception as exc:
            logger.warning("[tts_rvc] 语音合成失败，保留文字：%s", exc)
            return

        record = Record(file=path, url=path)
        others = [comp for comp in result.chain if not isinstance(comp, Plain)]
        if self._cfg("dual_output", False):
            result.chain = [Plain(text), record, *others]
        else:
            result.chain = [record, *others]
        logger.info("[tts_rvc] 语音已生成：%d 字 -> %s", len(text), path)

    # ---------------------------------------------------------------- 指令

    def _allowed(self, event: AstrMessageEvent) -> bool:
        return not self._cfg("admin_only", False) or event.is_admin()

    @staticmethod
    def _arg(event: AstrMessageEvent) -> str:
        text = (event.message_str or "").strip()
        parts = text.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    async def _deny(self, event: AstrMessageEvent):
        yield event.plain_result("只有管理员可以切换语音。")

    @filter.command("打开语音")
    async def open_voice(self, event: AstrMessageEvent):
        """在当前会话开启语音回复。"""
        if not self._allowed(event):
            async for result in self._deny(event):
                yield result
            return
        self._session(event)["enabled"] = True
        self._save_state()
        yield event.plain_result("语音已打开，从现在开始这个会话会用语音回复。")

    @filter.command("关闭语音")
    async def close_voice(self, event: AstrMessageEvent):
        """在当前会话关闭语音回复。"""
        if not self._allowed(event):
            async for result in self._deny(event):
                yield result
            return
        self._session(event)["enabled"] = False
        self._save_state()
        yield event.plain_result("语音已关闭，这个会话恢复文字回复。")

    @filter.command("语音状态")
    async def voice_status(self, event: AstrMessageEvent):
        """查看当前会话的语音开关、音色与服务状态。"""
        enabled = self._voice_enabled(event)
        entry = self._sessions.get(event.unified_msg_origin) or {}
        source = "单独设置" if "enabled" in entry else "默认"
        voice = self._session_voice(event) or "服务端默认"

        health = await self._service_health()
        if health is None:
            service = "连不上（检查语音服务是否启动）"
        elif health.get("ok"):
            count = len(health.get("voices") or [])
            service = f"正常，{count} 个音色可用" if count else "正常，但还没有导入模型"
        else:
            service = f"服务在跑，但 RVC 异常：{health.get('error', '未知错误')}"

        yield event.plain_result(
            f"当前会话语音：{'开' if enabled else '关'}（{source}）\n"
            f"音色：{voice}\n"
            f"服务：{service}\n"
            "发送 /voice_help 查看全部指令。"
        )

    @filter.command("voice_list")
    async def voice_list(self, event: AstrMessageEvent):
        """列出声音服务里可用的 RVC 音色。"""
        voices = await self._fetch_voices(timeout=5.0)
        if voices is None:
            yield event.plain_result(
                "获取音色列表失败：连接不上声音服务。\n"
                "先确认语音服务已启动（浏览器打开 http://127.0.0.1:9880/health）。"
            )
            return
        if not voices:
            yield event.plain_result(
                "声音服务里还没有导入任何 RVC 模型。\n"
                "打开 http://127.0.0.1:7868 进入 rvc-next 面板 → 模型库 → 导入 .pth 和 .index。"
            )
            return

        current = self._session_voice(event)
        lines = [f"可用音色（{len(voices)}）："]
        for voice in voices:
            vid = str(voice.get("id") or "?")
            mark = " ← 当前" if vid == current else ""
            rate = voice.get("sample_rate") or "?"
            version = voice.get("version") or "?"
            info = voice.get("info") or ""
            detail = f"{version} / {rate}Hz" + (f" / {info}" if info else "")
            lines.append(f"- {vid}（{detail}）{mark}")
        lines.append("用 /voice_set <id> 为当前会话切换，/voice_reset 恢复默认。")
        yield event.plain_result("\n".join(lines))

    @filter.command("voice_set")
    async def voice_set(self, event: AstrMessageEvent):
        """为当前会话指定 RVC 音色：/voice_set xilian"""
        if not self._allowed(event):
            async for result in self._deny(event):
                yield result
            return
        voice_id = self._arg(event)
        if not voice_id:
            yield event.plain_result("用法：/voice_set <音色 id>，先用 /voice_list 查看。")
            return

        voices = await self._fetch_voices(timeout=5.0)
        if voices is not None:
            known = {str(v.get("id")) for v in voices}
            if voices and voice_id not in known:
                yield event.plain_result(
                    f"没有叫「{voice_id}」的音色。\n"
                    "当前可用：" + "、".join(sorted(known)) + "\n用 /voice_list 查看详情。"
                )
                return
            if not voices:
                yield event.plain_result(
                    "声音服务里还没有导入任何模型，先把 .pth 和 .index 导入 rvc-next 再试。"
                )
                return
        else:
            logger.warning("[tts_rvc] 服务不可达，仍然保存音色 %s", voice_id)

        self._session(event)["voice"] = voice_id
        self._session(event)["enabled"] = True
        self._save_state()
        yield event.plain_result(f"当前会话音色已切换为：{voice_id}（语音也已打开）")

    @filter.command("voice_reset")
    async def voice_reset(self, event: AstrMessageEvent):
        """恢复当前会话的默认音色。"""
        self._session(event).pop("voice", None)
        self._save_state()
        yield event.plain_result("已恢复默认音色。")

    @filter.command("voice_test")
    async def voice_test(self, event: AstrMessageEvent):
        """手动测试：/voice_test 你好呀"""
        text = self._arg(event) or "你好呀，这是一条语音测试。"
        if len(text) > 200:
            yield event.plain_result("测试文本太长了，请控制在 200 字以内。")
            return
        try:
            path = await self._synthesize(text, self._session_voice(event))
        except Exception as exc:
            logger.exception("[tts_rvc] 测试合成失败")
            yield event.plain_result(
                f"语音合成失败：{exc}\n"
                "检查清单：语音服务是否启动（http://127.0.0.1:9880/health）、"
                "是否已导入 RVC 模型、显存是否够用。"
            )
            return
        yield event.chain_result([Record(file=path, url=path)])

    @filter.command("voice_help")
    async def voice_help(self, event: AstrMessageEvent):
        """查看语音相关指令。"""
        yield event.plain_result(HELP_TEXT)
