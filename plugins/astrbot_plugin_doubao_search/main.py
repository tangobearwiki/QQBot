import json
import os

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig

SEARCH_API = "https://open.feedcoopapi.com/search_api/web_search"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "search_state.json")
MAIN_CONFIG = "/opt/astrbot/data/cmd_config.json"

TIME_RANGES = ("OneDay", "OneWeek", "OneMonth", "OneYear")
CMD_NAME = "联网搜索"
ASK_HINT = "发送「/联网搜索 开」即可开启"
OFF_ASK = (
    "联网搜索未开启。请先简短询问用户是否需要联网搜索后回答，"
    f"并告知{ASK_HINT}。"
)


class DoubaoSearchPlugin(Star):
    """豆包搜索（Agent Plan 联网搜索）：

    - 「/联网搜索 开/关/状态」按会话开关联网搜索
    - 开启后，模型可通过 doubao_web_search 工具检索实时信息
    - 未开启时，遇到难以离线回答的问题会主动询问是否联网搜索
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.cfg = config
        self.enabled: dict[str, bool] = {}
        self._load_state()

    # ---------------- 状态持久化 ----------------

    def _load_state(self):
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                self.enabled = {str(k): bool(v) for k, v in data.items()}
        except Exception as e:
            logger.warning(f"[豆包搜索] 状态文件读取失败：{e}")
            self.enabled = {}

    def _save_state(self):
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.enabled, f, ensure_ascii=False, indent=1)
        except Exception as e:
            logger.warning(f"[豆包搜索] 状态文件写入失败：{e}")

    def _is_on(self, umo: str) -> bool:
        """会话开关：显式开/关优先，未设置时回落到 default_enabled 配置。"""
        explicit = self.enabled.get(umo)
        if explicit is not None:
            return explicit
        return bool(self.cfg.get("default_enabled", False))

    def _get_api_key(self) -> str | None:
        key = str(self.cfg.get("api_key") or "").strip()
        if key:
            return key
        try:
            with open(MAIN_CONFIG, "rb") as f:
                cfg = json.loads(f.read().decode("utf-8-sig"))
            for s in cfg.get("provider_sources", []):
                if s.get("id") == "Ark":
                    k = s.get("key")
                    return k[0] if isinstance(k, list) else k
        except Exception as e:
            logger.warning(f"[豆包搜索] 读取 Ark API Key 失败：{e}")
        return None

    # ---------------- /联网搜索 命令 ----------------

    @filter.command(CMD_NAME)
    async def toggle_search(self, event: AstrMessageEvent):
        """联网搜索 开/关/状态（按会话生效）"""
        raw = event.message_str.strip()
        arg = raw.replace(CMD_NAME, "", 1).strip().lower()
        umo = event.unified_msg_origin
        if arg in ("开", "开启", "on"):
            self.enabled[umo] = True
        elif arg in ("关", "关闭", "off"):
            self.enabled[umo] = False
        self._save_state()
        state = self._is_on(umo)
        if state:
            yield event.plain_result(
                "✅ 联网搜索（豆包搜索）已开启。现在提问会自动检索最新网络信息；"
                "发送「/联网搜索 关」可关闭。"
            )
        else:
            yield event.plain_result(
                "⛔ 联网搜索（豆包搜索）已关闭。遇到需要最新信息的问题时，我会先询问是否联网搜索；"
                f"{ASK_HINT}。"
            )

    # ---------------- LLM 工具 ----------------

    @filter.llm_tool(name="doubao_web_search")
    async def doubao_web_search(self, event: AstrMessageEvent, query: str, time_range: str = ""):
        """联网搜索（豆包搜索），获取实时/时效性信息。当问题需要最新事实（新闻、行情、天气、比分、软件版本、价格、政策等）或知识不确定时调用。
        Args:
            query(string): 搜索关键词，1~100 个字符
            time_range(string): 可选，限定信息时间范围：OneDay/OneWeek/OneMonth/OneYear，不确定时填空字符串
        """
        umo = event.unified_msg_origin
        if not self._is_on(umo):
            return f"【未执行搜索】{OFF_ASK}"
        return await self._do_search(event, query, time_range)

    # ---------------- 搜索执行 ----------------

    async def _do_search(self, event: AstrMessageEvent | None, query: str, time_range: str = "") -> str:
        # 标记本次会话实际用到了联网搜索，on_llm_response 据此追加声明
        if event is not None:
            event.set_extra("doubao_search_used", True)
        key = self._get_api_key()
        if not key:
            return "未配置豆包搜索 API Key（未找到 Ark provider_source），请联系管理员。"
        count = max(1, min(10, int(self.cfg.get("count", 6) or 6)))
        payload = {
            "Query": (query or "").strip()[:100],
            "SearchType": "web",
            "Count": count,
            "Filter": {"NeedContent": True, "NeedUrl": True},
            "ContentFormats": "text",
        }
        tr = (time_range or "").strip()
        if tr in TIME_RANGES:
            payload["TimeRange"] = tr
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    SEARCH_API,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
        except Exception as e:
            logger.error(f"[豆包搜索] 请求失败：{e}")
            return "联网搜索请求失败，请稍后再试。"
        web = (data.get("Result") or {}).get("WebResults") or []
        if not web:
            return "联网搜索完成，但没有找到相关结果。"
        limit = max(100, int(self.cfg.get("content_len", 400) or 400))
        lines = [f"【豆包搜索结果】关键词：{payload['Query']}"]
        for i, s in enumerate(web[:count], 1):
            title = str(s.get("Title", "")).strip()
            site = str(s.get("SiteName", "") or "").strip()
            pub = str(s.get("PublishTime", "") or "").strip()
            head = f"{i}. {title}"
            if site or pub:
                head += f"（{site}{' ' + pub if pub else ''}）"
            lines.append(head)
            if s.get("Url"):
                lines.append(f"   来源: {s.get('Url')}")
            body = (s.get("Content") or s.get("Summary") or s.get("Snippet") or "").strip()
            if body:
                lines.append(f"   内容: {body[:limit]}")
        return "\n".join(lines)

    # ---------------- LLM 请求注入 ----------------

    @filter.on_llm_response()
    async def append_search_badge(self, event: AstrMessageEvent, resp):
        """实际执行过搜索的回复，末尾统一追加"已联网"声明。"""
        if not event.get_extra("doubao_search_used"):
            return
        event.set_extra("doubao_search_used", False)
        badge = str(self.cfg.get("search_badge") or "").strip()
        if not badge:
            return
        if getattr(resp, "role", "") == "err":
            return  # 错误提示不追加
        badge_text = f"\n\n{badge}"
        if getattr(resp, "result_chain", None):
            resp.result_chain.chain.append(Plain(badge_text))
        elif getattr(resp, "completion_text", None):
            resp.completion_text = (resp.completion_text or "") + badge_text

    @filter.on_llm_request()
    async def inject_search_hint(self, event: AstrMessageEvent, req):
        umo = event.unified_msg_origin
        if self._is_on(umo):
            req.system_prompt += (
                "\n\n[联网搜索] 当前会话已开启豆包搜索。回答时效性/实时性问题时，"
                "先调用 doubao_web_search 获取最新资料再回答，并简短标注信息来源与时间。"
                "若搜索失败或无结果，请如实告知。"
            )
        else:
            req.system_prompt += (
                f"\n\n[联网搜索] 当前会话未开启联网搜索（doubao_web_search 会返回未开启提示）。"
                "当你判断问题必须依赖最新联网信息才能准确回答时，先简短询问用户是否联网搜索，"
                f"并告知{ASK_HINT}；能凭已有知识回答的问题直接回答，不必询问。"
            )

    async def terminate(self):
        pass
