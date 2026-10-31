import json
import time
import traceback
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain, Reply, Video
from astrbot.api.star import Context, Star, register
from astrbot.core import sp

DEFAULT_TEXT_PROVIDER_ID = "Sensenova/glm-5.2"
DEFAULT_MULTIMODAL_PROVIDER_ID = "Ark/ark-code-latest"
DEFAULT_SPONSOR_PROVIDER_ID = "Ark/ark-code-latest"
SP_KEY = "provider_perf_chat_completion"
EXTRA_USED_MODEL = "_model_route_used_model"
SPONSOR_DATA_FILE = Path(
    "/opt/astrbot/data/plugin_data/astrbot_plugin_sponsor/sponsor.json"
)
# 模型名关键字 -> 尾部 emoji：glm 系 -> 💠；Ark/doubao 系 -> 🚢
MODEL_EMOJI_RULES = (
    ("glm", "💠"),
    ("ark", "🚢"),
    ("doubao", "🚢"),
)


@register(
    "astrbot_plugin_model_route",
    "ser",
    "按输入模态路由模型；有效个人赞助者可将 LLM 请求优先路由至 Ark",
    "1.4.0",
)
class ModelRoutePlugin(Star):
    """普通用户按模态路由，有效个人赞助者可优先使用配置的 Ark provider。"""

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        config = config or {}
        self.text_provider_id = str(
            config.get("text_provider_id") or DEFAULT_TEXT_PROVIDER_ID
        ).strip()
        self.multimodal_provider_id = str(
            config.get("multimodal_provider_id") or DEFAULT_MULTIMODAL_PROVIDER_ID
        ).strip()
        self.enable_sponsor_priority = bool(config.get("enable_sponsor_priority", True))
        self.sponsor_provider_id = str(
            config.get("sponsor_provider_id") or DEFAULT_SPONSOR_PROVIDER_ID
        ).strip()
        try:
            self.sponsor_min_tier = max(1, min(3, int(config.get("sponsor_min_tier", 1))))
        except (TypeError, ValueError):
            logger.warning("[model_route] sponsor_min_tier 无效，使用默认值 1")
            self.sponsor_min_tier = 1
        logger.info(
            "[model_route] 配置：text=%s, media=%s, sponsor_priority=%s, "
            "min_tier=%s, sponsor_provider=%s",
            self.text_provider_id,
            self.multimodal_provider_id,
            self.enable_sponsor_priority,
            self.sponsor_min_tier,
            self.sponsor_provider_id,
        )

    # ---------- 赞助者路由 ----------

    @staticmethod
    def _active_personal_tier(sender_id: str, data_path: Path = SPONSOR_DATA_FILE) -> int:
        """读取有效个人档位；任何异常均按非赞助者处理，不阻断聊天。"""
        try:
            if not sender_id or not data_path.is_file():
                return 0
            # 每次请求重新读取，以便登记、续期、撤销和到期即时生效。
            with data_path.open(encoding="utf-8-sig") as file:
                data = json.load(file)
            sponsors = data.get("sponsors", {})
            if not isinstance(sponsors, dict):
                return 0
            row = sponsors.get(f"u:{sender_id}", {})
            if not isinstance(row, dict):
                return 0
            # 群组 g: 权益绝不给群内普通成员共享。
            tier = int(row.get("tier", 0))
            expires_at = int(row.get("expires_at", 0))
            if tier not in (1, 2, 3) or expires_at <= int(time.time()):
                return 0
            return tier
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("[model_route] 赞助权益不可用，按普通用户路由: %s", exc)
            return 0

    def _sponsor_target(self, event: AstrMessageEvent):
        if not self.enable_sponsor_priority or not self.sponsor_provider_id:
            return None
        sender_id = str(event.get_sender_id() or "")
        tier = self._active_personal_tier(sender_id)
        if tier < self.sponsor_min_tier:
            return None
        return self.sponsor_provider_id, tier

    # ---------- 模态路由 ----------

    @staticmethod
    def _has_media(message_components) -> bool:
        for comp in message_components:
            if isinstance(comp, (Image, Video)):
                return True
            if isinstance(comp, Reply):
                chain = getattr(comp, "chain", None)
                if chain and any(isinstance(sub, (Image, Video)) for sub in chain):
                    return True
        return False

    @filter.on_llm_request()
    async def route_by_modality(self, event: AstrMessageEvent, req):
        try:
            umo = event.unified_msg_origin
            components = (
                event.message_obj.message
                if event.message_obj and event.message_obj.message
                else []
            )
            sponsor = self._sponsor_target(event)
            if sponsor:
                target, tier = sponsor
                reason = f"有效个人赞助者档位 {tier}，Ark 优先"
            else:
                has_media = self._has_media(components)
                target = (
                    self.multimodal_provider_id if has_media else self.text_provider_id
                )
                reason = "多模态" if has_media else "纯文本"
            current = await sp.get_async("umo", umo, SP_KEY, None)
            if current != target:
                await sp.put_async("umo", umo, SP_KEY, target)
                logger.info("[model_route] %s 路由 -> %s (%s)", umo, target, reason)
            else:
                logger.debug("[model_route] %s 保持 %s (%s)", umo, target, reason)
        except Exception:
            logger.error("[model_route] 路由切换失败")
            logger.error(traceback.format_exc())

    # ---------- 模型标注 ----------

    @filter.on_llm_response()
    async def capture_used_model(self, event: AstrMessageEvent, resp):
        try:
            if getattr(resp, "role", "") == "err":
                return
            model = None
            raw = getattr(resp, "raw_completion", None)
            if raw is not None:
                model = getattr(raw, "model", None)
            if not model:
                current = await sp.get_async(
                    "umo", event.unified_msg_origin, SP_KEY, None
                )
                model = current.rsplit("/", 1)[-1] if current else None
            event.set_extra(EXTRA_USED_MODEL, model)
        except Exception:
            logger.error("[model_route] 记录模型名失败")
            logger.error(traceback.format_exc())

    @filter.on_decorating_result()
    async def append_model_tag(self, event: AstrMessageEvent):
        try:
            model = event.get_extra(EXTRA_USED_MODEL)
            emoji = self._emoji_for(model)
            if not emoji:
                return
            result = event.get_result()
            if not result or not result.chain:
                return
            chain = result.chain
            # 拼进最后一个非空白文字段，避免独立 Plain 被逐段另发。
            for seg in reversed(chain):
                if isinstance(seg, Plain) and seg.text and seg.text.strip():
                    seg.text = seg.text + emoji
                    return
        except Exception:
            logger.error("[model_route] 追加模型标注失败")
            logger.error(traceback.format_exc())

    @staticmethod
    def _emoji_for(model) -> str | None:
        m = (model or "").lower()
        for keyword, emoji in MODEL_EMOJI_RULES:
            if keyword in m:
                return emoji
        return None

    async def terminate(self):
        pass
