"""TTS-RVC 声音服务：文字 → edge-tts 干声 → RVC 换声 → wav。

设计原则：
- 只监听 127.0.0.1，不对外暴露；
- 串行合成，4GB 显存也能稳定跑；
- 插件只依赖 HTTP，本服务可以是独立进程、独立 venv；
- 出错时返回明确的 HTTP 状态，插件据此决定是否回退文字。

依赖：fastapi / uvicorn / httpx / pydantic
上游：rvc-next（默认 http://127.0.0.1:7868）
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

VERSION = "1.0.0"
SERVICE_DIR = Path(__file__).resolve().parent

logger = logging.getLogger("tts_rvc_service")


# --------------------------------------------------------------------- 配置


def _load_env_file(path: Path) -> None:
    """读取 KEY=VALUE 形式的 .env，不覆盖已存在的系统环境变量。"""
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "off", "no", "")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    rvc_url: str = "http://127.0.0.1:7868"
    voice_id: str = ""
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    pitch: float = 0.0
    index_rate: float = 0.75
    protect: float = 0.33
    f0_method: str = "rmvpe"
    job_timeout: float = 120.0
    emotion_enabled: bool = True
    emotion_scale: float = 1.0
    max_text_chars: int = 500
    warmup: bool = False

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Settings":
        _load_env_file(env_file or SERVICE_DIR / ".env")
        return cls(
            rvc_url=(os.getenv("RVC_URL") or "http://127.0.0.1:7868").rstrip("/"),
            voice_id=(os.getenv("VOICE_ID") or "").strip(),
            tts_voice=os.getenv("TTS_VOICE") or "zh-CN-XiaoxiaoNeural",
            pitch=_env_float("PITCH", 0.0),
            index_rate=_env_float("INDEX_RATE", 0.75),
            protect=_env_float("PROTECT", 0.33),
            f0_method=os.getenv("F0_METHOD") or "rmvpe",
            job_timeout=_env_float("JOB_TIMEOUT", 120.0),
            emotion_enabled=_env_bool("EMOTION_ENABLED", True),
            emotion_scale=_env_float("EMOTION_SCALE", 1.0),
            max_text_chars=_env_int("MAX_TEXT_CHARS", 500),
            warmup=_env_bool("WARMUP", False),
        )


# --------------------------------------------------------------------- 情绪

# 情绪 -> edge-tts 参数（rate 为百分比，pitch 为赫兹）
# 低落（sad）按需求不表达：命中相关词时一律按 neutral 处理。
EMOTIONS: dict[str, dict[str, int]] = {
    "happy": {"rate": 8, "pitch": 2},
    "excited": {"rate": 12, "pitch": 3},
    "gentle": {"rate": -6, "pitch": -1},
    "neutral": {"rate": 0, "pitch": 0},
}

KEYWORDS: dict[str, tuple[str, ...]] = {
    "excited": ("真的吗", "竟然", "居然", "哇", "天哪", "太厉害", "好厉害", "惊喜", "太棒了"),
    "happy": (
        "开心", "高兴", "快乐", "喜欢", "好耶", "太棒", "哈哈", "嘿嘿", "嘻嘻",
        "真好", "可爱", "期待", "幸福", "甜", "嘿嘿嘿",
    ),
    "gentle": (
        "别怕", "没事", "慢慢", "休息", "抱抱", "陪你", "安心", "晚安",
        "辛苦", "温柔", "轻轻", "放心", "乖", "别哭",
    ),
}


def detect_emotion(text: str) -> str:
    """按关键词命中次数判断情绪；平票或没命中就 neutral。"""
    scores = {name: sum(text.count(kw) for kw in words) for name, words in KEYWORDS.items()}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "neutral"
    if sum(1 for value in scores.values() if value == scores[best]) > 1:
        return "neutral"
    return best


# --------------------------------------------------------------------- 数据


class TtsIn(BaseModel):
    text: str = Field(default="", description="要合成的文字")
    voice_id: str | None = Field(default=None, description="RVC 音色 id，空则用默认")
    emotion: str | None = Field(
        default=None,
        description="手动指定情绪：happy / excited / gentle / neutral；空则自动判断",
    )


@dataclass
class Synthesis:
    path: str
    voice: str
    emotion: str
    rate: int
    pitch: int
    seconds: float
    queue_seconds: float


# --------------------------------------------------------------------- 上游


def _rvc_url(settings: Settings, path: str) -> str:
    return f"{settings.rvc_url}{path}"


async def fetch_models(settings: Settings, timeout: float = 10.0) -> list[dict]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(_rvc_url(settings, "/api/v1/models"))
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"读取 rvc-next 模型库失败：{exc}") from exc
    return [model for model in data if isinstance(model, dict)] if isinstance(data, list) else []


def _normalize_voice(model: dict) -> dict:
    return {
        "id": model.get("id"),
        "name": model.get("name") or model.get("id"),
        "sample_rate": model.get("sample_rate"),
        "version": model.get("version"),
        "info": model.get("info") or "",
        "has_index": model.get("has_index"),
        "size": model.get("size"),
    }


async def _resolve_voice(
    settings: Settings, client: httpx.AsyncClient, requested: str | None
) -> str:
    """决定这次用哪个音色：请求指定 > 配置默认 > 模型库第一个。"""
    try:
        resp = await client.get(_rvc_url(settings, "/api/v1/models"))
        resp.raise_for_status()
        models = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取 rvc-next 模型库失败：{exc}") from exc

    ids = [str(m.get("id")) for m in models if isinstance(m, dict) and m.get("id")]
    if requested:
        if ids and requested not in ids:
            raise HTTPException(
                status_code=404,
                detail=f"音色 {requested} 不存在，可用：{', '.join(ids)}",
            )
        return requested
    if settings.voice_id:
        if ids and settings.voice_id not in ids:
            logger.warning("配置的 VOICE_ID=%s 不在模型库里，重新自动选择", settings.voice_id)
        else:
            return settings.voice_id
    if not ids:
        raise HTTPException(
            status_code=409,
            detail="rvc-next 里还没有音色，请先导入 .pth 和 .index 模型",
        )
    return ids[0]


async def _wait_job(client: httpx.AsyncClient, settings: Settings, job_id: str) -> dict:
    deadline = time.monotonic() + settings.job_timeout
    while time.monotonic() < deadline:
        resp = await client.get(_rvc_url(settings, f"/api/v1/jobs/{job_id}"))
        resp.raise_for_status()
        job = resp.json()
        state = job.get("state")
        if state == "completed":
            return job
        if state in ("failed", "cancelled", "interrupted"):
            raise HTTPException(
                status_code=502,
                detail=f"RVC 任务失败：{job.get('error') or state}",
            )
        await asyncio.sleep(0.25)
    raise HTTPException(status_code=504, detail="RVC 任务超时")


def _emotion_params(settings: Settings, text: str, requested: str | None) -> tuple[str, int, int]:
    emotion = (requested or "").strip().lower()
    if emotion not in EMOTIONS:
        emotion = detect_emotion(text) if settings.emotion_enabled else "neutral"
    if not settings.emotion_enabled:
        return emotion, 0, 0
    offset = EMOTIONS[emotion]
    scale = settings.emotion_scale
    return emotion, int(round(offset["rate"] * scale)), int(round(offset["pitch"] * scale))


async def synthesize(
    settings: Settings,
    lock: asyncio.Lock,
    text: str,
    voice_id: str | None = None,
    emotion: str | None = None,
) -> Synthesis:
    started = time.monotonic()
    queue_started = started

    emotion_name, rate, pitch = _emotion_params(settings, text, emotion)
    client_timeout = httpx.Timeout(settings.job_timeout + 60.0, connect=10.0)

    async with lock:
        queue_seconds = time.monotonic() - queue_started
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            voice = await _resolve_voice(settings, client, voice_id)

            # 1) 文字 -> 干声
            try:
                tts_resp = await client.post(
                    _rvc_url(settings, "/api/v1/tts"),
                    json={
                        "text": text,
                        "voice": settings.tts_voice,
                        "rate": rate,
                        "pitch": pitch,
                    },
                )
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail=f"TTS 请求失败：{exc}") from exc
            if tts_resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"TTS 失败：{tts_resp.text[:300]}")
            audio = tts_resp.json()
            audio_id = audio.get("id")
            if not audio_id:
                raise HTTPException(status_code=502, detail="TTS 没有返回音频 id")

            # 2) 干声 -> RVC 换声
            try:
                convert_resp = await client.post(
                    _rvc_url(settings, "/api/v1/convert"),
                    json={
                        "inputs": [{"kind": "upload", "id": audio_id}],
                        "voice_id": voice,
                        "params": {
                            "pitch": settings.pitch,
                            "index_rate": settings.index_rate,
                            "protect": settings.protect,
                            "f0_method": settings.f0_method,
                        },
                        "output_format": "wav",
                    },
                )
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail=f"RVC 请求失败：{exc}") from exc
            if convert_resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"RVC 转换失败：{convert_resp.text[:300]}",
                )
            job = await _wait_job(client, settings, convert_resp.json()["id"])

    outputs = (job.get("result") or {}).get("outputs") or []
    if not outputs:
        raise HTTPException(status_code=502, detail="任务完成但没有输出文件")
    first = outputs[0]
    out_path = first if isinstance(first, str) else first.get("path", "")
    if not out_path or not Path(out_path).is_file():
        raise HTTPException(status_code=502, detail=f"输出文件不存在：{out_path}")

    return Synthesis(
        path=out_path,
        voice=voice,
        emotion=emotion_name,
        rate=rate,
        pitch=pitch,
        seconds=time.monotonic() - started,
        queue_seconds=queue_seconds,
    )


async def _warmup(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    await asyncio.sleep(1.0)
    started = time.monotonic()
    try:
        result = await synthesize(settings, app.state.lock, "你好呀，声音服务预热完成。")
        logger.info("预热完成：%s，用时 %.1f 秒", result.voice, time.monotonic() - started)
    except Exception:
        logger.exception("预热失败（不影响服务启动，第一条语音会慢一些）")


# --------------------------------------------------------------------- 应用


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.lock = asyncio.Lock()
        app.state.started_at = time.time()
        logger.info(
            "TTS-RVC 服务启动：rvc=%s voice=%s tts_voice=%s",
            settings.rvc_url,
            settings.voice_id or "(自动)",
            settings.tts_voice,
        )
        if settings.warmup:
            app.state.warmup_task = asyncio.create_task(_warmup(app))
        yield
        task = getattr(app.state, "warmup_task", None)
        if task and not task.done():
            task.cancel()

    app = FastAPI(title="TTS-RVC 语音服务", version=VERSION, lifespan=lifespan)

    @app.get("/")
    async def root() -> dict:
        return {
            "name": "tts-rvc-service",
            "version": VERSION,
            "rvc": settings.rvc_url,
            "endpoints": ["/tts", "/voices", "/health", "/emotions"],
        }

    @app.get("/health")
    async def health() -> dict:
        payload: dict[str, Any] = {
            "ok": False,
            "service": "tts-rvc",
            "version": VERSION,
            "rvc": settings.rvc_url,
            "voice": settings.voice_id or None,
            "tts_voice": settings.tts_voice,
            "pitch": settings.pitch,
            "index_rate": settings.index_rate,
            "protect": settings.protect,
            "f0_method": settings.f0_method,
            "emotion_enabled": settings.emotion_enabled,
            "emotion_scale": settings.emotion_scale,
            "uptime_seconds": round(time.time() - getattr(app.state, "started_at", time.time()), 1),
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(_rvc_url(settings, "/api/v1/app/health"))
                resp.raise_for_status()
                models_resp = await client.get(_rvc_url(settings, "/api/v1/models"))
                models = models_resp.json() if models_resp.status_code == 200 else []
            payload["ok"] = True
            payload["voices"] = [m.get("id") for m in models if isinstance(m, dict)]
        except Exception as exc:
            payload["error"] = str(exc)
        return payload

    @app.get("/voices")
    async def voices() -> list[dict]:
        return [_normalize_voice(model) for model in await fetch_models(settings)]

    @app.get("/emotions")
    async def emotions() -> dict:
        return {
            "enabled": settings.emotion_enabled,
            "scale": settings.emotion_scale,
            "emotions": EMOTIONS,
            "rules": {name: list(words) for name, words in KEYWORDS.items()},
            "note": "低落情绪不表达，命中负面词按 neutral 处理。",
        }

    @app.post("/tts")
    async def tts(body: TtsIn) -> FileResponse:
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="text 不能为空")
        if len(text) > settings.max_text_chars:
            raise HTTPException(
                status_code=422,
                detail=f"文本超过上限 {settings.max_text_chars} 字",
            )
        result = await synthesize(settings, app.state.lock, text, body.voice_id, body.emotion)
        return FileResponse(
            result.path,
            media_type="audio/wav",
            filename="voice.wav",
            headers={
                "X-TTS-Seconds": f"{result.seconds:.2f}",
                "X-Queue-Seconds": f"{result.queue_seconds:.2f}",
                "X-Emotion": result.emotion,
                "X-Emotion-Rate": str(result.rate),
                "X-Emotion-Pitch": str(result.pitch),
                "X-Voice": result.voice,
            },
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = _env_int("PORT", 9880)
    uvicorn.run("app:app", host=host, port=port, log_level="info")
