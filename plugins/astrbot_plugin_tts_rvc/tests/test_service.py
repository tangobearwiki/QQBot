"""服务端离线测试：情绪、配置、参数校验。

运行：
    python tests/test_service.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "service"))

import app as svc  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [ok] {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        failures.append(name)


def test_detect_emotion() -> None:
    print("detect_emotion")
    check("开心", svc.detect_emotion("好开心呀，今天真高兴") == "happy")
    check("兴奋", svc.detect_emotion("哇！太厉害了吧") == "excited")
    check("温柔", svc.detect_emotion("别怕，我陪着你，晚安") == "gentle")
    check("低落不表达", svc.detect_emotion("今天好难过，想哭") == "neutral")
    check("空文本", svc.detect_emotion("") == "neutral")
    check("平票按中性", svc.detect_emotion("开心 别怕") == "neutral")


def test_emotion_params() -> None:
    print("_emotion_params")
    settings = svc.Settings()
    name, rate, pitch = svc._emotion_params(settings, "好开心呀", None)
    check("自动识别开心", (name, rate, pitch) == ("happy", 8, 2), f"{name},{rate},{pitch}")

    name, rate, pitch = svc._emotion_params(settings, "随便说说", "gentle")
    check("手动指定温柔", (name, rate, pitch) == ("gentle", -6, -1), f"{name},{rate},{pitch}")

    scaled = svc.Settings(emotion_scale=1.5)
    name, rate, pitch = svc._emotion_params(scaled, "好开心呀", None)
    check("情绪倍率", (rate, pitch) == (12, 3), f"{rate},{pitch}")

    disabled = svc.Settings(emotion_enabled=False)
    name, rate, pitch = svc._emotion_params(disabled, "好开心呀", None)
    check("关闭情绪后归零", (name, rate, pitch) == ("neutral", 0, 0), f"{name},{rate},{pitch}")


def test_env_file() -> None:
    print("_load_env_file")
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text(
            "# 注释\nTEST_TTS_RVC_VALUE=hello\nTEST_TTS_RVC_QUOTED=\"world\"\n",
            encoding="utf-8",
        )
        os.environ.pop("TEST_TTS_RVC_VALUE", None)
        os.environ.pop("TEST_TTS_RVC_QUOTED", None)
        svc._load_env_file(env)
        check("读取普通值", os.environ.get("TEST_TTS_RVC_VALUE") == "hello")
        check("去引号", os.environ.get("TEST_TTS_RVC_QUOTED") == "world")
        os.environ.pop("TEST_TTS_RVC_VALUE", None)
        os.environ.pop("TEST_TTS_RVC_QUOTED", None)


def test_api() -> None:
    print("HTTP 接口")
    settings = svc.Settings(rvc_url="http://127.0.0.1:1", emotion_enabled=True)
    application = svc.create_app(settings)
    with TestClient(application) as client:
        root = client.get("/")
        check("GET /", root.status_code == 200 and root.json()["version"] == svc.VERSION)

        emotions = client.get("/emotions")
        body = emotions.json()
        check(
            "GET /emotions",
            emotions.status_code == 200 and "gentle" in body["emotions"],
        )

        empty = client.post("/tts", json={"text": "   "})
        check("空文本返回 422", empty.status_code == 422, str(empty.status_code))

        too_long = client.post("/tts", json={"text": "字" * 501})
        check("超长返回 422", too_long.status_code == 422, str(too_long.status_code))

        voices = client.get("/voices")
        check("rvc 不可达返回 502", voices.status_code == 502, str(voices.status_code))

        health = client.get("/health").json()
        check("健康检查 ok=false", health.get("ok") is False)


def test_normalize_voice() -> None:
    print("_normalize_voice")
    voice = svc._normalize_voice(
        {
            "id": "xilian",
            "name": "昔涟",
            "sample_rate": 48000,
            "version": "v2",
            "info": "360epoch",
            "has_index": True,
            "size": 57552537,
        }
    )
    check("字段齐全", voice["id"] == "xilian" and voice["sample_rate"] == 48000)
    check("显示名保留", voice["name"] == "昔涟")


def main() -> None:
    test_detect_emotion()
    test_emotion_params()
    test_env_file()
    test_api()
    test_normalize_voice()
    print()
    if failures:
        print(f"失败 {len(failures)} 项：{', '.join(failures)}")
        sys.exit(1)
    print("服务端离线测试全部通过。")


if __name__ == "__main__":
    main()
