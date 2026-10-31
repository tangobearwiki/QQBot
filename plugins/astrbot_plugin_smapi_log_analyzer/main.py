import asyncio
import json
import re
import time

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig

LOG_URL_RE = re.compile(r"(?:https?://)?smapi\.io/log/([A-Za-z0-9_-]{6,64})", re.I)
FETCH_URL = "https://smapi.io/log/{log_id}?format=RawDownload"
UA = "Mozilla/5.0 (compatible; AstrBot-SMAPI-Analyzer/2.0)"

ERR_LEVELS = {"error", "fatal", "alert"}
WARN_LEVELS = {"warn", "warning"}

AI_SYSTEM_PROMPT = (
    "你是星露谷物语（Stardew Valley）模组社区的技术支持助手，说话亲切、接地气，像懂行的老玩家帮群友看日志。"
    "你会收到一份 SMAPI 日志的关键信息摘要。请用中文口语化自然语言回复，要求：\n"
    "1. 先一句话给总体结论（能不能正常进游戏、问题大不大）；\n"
    "2. 指出有问题的模组及其原因（过时、缺前置、崩溃、重复安装、冲突等），用你自己的话解释，不要罗列堆砌；\n"
    "3. 按处理顺序给出具体修复步骤（先删/更新哪个、再装什么前置、去哪里下载）；\n"
    "4. 如果日志没有问题，就轻松地告诉用户一切正常。\n"
    "硬性要求：纯自然语言段落，禁止任何 Markdown 语法（不用*、#、-、数字列表、表格、代码块），"
    "少用或不用 emoji；不要编造摘要里不存在的模组或错误；不要出现'根据日志'、'分析如下'这类机械开场白；"
    "回复控制在{max_chars}字以内。"
)

USAGE_TEXT = (
    "用法：/smapi <smapi.io 日志链接>\n"
    "例：/smapi https://smapi.io/log/xxxxxxxx\n"
    "也可以直接把 smapi.io/log/… 链接发到群里，我会自动分析"
)


@register(
    "astrbot_plugin_smapi_log_analyzer",
    "serUNzmcsYXp2R1",
    "SMAPI 日志分析",
    "2.0.0",
    "自动识别群里的 smapi.io/log 日志链接并分析：本地解析提取错误与问题模组，再由大模型综合成自然语言诊断和修复建议（LLM 失败时回退规则分析）；支持 /smapi 命令与 smapi_log_analysis 大模型工具。",
)
class SmapiLogAnalyzerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.cfg = config
        self._last_hit: dict[str, float] = {}

    # ---------------- 取日志 ----------------

    async def _fetch_log(self, log_id: str) -> tuple[dict | None, str | None]:
        url = FETCH_URL.format(log_id=log_id)
        timeout = aiohttp.ClientTimeout(total=40)
        last_err = None
        for attempt in range(2):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url, timeout=timeout, headers={"User-Agent": UA}
                    ) as resp:
                        if resp.status != 200:
                            return None, f"smapi.io 返回 HTTP {resp.status}"
                        data = await resp.json(content_type=None)
                if not isinstance(data, dict) or not (data.get("RawText") or data.get("Messages")):
                    return None, "日志数据为空（链接可能已过期，smapi.io 保存 30 天）"
                return data, None
            except Exception as e:
                last_err = e
                await asyncio.sleep(1.5)
        return None, f"获取日志失败: {last_err}"

    # ---------------- 本地解析（供 AI 摘要与规则兜底） ----------------

    @staticmethod
    def _level_of(x):
        lv = x.get("Level")
        _INT_LEVELS = {0: "trace", 1: "debug", 2: "info", 3: "warn",
                       4: "error", 5: "fatal", 6: "alert"}
        if isinstance(lv, bool):
            return ""
        if isinstance(lv, (int, float)):
            return _INT_LEVELS.get(int(lv), "info")
        return str(lv or "").lower()

    def _analyze(self, data: dict, max_lines: int) -> str:
        """规则版分析：作为 LLM 失败时的兜底输出。"""
        raw = str(data.get("RawText") or "")
        mods = data.get("Mods") or []
        msgs = data.get("Messages") or []

        m = re.search(r"SMAPI version ([\d.]+)", raw) or re.search(r"SMAPI ([\d.]+) starting", raw)
        smapi_v = m.group(1) if m else "?"
        m = re.search(r"Stardew Valley ([\d.]+)", raw)
        game_v = m.group(1) if m else "?"
        n_code = data.get("TotalCodeMods")
        n_pack = data.get("TotalContentPacks")
        n_mods = sum(1 for mo in mods if mo.get("IsMod") or mo.get("IsCodeMod"))
        n_all = len(mods)

        errors = [x for x in msgs if self._level_of(x) in ERR_LEVELS]
        warns = [x for x in msgs if self._level_of(x) in WARN_LEVELS]
        if not errors and raw:
            raw_err = re.findall(r"\[\d{1,2}:\d{2}(?::\d{2})?[:.\d]*\s+(ERROR|FATAL|ALERT)", raw)
            raw_warn = re.findall(r"\[\d{1,2}:\d{2}(?::\d{2})?[:.\d]*\s+(WARN(?:ING)?)", raw)
            if raw_err or raw_warn:
                errors = [{"Text": t} for t in re.findall(
                    r"\[\d{1,2}:\d{2}(?::\d{2})?[:.\d]*\s+(?:ERROR|FATAL|ALERT)[^\]]*\]\s*(.+)", raw)]
                warns = [{"Text": t} for t in re.findall(
                    r"\[\d{1,2}:\d{2}(?::\d{2})?[:.\d]*\s+WARN(?:ING)?[^\]]*\]\s*(.+)", raw)]
        err_text = "\n".join(str(x.get("Text", "")) for x in errors)

        lines: list[str] = ["📋 SMAPI 日志分析"]
        n_display = n_mods if n_mods > 0 else n_all
        lines.append(
            f"游戏 {game_v} | SMAPI {smapi_v} | 模组 {n_display} 个"
            + (f"（代码 {n_code} + 内容包 {n_pack}）" if isinstance(n_code, int) and isinstance(n_pack, int) else "")
        )
        lines.append(f"❌ 错误 {len(errors)} 条 | ⚠️ 警告 {len(warns)} 条")

        issues: list[str] = []
        seen: set[str] = set()

        def _add(text: str):
            key = text[:40]
            if key not in seen:
                seen.add(key)
                issues.append(text)

        for mo_name, author, reason in re.findall(
            r"-\s*(.+?)\s+by\s+([^:]+?):\s*(.+)", err_text
        ):
            mo_name, reason = mo_name.strip(), reason.strip()
            if "outdated" in reason:
                _add(f"「{mo_name}」已过时：{reason[:60]} → 去下载页更新或先删除")
            elif md := re.search(r"needs the ([\w '.-]+?) mod", reason):
                _add(f"「{mo_name}」缺前置：需安装 {md.group(1)}")
            elif "duplicate" in reason:
                _add(f"「{mo_name}」重复安装：保留一份即可")
            elif "failed" in reason:
                _add(f"「{mo_name}」加载失败：{reason[:70]}")
            else:
                _add(f"「{mo_name}」被跳过：{reason[:70]}")

        for mo, where in re.findall(r"Mod '(.+?)' failed in the (\w+)", err_text):
            _add(f"「{mo}」在 {where} 阶段崩溃：多为版本不匹配或文件损坏，建议重装最新版")

        for mo, ver in re.findall(r"(?:Mod\s+)?(\w[\w .'-]{0,40}?) suggests update ([\d.]+)", raw):
            _add(f"「{mo.strip()}」有新版 {ver} 可更新")

        if re.search(r"blocked|prevented.*(repeated|spam)", err_text, re.I):
            _add("存在被 SMAPI 屏蔽的刷屏错误（同一错误重复过多次）")

        bad = [(mo.get("Name"), mo.get("Errors")) for mo in mods if mo.get("Errors")]
        if bad:
            bad.sort(key=lambda x: -x[1])
            top = "、".join(f"{n}({c})" for n, c in bad[:3])
            lines.append(f"🔥 报错最多的：{top}")

        if not errors and not warns:
            lines.append("✅ 未发现错误和警告，日志一切正常")
        elif not issues:
            for x in errors[:max(2, max_lines - 6)]:
                txt = re.sub(r"\s+", " ", str(x.get("Text", "")))[:90]
                if txt:
                    _add(f"错误：{txt}")

        if data.get("HasApiUpdate"):
            lines.append("⬆️ SMAPI 本体有新版本")
        if issues:
            lines.append("── 问题清单 ──")
            lines.extend(f"{i}. {t}" for i, t in enumerate(issues[:max_lines], 1))
            if len(issues) > max_lines:
                lines.append(f"…另有 {len(issues) - max_lines} 条未列出")
            lines.append(
                "💡 处理顺序：先删/更新过时模组 → 补缺的前置 → 单独开启排查崩溃模组"
            )
        elif errors or warns:
            lines.append("💡 有报错但未匹配已知模式，建议把上面错误原文发到模组发布页询问")

        if data.get("HasModUpdates"):
            lines.append("ℹ️ 检测到有模组可更新（见上方清单）")
        return "\n".join(lines)

    # ---------------- AI 摘要构建 ----------------

    def _build_digest(self, data: dict, max_lines: int) -> str:
        raw = str(data.get("RawText") or "")
        mods = data.get("Mods") or []
        msgs = data.get("Messages") or []

        m = re.search(r"SMAPI version ([\d.]+)", raw) or re.search(r"SMAPI ([\d.]+) starting", raw)
        smapi_v = m.group(1) if m else "?"
        m = re.search(r"Stardew Valley ([\d.]+)", raw)
        game_v = m.group(1) if m else "?"
        n_mods = sum(1 for mo in mods if mo.get("IsMod") or mo.get("IsCodeMod"))
        n_code = data.get("TotalCodeMods")
        n_pack = data.get("TotalContentPacks")

        errors = [x for x in msgs if self._level_of(x) in ERR_LEVELS]
        warns = [x for x in msgs if self._level_of(x) in WARN_LEVELS]
        if not errors and raw:
            errors = [{"Text": t} for t in re.findall(
                r"\[\d{1,2}:\d{2}(?::\d{2})?[:.\d]*\s+(?:ERROR|FATAL|ALERT)[^\]]*\]\s*(.+)", raw)]
            warns = [{"Text": t} for t in re.findall(
                r"\[\d{1,2}:\d{2}(?::\d{2})?[:.\d]*\s+WARN(?:ING)?[^\]]*\]\s*(.+)", raw)]
        err_text = "\n".join(str(x.get("Text", "")) for x in errors)

        parts: list[str] = []
        parts.append(
            f"【环境】游戏 {game_v}，SMAPI {smapi_v}，"
            + (f"代码模组 {n_code} 个、内容包 {n_pack} 个" if isinstance(n_code, int) and isinstance(n_pack, int)
               else f"模组共 {n_mods or len(mods)} 个")
        )

        skips = re.findall(r"-\s*(.+?)\s+by\s+([^:]+?):\s*(.+)", err_text)
        if skips:
            lines = [f"- {n.strip()}（作者 {a.strip()}）：{r.strip()[:110]}" for n, a, r in skips[:18]]
            parts.append("【被跳过/加载失败的模组】\n" + "\n".join(lines))

        crashes = re.findall(r"Mod '(.+?)' failed in the (\w+)", err_text)
        if crashes:
            lines = [f"- {n} 在 {w} 阶段崩溃" for n, w in crashes[:10]]
            parts.append("【崩溃模组】\n" + "\n".join(lines))

        bad = [(mo.get("Name"), mo.get("Errors")) for mo in mods if mo.get("Errors")]
        if bad:
            bad.sort(key=lambda x: -x[1])
            parts.append("【报错次数最多的模组】" + "、".join(f"{n}({c}次)" for n, c in bad[:8]))

        updates = re.findall(r"(?:Mod\s+)?(\w[\w .'-]{0,40}?) suggests update ([\d.]+)", raw)
        if updates:
            parts.append("【可更新】" + "、".join(f"{n.strip()} 有新版 {v}" for n, v in updates[:10]))

        if re.search(r"blocked|prevented.*(repeated|spam)", err_text, re.I):
            parts.append("【刷屏屏蔽】SMAPI 屏蔽了大量重复错误，说明同一问题在疯狂刷屏")

        uniq: list[str] = []
        seen: set[str] = set()
        for x in errors:
            t = re.sub(r"\s+", " ", str(x.get("Text", ""))).strip()[:170]
            key = t[:60]
            if t and key not in seen:
                seen.add(key)
                uniq.append(t)
        if uniq:
            parts.append(f"【错误原文（去重后前 {min(len(uniq), 22)} 条，共 {len(errors)} 条）】\n"
                         + "\n".join(uniq[:22]))
        if warns and not errors:
            wt = [re.sub(r"\s+", " ", str(x.get("Text", "")))[:120] for x in warns[:8]]
            parts.append("【警告原文（前8条）】\n" + "\n".join(wt))

        flags = []
        if data.get("HasApiUpdate"):
            flags.append("SMAPI 本体有新版本")
        if data.get("HasModUpdates"):
            flags.append("有模组可更新")
        if flags:
            parts.append("【更新提示】" + "；".join(flags))

        digest = "\n\n".join(parts)
        return digest[:3200]

    async def _ai_analyze(self, data: dict, umo: str | None, max_lines: int) -> str | None:
        try:
            provider = self.context.get_using_provider(umo) if umo else self.context.get_using_provider()
            if provider is None:
                provider = self.context.get_using_provider()
            if provider is None:
                logger.warning("[SMAPI分析] 无可用 LLM provider，回退规则分析")
                return None
            max_chars = int(self.cfg.get("ai_max_chars") or 420)
            digest = self._build_digest(data, max_lines)
            system = AI_SYSTEM_PROMPT.replace("{max_chars}", str(max_chars))
            resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt="请根据以下 SMAPI 日志摘要进行诊断：\n\n" + digest,
                    system_prompt=system,
                    request_max_retries=3,
                ),
                timeout=75,
            )
            text = (resp.completion_text or "").strip() if resp else ""
            if not text:
                return None
            # 清理可能混入的 Markdown 符号，确保纯自然语言
            text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
            text = text.replace("**", "").replace("*", "")
            text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
            if len(text) > max_chars + 160:
                text = text[: max_chars + 160] + "…"
            return text
        except asyncio.TimeoutError:
            logger.warning("[SMAPI分析] LLM 分析超时（75s），回退规则分析")
            return None
        except Exception as e:
            logger.error(f"[SMAPI分析] LLM 分析失败: {e}，回退规则分析")
            return None

    # ---------------- 公共入口 ----------------

    async def analyze_url(self, url_or_id: str, umo: str | None = None) -> str:
        m = LOG_URL_RE.search(url_or_id or "")
        if not m:
            return "这不是有效的 smapi.io 日志链接（形如 smapi.io/log/xxxxxxxx）"
        data, err = await self._fetch_log(m.group(1))
        if err:
            return f"❌ {err}"
        max_lines = int(self.cfg.get("max_issue_lines") or 8)
        try:
            if self.cfg.get("enable_ai_analysis", True):
                ai = await self._ai_analyze(data, umo, max_lines)
                if ai:
                    return ai
            return self._analyze(data, max_lines)
        except Exception as e:
            logger.error(f"[SMAPI分析] 分析异常: {e}")
            return "❌ 日志解析出现异常，请稍后再试"

    def _cooldown_ok(self, key: str) -> bool:
        cd = float(self.cfg.get("cooldown_seconds") or 15)
        now = time.time()
        if len(self._last_hit) > 500:
            cutoff = now - max(cd, 300)
            self._last_hit = {k: v for k, v in self._last_hit.items() if v > cutoff}
        if now - self._last_hit.get(key, 0) < cd:
            return False
        self._last_hit[key] = now
        return True

    # ---------------- 被动：群里出现日志链接 ----------------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if not self.cfg.get("enable_passive", True):
            return
        text = event.message_str or ""
        m = LOG_URL_RE.search(text)
        if not m:
            return
        if event.is_at_or_wake_command:
            return
        key = event.unified_msg_origin or "global"
        if not self._cooldown_ok(key):
            return
        yield event.plain_result("🔍 检测到 SMAPI 日志链接，正在分析…")
        result = await self.analyze_url(m.group(0), event.unified_msg_origin)
        yield event.plain_result(result)

    # ---------------- 命令 ----------------

    @filter.command("smapi", alias={"日志分析", "log分析"})
    async def smapi_cmd(self, event: AstrMessageEvent):
        """分析 smapi.io 日志链接：/smapi <链接>"""
        tokens = (event.message_str or "").strip().split()
        rest = " ".join(
            t for t in tokens if t.lower().lstrip("/！!？?") not in {"smapi", "日志分析", "log分析"}
        )
        if not LOG_URL_RE.search(rest):
            yield event.plain_result(USAGE_TEXT)
            return
        yield event.plain_result("🔍 正在获取并分析日志…")
        result = await self.analyze_url(rest, event.unified_msg_origin)
        yield event.plain_result(result)

    # ---------------- LLM 工具 ----------------

    @filter.llm_tool(name="smapi_log_analysis")
    async def smapi_log_analysis(self, event: AstrMessageEvent, log_url: str):
        """分析 SMAPI（星露谷模组加载器）日志链接并给出诊断。当用户发来 smapi.io/log/… 链接并询问游戏报错、模组冲突、为什么进不去游戏等问题时调用。
        Args:
            log_url(string): 用户提供的 smapi.io/log/ 日志链接（完整链接或仅 ID 均可）
        """
        result = await self.analyze_url(log_url, event.unified_msg_origin)
        return (
            "以下是对该日志的完整诊断结论，请用你自己的语气转达给用户：保留问题模组名称和修复步骤的关键信息，"
            "不要遗漏修复建议，不要逐字照读，用自然口语重新组织：\n\n" + result
        )

    async def terminate(self):
        pass
