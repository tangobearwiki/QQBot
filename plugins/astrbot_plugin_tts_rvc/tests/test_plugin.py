"""插件离线测试：会话开关、指令参数、失败回退。

需要能 import 到 AstrBot：
    set ASTRBOT_ROOT=F:\\QQbot\\AstrBot
    python tests\\test_plugin.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ASTRBOT_ROOT = os.environ.get("ASTRBOT_ROOT", r"F:\QQbot\AstrBot")
ROOT = Path(__file__).resolve().parent.parent

# AstrBot 被 import 时可能往当前目录写 data/，先切到临时目录，别弄脏仓库
os.chdir(tempfile.mkdtemp(prefix="tts_rvc_test_"))

sys.path.insert(0, ASTRBOT_ROOT)
sys.path.insert(0, str(ROOT))

from astrbot.api.message_components import Plain  # noqa: E402
from astrbot.api.platform import MessageType  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [ok] {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        failures.append(name)


def load_plugin(data_dir: str):
    spec = importlib.util.spec_from_file_location("tts_rvc_plugin_test", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # 让插件把会话状态写到临时目录，别碰真实配置
    module.StarTools.get_data_dir = classmethod(lambda cls, name=None: Path(data_dir))
    return module


class FakeEvent:
    def __init__(self, key: str, message_type: MessageType, text: str = ""):
        self._key = key
        self._type = message_type
        self.message_str = text
        self._result = None

    @property
    def unified_msg_origin(self) -> str:
        return self._key

    def get_message_type(self):
        return self._type

    def get_result(self):
        return self._result

    def is_admin(self) -> bool:
        return False

    def plain_result(self, text: str):
        return {"type": "plain", "text": text}

    def chain_result(self, chain):
        return {"type": "chain", "chain": chain}


class FakeResult:
    def __init__(self, chain):
        self.chain = chain

    def is_llm_result(self) -> bool:
        return True

    def is_model_result(self) -> bool:
        return False


async def collect(agen) -> list:
    return [item async for item in agen]


def make_plugin(module):
    return module.TTSRVCPlugin(
        None,
        {
            "enabled": True,
            "service_url": "http://127.0.0.1:1/tts",
            "voice_id": "",
            "private_only": True,
            "group_default": False,
            "admin_only": False,
            "dual_output": False,
            "only_llm": True,
            "min_chars": 2,
            "max_chars": 150,
            "timeout": 2.0,
            "cache": False,
        },
    )


def test_session(plugin, module) -> None:
    print("会话开关")
    group = FakeEvent("onebot:GroupMessage:1", MessageType.GROUP_MESSAGE)
    private = FakeEvent("onebot:FriendMessage:2", MessageType.FRIEND_MESSAGE)

    check("私聊默认开", plugin._voice_enabled(private) is True)
    check("群聊默认关", plugin._voice_enabled(group) is False)

    async def run():
        opened = await collect(plugin.open_voice(group))
        return opened

    results = asyncio.run(run())
    check("打开语音有回复", bool(results) and "已打开" in results[0]["text"])
    check("群聊打开后生效", plugin._voice_enabled(group) is True)

    state = json.loads(Path(plugin._state_file).read_text(encoding="utf-8"))
    check("状态已落盘", state.get("onebot:GroupMessage:1", {}).get("enabled") is True)

    asyncio.run(collect(plugin.close_voice(group)))
    check("关闭后恢复文字", plugin._voice_enabled(group) is False)


def test_basic_helpers(plugin, module) -> None:
    print("基础工具")
    event = FakeEvent("x", MessageType.FRIEND_MESSAGE, "/voice_set xilian")
    check("解析指令参数", module.TTSRVCPlugin._arg(event) == "xilian")
    event.message_str = "/voice_set"
    check("无参数返回空", module.TTSRVCPlugin._arg(event) == "")

    check("去 /tts 后缀", plugin._service_base() == "http://127.0.0.1:1")
    plugin.config["service_url"] = "http://127.0.0.1:9880/"
    check("去尾斜杠", plugin._service_base() == "http://127.0.0.1:9880")
    plugin.config["service_url"] = "http://127.0.0.1:9880/tts/"
    check("同时去 /tts 与斜杠", plugin._service_base() == "http://127.0.0.1:9880")

    path_a = plugin._cache_path("你好呀", "xilian")
    path_b = plugin._cache_path("你好呀", "xilian")
    path_c = plugin._cache_path("你好呀", "elysia")
    check("缓存路径稳定", path_a == path_b)
    check("不同音色不同缓存", path_a != path_c)


def test_fallback(plugin, module) -> None:
    print("失败回退")
    event = FakeEvent("onebot:FriendMessage:3", MessageType.FRIEND_MESSAGE)
    text = "你好呀，今天过得怎么样？"
    event._result = FakeResult([Plain(text)])

    asyncio.run(plugin.convert_reply_to_voice(event))
    check("服务不可达时保留文字", isinstance(event._result.chain[0], Plain))
    check("文字内容不变", event._result.chain[0].text == text)

    event._result = FakeResult([Plain("嗯")])
    asyncio.run(plugin.convert_reply_to_voice(event))
    check("过短文本不转语音", isinstance(event._result.chain[0], Plain))


def test_commands(plugin, module) -> None:
    print("指令（服务不可达）")
    group = FakeEvent("onebot:GroupMessage:9", MessageType.GROUP_MESSAGE)

    status = asyncio.run(collect(plugin.voice_status(group)))
    check("状态显示连不上", "连不上" in status[0]["text"], status[0]["text"][:60])

    listing = asyncio.run(collect(plugin.voice_list(group)))
    check("音色列表报错友好", "连接不上声音服务" in listing[0]["text"])

    set_result = asyncio.run(collect(plugin.voice_set(FakeEvent(
        "onebot:GroupMessage:9", MessageType.GROUP_MESSAGE, "/voice_set xilian"
    ))))
    check("服务不可达仍保存音色", "已切换为：xilian" in set_result[0]["text"])
    check("切换音色自动开语音", plugin._voice_enabled(group) is True)

    test_result = asyncio.run(collect(plugin.voice_test(group)))
    check("测试合成失败有提示", "语音合成失败" in test_result[0]["text"])


def test_schema() -> None:
    print("配置 schema")
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    check("包含核心配置", {"enabled", "service_url", "voice_id"} <= set(schema))
    check("都有默认值", all("default" in item for item in schema.values()))


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        module = load_plugin(tmp)
        plugin = make_plugin(module)
        test_session(plugin, module)
        test_basic_helpers(plugin, module)
        test_fallback(plugin, module)
        test_commands(plugin, module)
        test_schema()

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{', '.join(failures)}")
        sys.exit(1)
    print("插件离线测试全部通过。")


if __name__ == "__main__":
    main()
