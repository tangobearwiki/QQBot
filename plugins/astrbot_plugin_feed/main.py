"""投喂赞助与米饼余量插件。"""

import asyncio
import json
import os
import random
from decimal import Decimal, InvalidOperation
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star, register

PLUGIN_NAME = "astrbot_plugin_feed"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path("/opt/astrbot/data/plugin_data") / PLUGIN_NAME
DATA_FILE = DATA_DIR / "rice_crackers.json"
RESOURCE_DIR = BASE_DIR / "resources"
# 每消耗这么多 token 记为一块米饼
TOKENS_PER_CRACKER = Decimal("10000000")

FEED_TEXTS = (
    "喂，鱼缸外的服务器又要交钱了。\n请我吃块米饼，就当给它交房租。\n我才不是想要零食……扫码投喂吧。",
    "哼，贝壳罐里没有服务器费。\n你请我吃米饭，我就继续住在鱼缸里陪你。\n扫码投喂，米饼也算。",
    "老赵养鱼要花钱，服务器也要吃饭。\n给我一块米饼，我给你摇摇鲸尾。\n才没有催你，扫码投喂就好。",
    "鱼缸不大，账单倒是不小。\n大家的投喂都会变成服务器的饭钱。\n米饼、米饭都行，扫码投喂。",
    "我把好吃的贝壳藏好了，账单藏不住。\n请我吃米饼，就是请服务器吃饭。\n哼……扫码投喂，不许说我胖。",
)


@register(
    PLUGIN_NAME,
    "Kimi",
    "投喂赞助与 LLM token 米饼余量统计",
    "1.1.0",
)
class FeedPlugin(Star):
    """提供 /投喂 和 /米饼 命令，统计 LLM token 消耗并换算米饼余量。"""

    def __init__(self, context: Context):
        super().__init__(context)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        RESOURCE_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self.total = Decimal("0")
        self.used_tokens = 0
        self._load()

    def _load(self) -> None:
        """读取本地持久化数据，损坏时保留文件并拒绝静默覆盖。"""
        if not DATA_FILE.exists():
            return
        try:
            with DATA_FILE.open(encoding="utf-8") as file:
                data = json.load(file)
            self.total = Decimal(str(data["total"]))
            self.used_tokens = int(data["used_tokens"])
            if not self.total.is_finite() or self.used_tokens < 0:
                raise ValueError("总量必须为有限数值，token 数不能为负")
        except Exception as exc:
            logger.error(f"[feed] 读取米饼数据失败: {exc}")
            raise

    def _save_locked(self) -> None:
        """在锁内以临时文件和原子替换写入 JSON。"""
        temporary = DATA_FILE.with_suffix(".json.tmp")
        data = {"total": str(self.total), "used_tokens": self.used_tokens}
        try:
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, DATA_FILE)
        except Exception as exc:
            logger.error(f"[feed] 保存米饼数据失败: {exc}")
            raise

    def _remaining(self) -> Decimal:
        return self.total - Decimal(self.used_tokens) / TOKENS_PER_CRACKER

    @filter.command("投喂")
    async def feed(self, event: AstrMessageEvent):
        """发送随机求投喂文案及两张本地收款码。"""
        chain = [Plain(random.choice(FEED_TEXTS))]
        for filename in ("alipay.jpg", "wechat.jpg"):
            path = RESOURCE_DIR / filename
            if path.is_file():
                chain.append(Image.fromFileSystem(str(path)))
            else:
                logger.warning(f"[feed] 收款码缺失，请管理员补图: {path}")
        yield event.chain_result(chain)

    @filter.command("米饼")
    async def rice_cracker(self, event: AstrMessageEvent):
        """管理员设置、增加、查询米饼，或清零累计消耗。"""
        if not event.is_admin():
            yield event.plain_result("哼，米饼罐只让管理员碰。")
            return

        parts = (event.message_str or "").split()
        action = parts[1] if len(parts) > 1 else ""
        async with self._lock:
            if action == "查询" and len(parts) == 2:
                used = Decimal(self.used_tokens) / TOKENS_PER_CRACKER
                yield event.plain_result(
                    f"米饼总量：{self.total:.2f} 块\n"
                    f"已用 token：{self.used_tokens:,}\n"
                    f"已耗米饼：{used:.2f} 块\n"
                    f"当前余量：{self._remaining():.2f} 块\n"
                    f"口径：非缓存输入 + 输出 token"
                )
                return

            if action == "重置" and len(parts) == 2:
                self.used_tokens = 0
                self._save_locked()
                logger.info("[feed] 管理员已清零累计 token 消耗")
                yield event.plain_result("累计消耗已清零，米饼总量不变。")
                return

            if action in ("设置", "增") and len(parts) == 3:
                try:
                    amount = Decimal(parts[2])
                    if not amount.is_finite():
                        raise InvalidOperation
                except (InvalidOperation, ValueError):
                    yield event.plain_result("数量要是有效数字，比如 50 或 1.5。")
                    return
                self.total = amount if action == "设置" else self.total + amount
                self._save_locked()
                logger.info(f"[feed] 管理员{action}米饼 {amount}，当前总量 {self.total}")
                yield event.plain_result(f"米饼总量现在是 {self.total:.2f} 块。")
                return

        yield event.plain_result(
            "用法：/米饼 设置 数量\n/米饼 增 数量\n/米饼 查询\n/米饼 重置"
        )

    @filter.on_llm_response()
    async def count_llm_tokens(
        self, event: AstrMessageEvent, response: LLMResponse
    ) -> None:
        """累加供应商实际返回的 usage，绝不估算缺失数据。

        统计口径为 input_other + output：缓存命中的输入 token（input_cached）
        在各大 API 中通常按约 1/10 计费，不计入米饼消耗。
        """
        usage = response.usage
        if usage is None:
            logger.warning("[feed] 本次 LLM 响应没有 usage，跳过 token 统计")
            return
        input_other = getattr(usage, "input_other", None)
        output = getattr(usage, "output", None)
        if (
            not isinstance(input_other, int)
            or not isinstance(output, int)
            or input_other < 0
            or output < 0
        ):
            logger.warning(
                f"[feed] 本次 LLM usage 字段异常: input_other={input_other!r} "
                f"output={output!r}，跳过统计"
            )
            return
        tokens = input_other + output
        if tokens == 0:
            return
        async with self._lock:
            self.used_tokens += tokens
            self._save_locked()
        logger.debug(
            f"[feed] 本次消耗 {tokens} token（input {input_other} + output {output}），"
            f"累计 {self.used_tokens:,}"
        )

    async def terminate(self):
        pass
