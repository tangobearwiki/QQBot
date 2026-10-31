import asyncio
import base64
import math
import os
import urllib.request
from io import BytesIO

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image as CompImage, Plain, Reply
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest
from astrbot.core.config.astrbot_config import AstrBotConfig

API_URL = "https://token.sensenova.cn/v1/images/generations"
EDIT_API_URL = "https://token.sensenova.cn/v1/images/edits"

SIZE_MAP = {
    "1:1": "1024x1024",
    "16:9": "1344x768",
    "9:16": "768x1344",
    "4k": "2688x1536",
    "高清": "2688x1536",
}
USAGE_TEXT = (
    "用法：/画图 <描述> [--1:1|--16:9|--9:16|--4k]\n"
    "例：/画图 雪山下的星空营地 --16:9"
)
TOKEN_TO_SIZE = {
    "--1:1": "1024x1024",
    "--1k": "1024x1024",
    "--16:9": "1344x768",
    "--横版": "1344x768",
    "--9:16": "768x1344",
    "--竖版": "768x1344",
    "--4k": "2688x1536",
    "--高清": "2688x1536",
}
AR_TO_SIZE = {"1:1": "1024x1024", "16:9": "1344x768", "9:16": "768x1344"}
CMD_TOKENS = {"画图", "生成图片"}
EDIT_CMD_TOKENS = {"改图", "修改图片", "编辑图片", "p图"}


def _norm_token(t: str) -> str:
    """归一化消息令牌：小写并去掉前导唤醒符号（/ 、！等，含全角）。"""
    return t.lower().lstrip("/／！!？?：: 　")

EDIT_USAGE_TEXT = (
    "用法：回复一张图片并发送 /改图 <修改要求>\n"
    "或与图片一起发送：/改图 把背景换成雪夜\n"
    "例：/改图 把这张图改成赛博朋克夜景风格"
)

DRAW_INTENT_GUIDE = (
    "【生图/改图能力】你拥有两个真实可用的图像工具，根据用户意图选择：\n"
    "1) sensenova_text_to_image：凭空生成新图。用户想画/生成图片（插画、海报、表情包、头像、壁纸、Q版形象等）时必须调用；"
    "不要只用文字描述、不要声称无法生图；把需求扩写为含主体、场景、风格、细节的提示词；"
    "横屏/电脑壁纸用 16:9，手机壁纸/竖图用 9:16，其余默认 1:1。\n"
    "2) sensenova_image_edit：修改用户已提供的图片。用户发送或引用了一张图片并要求修改"
    "（换背景、改风格、p图、添加/去除元素、上色、变清晰、表情包化等）时必须调用该工具而不是重画一张；"
    "源图片由系统自动从当前消息/引用消息中获取，不要向用户索要图片链接。\n"
    "仅当用户只是想找现成图片（搜图、找表情包原图）时才不调用任何图像工具。"
)


def _sniff_image_size(data: bytes) -> tuple[int, int] | None:
    """从 PNG/JPEG/WebP 文件头解析 (宽, 高)，失败返回 None。"""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            w = int.from_bytes(data[16:20], "big")
            h = int.from_bytes(data[20:24], "big")
            return (w, h) if w > 0 and h > 0 else None
        if data[:2] == b"\xff\xd8":  # JPEG
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h = int.from_bytes(data[i + 5:i + 7], "big")
                    w = int.from_bytes(data[i + 7:i + 9], "big")
                    return (w, h) if w > 0 and h > 0 else None
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                else:
                    i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
            return None
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            fmt = data[12:16]
            if fmt == b"VP8 ":
                w = int.from_bytes(data[26:28], "little") & 0x3FFF
                h = int.from_bytes(data[28:30], "little") & 0x3FFF
                return (w, h) if w and h else None
            if fmt == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
            if fmt == b"VP8X":
                return (1 + int.from_bytes(data[24:27], "little"),
                        1 + int.from_bytes(data[27:30], "little"))
        return None
    except Exception:
        return None


def _normalize_for_edit(data: bytes) -> tuple[bytes, str | None, str | None]:
    """规范化为可提交 edits 接口的图片。返回 (字节, mime, 错误)。

    接口约束：PNG/JPEG/WebP，<=10MB，宽高 [256,4096]px，宽高比 <=2:1。
    有 Pillow 时自动处理：转 RGB、小图放大、超限缩小、超宽高比加白边；
    无 Pillow 时仅校验，不满足则报错。
    """
    if len(data) > 10 * 1024 * 1024:
        return b"", None, "图片超过 10MB，无法编辑"
    try:
        from PIL import Image as PILImage
    except ImportError:
        dims = _sniff_image_size(data)
        if dims is None:
            # 已知但不支持的格式给出明确提示，避免误标 png 后报出难懂的接口错误
            if data[:6] in (b"GIF87a", b"GIF89a"):
                return b"", None, "GIF 动图不支持编辑（需 PNG/JPEG/WebP）"
            if data[:2] == b"BM":
                return b"", None, "BMP 格式不支持编辑（需 PNG/JPEG/WebP，或安装 Pillow 后可自动转换）"
            return data, None, None  # 未知格式，交给接口自行校验
        w, h = dims
        if min(w, h) < 256 or max(w, h) > 4096 or w / h > 2 or h / w > 2:
            return b"", None, f"图片尺寸 {w}x{h} 不满足编辑要求（需 256-4096px、宽高比≤2:1）"
        if data[:4] == b"\x89PNG":
            return data, "image/png", None
        if data[:2] == b"\xff\xd8":
            return data, "image/jpeg", None
        return data, "image/webp", None
    try:
        img = PILImage.open(BytesIO(data))
        img.load()
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            bg = PILImage.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        if min(w, h) < 256:
            scale = 256 / min(w, h)
            img = img.resize((max(256, round(w * scale)), max(256, round(h * scale))), PILImage.NEAREST)
            w, h = img.size
        if max(w, h) > 4096:
            scale = 4096 / max(w, h)
            img = img.resize((max(256, round(w * scale)), max(256, round(h * scale))), PILImage.LANCZOS)
            w, h = img.size
        if w / h > 2:
            canvas = PILImage.new("RGB", (w, max(h, math.ceil(w / 2))), (255, 255, 255))
            canvas.paste(img, (0, (canvas.size[1] - h) // 2))
            img = canvas
        elif h / w > 2:
            canvas = PILImage.new("RGB", (max(w, math.ceil(h / 2)), h), (255, 255, 255))
            canvas.paste(img, ((canvas.size[0] - w) // 2, 0))
            img = canvas
        out = BytesIO()
        img.save(out, format="PNG")
        return out.getvalue(), "image/png", None
    except Exception as e:
        logger.warning(f"[日日新改图] 图片规范化失败，按原图提交: {e}")
        return data, None, None


@register(
    "astrbot_plugin_sensenova_t2i",
    "serUNzmcsYXp2R1",
    "商汤日日新生图（SenseNova）",
    "1.1.0",
    "基于商汤日日新 SenseNova U1.5 的 AI 生图与改图。/画图 /改图 命令；注册 sensenova_text_to_image 与 sensenova_image_edit 工具，自然语言自动调用。",
)
class SenseNovaT2IPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.cfg = config

    @filter.on_llm_request()
    async def inject_draw_intent(self, event: AstrMessageEvent, req: ProviderRequest):
        """LLM 请求前注入生图意图引导，确保自然语言生图时稳定触发工具调用。"""
        try:
            if self.cfg.get("nl_intent_guide", True):
                req.system_prompt = (req.system_prompt or "") + "\n" + DRAW_INTENT_GUIDE
        except Exception as e:
            logger.warning(f"[日日新生图] 注入生图意图引导失败: {e}")

    # ---------------- 核心：生成 + 下载 ----------------

    def _resolve_key(self) -> str:
        key = str(self.cfg.get("api_key") or "").strip()
        if not key:
            key = str(os.environ.get("SENSENOVA_API_KEY") or "").strip()
        return key

    async def _generate(self, prompt: str, size: str) -> tuple[str | None, str | None]:
        """调用日日新生图 API，返回 (图片URL, 错误)。"""
        api_key = self._resolve_key()
        if not api_key:
            return None, "未配置 SenseNova API Key（插件配置 api_key 或环境变量 SENSENOVA_API_KEY）"
        payload = {
            "model": str(self.cfg.get("model") or "sensenova-u1.5-lite"),
            "prompt": prompt[:1000],
            "size": size,
            "response_format": "url",
            "output_format": "png",
            "watermark": False,
        }
        timeout = aiohttp.ClientTimeout(total=int(self.cfg.get("timeout") or 180))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    API_URL,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=timeout,
                ) as resp:
                    if resp.status != 200:
                        text = (await resp.text())[:200]
                        return None, f"API 返回 {resp.status}: {text}"
                    data = await resp.json()
        except Exception as e:
            logger.error(f"[日日新生图] 请求失败: {e}")
            return None, f"请求异常: {e}"
        urls = [d.get("url") for d in (data.get("data") or []) if d.get("url")]
        if not urls:
            return None, "接口未返回图片 URL"
        return urls[0], None

    @staticmethod
    def _download_sync(url: str, timeout: int = 60) -> bytes:
        """用 urllib 直接拉取签名 URL 字节（绕开 aiohttp/yarl 重编码导致的 OSS 403）。"""
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                raise RuntimeError(f"下载失败 HTTP {r.status}")
            return r.read()

    async def _fetch_image_bytes(self, url: str) -> tuple[bytes | None, str | None]:
        try:
            data = await asyncio.to_thread(self._download_sync, url)
            if not data or not data.startswith(b"\x89PNG"):
                return None, "下载内容不是有效图片"
            return data, None
        except Exception as e:
            logger.error(f"[日日新生图] 图片下载失败: {e}")
            return None, f"图片下载失败: {e}"

    # ---------------- 改图：取图 → 规范化 → 调接口 ----------------

    def _extract_image_urls(self, event: AstrMessageEvent) -> list[str]:
        """从当前消息与被引用消息中提取图片（http URL 或 data URL），最多 5 张。"""
        urls: list[str] = []
        comps = list(getattr(event.message_obj, "message", None) or [])
        chains = [comps]
        for c in comps:
            if isinstance(c, Reply):
                chains.append(list(c.chain or []))
        for chain in chains:
            for c in chain:
                if not isinstance(c, CompImage):
                    continue
                u = str(getattr(c, "url", "") or "")
                f = str(getattr(c, "file", "") or "")
                if not u and f.startswith("http"):
                    u = f
                if u.startswith("http"):
                    urls.append(u)
                elif f.startswith("base64://"):
                    urls.append("data:image/png;base64," + f[len("base64://"):])
        seen, out = set(), []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out[:5]

    async def _images_to_dataurls(self, urls: list[str]) -> tuple[list[dict], str | None]:
        """下载图片并规范化为 edits 接口的 Data URL 载荷。"""
        imgs = []
        for u in urls[:5]:
            if u.startswith("data:"):
                try:
                    data = base64.b64decode(u.split(",", 1)[-1])
                except Exception:
                    return [], "图片数据解析失败"
            else:
                try:
                    data = await asyncio.to_thread(self._download_sync, u)
                except Exception as e:
                    return [], f"图片下载失败: {e}"
            payload, mime, nerr = _normalize_for_edit(data)
            if nerr:
                return [], nerr
            if not payload:
                return [], "图片处理失败"
            imgs.append({
                "image_url":
                    f"data:{mime or 'image/png'};base64,"
                    + base64.b64encode(payload).decode()
            })
        if not imgs:
            return [], "未获取到图片"
        return imgs, None

    async def _edit(self, prompt: str, image_urls: list[str]) -> tuple[str | None, str | None]:
        """调用日日新图片编辑 API，返回 (结果图URL, 错误)。"""
        api_key = self._resolve_key()
        if not api_key:
            return None, "未配置 SenseNova API Key（插件配置 api_key 或环境变量 SENSENOVA_API_KEY）"
        imgs, err = await self._images_to_dataurls(image_urls)
        if err:
            return None, err
        payload = {
            "model": str(self.cfg.get("edit_model") or "sensenova-u1.5-lite"),
            "images": imgs,
            "prompt": prompt[:800],
            "size": "auto",
            "response_format": "url",
            "output_format": "png",
            "watermark": False,
        }
        timeout = aiohttp.ClientTimeout(total=int(self.cfg.get("timeout") or 180))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    EDIT_API_URL,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=timeout,
                ) as resp:
                    if resp.status != 200:
                        text = (await resp.text())[:200]
                        return None, f"API 返回 {resp.status}: {text}"
                    data = await resp.json()
        except Exception as e:
            logger.error(f"[日日新改图] 请求失败: {e}")
            return None, f"请求异常: {e}"
        urls = [d.get("url") for d in (data.get("data") or []) if d.get("url")]
        if not urls:
            return None, "接口未返回图片 URL"
        return urls[0], None

    # ---------------- 参数解析 ----------------

    @staticmethod
    def _parse_draw_args(message_str: str) -> tuple[str, str]:
        """从消息解析出 (提示词, 尺寸)，剥离命令词与尺寸旗标。"""
        tokens = message_str.strip().split()
        size = "1024x1024"
        kept = []
        for t in tokens:
            low = t.lower()
            if low in TOKEN_TO_SIZE or t in TOKEN_TO_SIZE:
                size = TOKEN_TO_SIZE.get(low) or TOKEN_TO_SIZE.get(t) or size
                continue
            if _norm_token(t) in CMD_TOKENS and not kept:
                continue
            kept.append(t)
        return " ".join(kept).strip(), size

    # ---------------- /画图 命令 ----------------

    @filter.command("画图", alias={"生成图片"})
    async def draw(self, event: AstrMessageEvent):
        """AI 生图：/画图 描述 [--16:9 等]"""
        prompt, size = self._parse_draw_args(event.message_str)
        if len(prompt) < 2:
            yield event.plain_result(USAGE_TEXT)
            return
        # 首个 yield 立即返回，占位并终止管道进入 LLM 阶段
        yield event.plain_result(f"🎨 正在生成图片（{size}），约需 20-60 秒，请稍候…")
        url, err = await self._generate(prompt, size)
        if not url:
            yield event.plain_result(f"❌ 生图失败：{err}")
            return
        data, derr = await self._fetch_image_bytes(url)
        if not data:
            yield event.plain_result(f"❌ {derr}（原图 24h 内有效：{url[:80]}…）")
            return
        caption = Plain(f"\n🎨 {prompt[:80]}" + ("（高清）" if size == "2688x1536" else ""))
        yield event.chain_result([CompImage.fromBase64(base64.b64encode(data).decode()), caption])

    # ---------------- /改图 命令 ----------------

    @staticmethod
    def _parse_edit_prompt(message_str: str) -> str:
        """剥离命令词，返回修改要求文本。"""
        tokens = message_str.strip().split()
        kept = []
        for t in tokens:
            if _norm_token(t) in EDIT_CMD_TOKENS and not kept:
                continue
            kept.append(t)
        return " ".join(kept).strip()

    @filter.command("改图", alias={"修改图片", "编辑图片", "p图", "P图"})
    async def edit_image(self, event: AstrMessageEvent):
        """AI 改图：回复一张图片并发送 /改图 修改要求"""
        prompt = self._parse_edit_prompt(event.message_str)
        imgs = self._extract_image_urls(event)
        if len(prompt) < 2:
            yield event.plain_result(EDIT_USAGE_TEXT)
            return
        if not imgs:
            yield event.plain_result(
                "未找到图片：请回复一张图片并发送 /改图 <修改要求>，或与图片一起发送。\n" + EDIT_USAGE_TEXT
            )
            return
        yield event.plain_result(f"🖼️ 已获取 {len(imgs)} 张图片，正在修改，约需 30-90 秒，请稍候…")
        url, err = await self._edit(prompt, imgs)
        if not url:
            yield event.plain_result(f"❌ 改图失败：{err}")
            return
        data, derr = await self._fetch_image_bytes(url)
        if not data:
            yield event.plain_result(f"❌ 结果下载失败：{derr}（原图 24h 内有效：{url[:80]}…）")
            return
        caption = Plain(f"\n🖼️ {prompt[:80]}")
        yield event.chain_result([CompImage.fromBase64(base64.b64encode(data).decode()), caption])

    # ---------------- LLM 生图工具 ----------------

    @filter.llm_tool(name="sensenova_text_to_image")
    async def sensenova_text_to_image(
        self, event: AstrMessageEvent, prompt: str, aspect_ratio: str = "1:1"
    ):
        """使用商汤日日新模型生成图片并发送。当用户想要画图、生成图片、插画、海报、表情包等图像创作需求时调用。
        Args:
            prompt(string): 图像描述提示词，建议写清主体、场景、风格与细节
            aspect_ratio(string): 画面比例，可选 1:1、16:9、9:16，默认 1:1
        """
        size = AR_TO_SIZE.get(str(aspect_ratio).strip(), "1024x1024")
        url, err = await self._generate(prompt, size)
        if not url:
            return f"生图失败：{err}"
        data, derr = await self._fetch_image_bytes(url)
        if not data:
            return f"图片已生成但发送失败：{derr}"
        await event.send(event.chain_result([CompImage.fromBase64(base64.b64encode(data).decode())]))
        return "图片已生成并发送给用户。"

    @filter.llm_tool(name="sensenova_image_edit")
    async def sensenova_image_edit(self, event: AstrMessageEvent, prompt: str):
        """编辑/修改用户提供的图片并生成新图。当用户发送或引用了一张图片并要求修改它（如换背景、改风格、p图、添加或去除元素、上色、变清晰、表情包化等）时调用。源图片由系统自动从消息中获取，无需用户提供链接；若用户没有发图，请提示先发送图片。
        Args:
            prompt(string): 修改要求，说明要改什么、保留什么，可包含新增元素的描述
        """
        imgs = self._extract_image_urls(event)
        if not imgs:
            return "未找到图片：请用户发送一张图片（或回复一张图片）后再说修改要求。"
        url, err = await self._edit(prompt, imgs)
        if not url:
            return f"改图失败：{err}"
        data, derr = await self._fetch_image_bytes(url)
        if not data:
            return f"图片已修改但发送失败：{derr}"
        await event.send(event.chain_result([CompImage.fromBase64(base64.b64encode(data).decode())]))
        return "图片已修改并发送给用户。"

    async def terminate(self):
        pass
