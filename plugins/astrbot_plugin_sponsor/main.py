"""透明、低打扰的机器人赞助与功能共创插件。"""

import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At
from astrbot.api.star import Context, Star, register

PLUGIN_NAME = "astrbot_plugin_sponsor"
DATA_DIR = Path("/opt/astrbot/data/plugin_data") / PLUGIN_NAME
DATA_FILE = DATA_DIR / "sponsor.json"
TIMEZONE = ZoneInfo("Asia/Shanghai")
DAY = 86400
# 仅为个人档位提供天数；金额完全由管理员按实际到账登记。
TIERS = {
    "1": ("支持者", 30, "感谢标识、自愿公开感谢名单、新功能投票权"),
    "2": ("高级支持者", 30, "支持者权益、新功能优先体验（以实际上线为准）"),
    "3": ("共创支持者", 30, "高级支持者权益、参与功能内测与共创投票"),
    "群": ("群组支持", 30, "群支持标识与共建投票；定制和额度需另行协商"),
}
DEFAULT_STATE = {
    "schema_version": 1,
    "sponsors": {},
    "ledger": [],
    "candidates": {},
    "votes": {},
    "goal": "0",
}


def now_ts() -> int:
    return int(time.time())


def fmt_time(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), TIMEZONE).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "-"


def fmt_date(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), TIMEZONE).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return "-"


def money(value) -> str:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            return "0"
        quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        # 固定小数格式，避免 Decimal.normalize() 将 120 输出为 1.2E+2。
        text = format(quantized, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    except (InvalidOperation, ValueError):
        return "0"


def valid_month(text: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}", text):
        return False
    try:
        datetime.strptime(text, "%Y-%m")
        return True
    except ValueError:
        return False


def current_month() -> str:
    return datetime.now(TIMEZONE).strftime("%Y-%m")


@register(
    PLUGIN_NAME,
    "Kimi",
    "透明的赞助权益、感谢名单、月度收支与功能投票；不自动确认收款",
    "1.1.1",
)
class SponsorPlugin(Star):
    """所有资金操作仅由管理员人工确认，普通用户无法自行开通权益。"""

    def __init__(self, context: Context):
        super().__init__(context)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self.state = json.loads(json.dumps(DEFAULT_STATE, ensure_ascii=False))
        self._load()
        from .webui import SponsorWebApi

        self.web_api = SponsorWebApi(self)
        self.web_api.register()

    def _load(self) -> None:
        if not DATA_FILE.exists():
            return
        try:
            with DATA_FILE.open(encoding="utf-8") as file:
                raw = json.load(file)
            if not isinstance(raw, dict):
                raise ValueError("数据根节点不是对象")
            for key in ("sponsors", "candidates", "votes"):
                if not isinstance(raw.get(key, {}), dict):
                    raise ValueError(f"{key} 不是对象")
            if not isinstance(raw.get("ledger", []), list):
                raise ValueError("ledger 不是数组")
            self.state["sponsors"] = raw.get("sponsors", {})
            self.state["ledger"] = raw.get("ledger", [])
            self.state["candidates"] = raw.get("candidates", {})
            self.state["votes"] = raw.get("votes", {})
            self.state["goal"] = money(raw.get("goal", "0"))
        except Exception as exc:
            # 损坏文件绝不被初始数据覆盖；让加载失败并保留现场。
            logger.error(f"[sponsor] 数据读取失败，原文件已保留: {exc}")
            raise

    def _save_locked(self) -> None:
        temporary = DATA_FILE.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(self.state, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, DATA_FILE)

    @staticmethod
    def _target(event: AstrMessageEvent, explicit: str = "") -> str:
        if explicit.isdigit() and 4 <= len(explicit) <= 12:
            return explicit
        chain = event.message_obj.message if event.message_obj else []
        self_id = str(event.get_self_id() or "")
        for segment in chain or []:
            if isinstance(segment, At):
                qq = str(segment.qq)
                if qq not in ("all", self_id) and qq.isdigit():
                    return qq
        return ""

    @staticmethod
    def _is_group(event: AstrMessageEvent) -> bool:
        return bool(event.get_group_id())

    def _summary(self, month: str) -> tuple[Decimal, Decimal]:
        income = Decimal("0")
        expense = Decimal("0")
        for row in self.state["ledger"]:
            if row.get("month") != month or row.get("status") == "pending":
                continue
            try:
                amount = Decimal(str(row.get("amount", "0")))
                if not amount.is_finite():
                    continue
                if row.get("kind") == "income":
                    income += amount
                elif row.get("kind") == "expense":
                    expense += amount
            except (InvalidOperation, ValueError):
                logger.warning(f"[sponsor] 跳过异常账目: {row.get('id', '-')}")
        return income, expense

    def _ledger_rows(self, month: str) -> list:
        rows = [r for r in self.state["ledger"] if r.get("month") == month and r.get("status") != "pending"]
        return sorted(rows, key=lambda r: int(r.get("time", 0)), reverse=True)[:8]

    def _benefits_text(self) -> str:
        return (
            "🤝 支持机器人共建\n"
            "基础功能继续免费，赞助完全自愿；无群管理、隐私或绕过限制权限。\n\n"
            "个人档位（权益期均从管理员登记之日起 30 天）：\n"
            "1 支持者｜感谢标识、自愿公开名单、功能投票\n"
            "2 高级支持者｜上述权益＋新功能优先体验\n"
            "3 共创支持者｜上述权益＋参与内测与共创投票\n"
            "群 群组支持｜群支持标识与共建投票（30 天）\n"
            "金额不设强制门槛；推荐参考：个人 5/15/30 元，群组 29 元。\n\n"
            "到账后由管理员人工登记，不会自动开通；额度提升和定制功能以实际公告为准。\n"
            "命令：/我的权益　/赞助名单　/赞助公示 [YYYY-MM]　/功能投票　/投喂"
        )

    @filter.command("赞助")
    async def sponsor(self, event: AstrMessageEvent):
        """查看赞助档位、规则和可用命令。"""
        yield event.plain_result(self._benefits_text())

    @filter.command("我的权益")
    async def my_benefits(self, event: AstrMessageEvent):
        """查询自己的权益；群聊中也可查询本群。"""
        uid = str(event.get_sender_id())
        gid = str(event.get_group_id() or "")
        lines = []
        for key, title in ((f"u:{uid}", "个人"), (f"g:{gid}", "本群")):
            if key.endswith(":"):
                continue
            row = self.state["sponsors"].get(key)
            if not row:
                continue
            active = int(row.get("expires_at", 0)) > now_ts()
            lines.append(
                f"{title}：{row.get('tier_name', '支持者')}｜"
                f"{'有效' if active else '已到期'}｜至 {fmt_date(row.get('expires_at'))}"
            )
        if not lines:
            # 不泄露其他用户或群组的信息。
            yield event.plain_result("暂未查询到个人或本群权益。基础功能仍可免费使用；查看 /赞助。")
            return
        yield event.plain_result("💎 权益查询\n" + "\n".join(lines) + "\n权益不含群管理、隐私访问或无限资源。")

    @filter.command("赞助名单")
    async def sponsor_list(self, event: AstrMessageEvent):
        """仅显示明确选择公开的有效赞助者；默认不公开。"""
        rows = []
        for key, row in self.state["sponsors"].items():
            if not row.get("public") or int(row.get("expires_at", 0)) <= now_ts():
                continue
            nickname = str(row.get("display_name", "")).strip()
            if not nickname:
                continue
            kind = "群" if key.startswith("g:") else "个人"
            rows.append((int(row.get("expires_at", 0)), f"{nickname}（{kind}·{row.get('tier_name', '支持者')}）"))
        rows.sort(reverse=True)
        if not rows:
            yield event.plain_result("感谢每一位支持者。目前暂无选择公开昵称的有效赞助者。")
            return
        lines = ["💛 自愿公开的感谢名单", "以下仅展示有效且明确选择公开的昵称："]
        lines.extend(f"{i}. {text}" for i, (_, text) in enumerate(rows[:30], 1))
        if len(rows) > 30:
            lines.append(f"……另有 {len(rows) - 30} 位/群")
        yield event.plain_result("\n".join(lines))

    @filter.command("赞助公示")
    async def public_report(self, event: AstrMessageEvent):
        """查看某月已人工登记的汇总，不代表支付平台自动对账。"""
        parts = (event.message_str or "").split()
        month = parts[1] if len(parts) > 1 else current_month()
        if len(parts) > 2 or not valid_month(month):
            yield event.plain_result("用法：/赞助公示 [YYYY-MM]，例如 /赞助公示 2026-10")
            return
        income, expense = self._summary(month)
        balance = income - expense
        lines = [
            f"📊 {month} 赞助与运营公示（人工登记）",
            f"赞助收入：{money(income)} 元",
            f"运营支出：{money(expense)} 元",
            f"收支差额：{money(balance)} 元",
        ]
        goal = Decimal(self.state.get("goal", "0"))
        if goal > 0:
            progress = min(Decimal("100"), income / goal * 100) if goal else Decimal("0")
            lines.append(f"本月目标：{money(goal)} 元｜收入覆盖 {progress.quantize(Decimal('0.1'))}%")
        lines.append("最近账目：")
        rows = self._ledger_rows(month)
        if rows:
            for row in rows:
                sign = "+" if row.get("kind") == "income" else "-"
                note = str(row.get("note", "未填写"))[:50]
                lines.append(f"{sign}{money(row.get('amount'))} 元｜{note}")
        else:
            lines.append("暂无人工登记记录。")
        lines.append("不公开付款凭证、QQ 号或交易单号；超额部分用于后续运营与开发。")
        yield event.plain_result("\n".join(lines))

    @filter.command("功能投票")
    async def feature_vote(self, event: AstrMessageEvent):
        """列表、投票、提名；每个 QQ 对同一候选项仅一票。"""
        parts = (event.message_str or "").split(maxsplit=2)
        action = parts[1] if len(parts) > 1 else "列表"
        candidates = self.state["candidates"]

        if action in ("列表", "list") and len(parts) == 2:
            if not candidates:
                yield event.plain_result("暂无候选功能。可用 /功能投票 提名 功能名称 提议。")
                return
            ordered = sorted(candidates.items(), key=lambda kv: len(kv[1].get("voters", [])), reverse=True)
            lines = ["🗳️ 功能共创投票", "每个 QQ 对同一候选功能限一票；结果供排期参考。"]
            for cid, row in ordered[:20]:
                lines.append(f"[{cid}] {row.get('title', '未命名')}｜{len(row.get('voters', []))} 票")
            lines.append("投票：/功能投票 投 编号；提名：/功能投票 提名 功能名称")
            yield event.plain_result("\n".join(lines))
            return

        if action in ("投", "投票", "vote") and len(parts) == 3:
            cid = parts[2].strip()
            row = candidates.get(cid)
            if not row:
                yield event.plain_result("没有这个候选编号，请先发送 /功能投票 查看列表。")
                return
            uid = str(event.get_sender_id())
            async with self._lock:
                voters = row.setdefault("voters", [])
                if uid in voters:
                    yield event.plain_result(f"你已经投过 [{cid}] {row.get('title')}，无需重复投票。")
                    return
                voters.append(uid)
                self._save_locked()
            yield event.plain_result(f"投票成功：[{cid}] {row.get('title')}，当前 {len(voters)} 票。")
            return

        if action in ("提名", "nominate") and len(parts) == 3:
            title = parts[2].strip()
            if not (2 <= len(title) <= 40):
                yield event.plain_result("功能名称需为 2～40 个字符，请勿包含付款或个人信息。")
                return
            normalized = title.casefold()
            for row in candidates.values():
                if str(row.get("title", "")).casefold() == normalized:
                    yield event.plain_result("相同功能已在候选列表中，可直接投票。")
                    return
            cid = uuid.uuid4().hex[:6]
            async with self._lock:
                candidates[cid] = {
                    "title": title,
                    "voters": [],
                    "proposer": str(event.get_sender_id()),
                    "created_at": now_ts(),
                }
                self._save_locked()
            yield event.plain_result(f"已加入候选：[{cid}] {title}。可用 /功能投票 投 {cid} 支持它。")
            return

        yield event.plain_result("用法：/功能投票\n/功能投票 投 编号\n/功能投票 提名 功能名称")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("赞助管理")
    async def sponsor_admin(self, event: AstrMessageEvent):
        """管理员人工登记、续期、撤销、记账和设置目标。"""
        parts = (event.message_str or "").split(maxsplit=8)
        if len(parts) < 2:
            yield event.plain_result(self._admin_help())
            return
        action = parts[1]

        if action in ("登记", "续期"):
            # /赞助管理 登记 QQ号 档位 金额 [公开昵称|-]
            if len(parts) < 5:
                yield event.plain_result("用法：/赞助管理 登记 QQ号 1|2|3|群 金额 [公开昵称|-]")
                return
            target = self._target(event, parts[2])
            tier_key = parts[3]
            if not target or tier_key not in TIERS:
                yield event.plain_result("请提供有效 QQ 号（或 @某人）及档位 1、2、3、群。")
                return
            try:
                amount = Decimal(parts[4])
                if not amount.is_finite() or amount < 0 or amount > Decimal("1000000"):
                    raise InvalidOperation
            except InvalidOperation:
                yield event.plain_result("金额须为 0～1000000 的有效数字，请按实际到账登记。")
                return
            display_name = parts[5].strip() if len(parts) > 5 else ""
            public = bool(display_name and display_name != "-")
            if public and not (1 <= len(display_name) <= 20):
                yield event.plain_result("公开昵称须为 1～20 个字符；不公开请写 -。")
                return
            async with self._lock:
                row = await self._create_sponsor(
                    target, tier_key, amount, display_name if public else "",
                    action, str(event.get_sender_id()),
                )
                self._save_locked()
            visibility = f"公开昵称：{display_name}" if public else "不公开昵称"
            yield event.plain_result(
                f"✅ 已{action} {tier_name}｜到期：{fmt_date(row['expires_at'])}｜{visibility}\n"
                f"已按 {money(amount)} 元人工入账；请确保已实际到账且公开昵称已获对方同意。"
            )
            return

        if action == "撤销":
            if len(parts) < 4 or parts[3] not in ("个人", "群"):
                yield event.plain_result("用法：/赞助管理 撤销 QQ号 个人|群（只撤权益，不自动删账或退款）")
                return
            target = self._target(event, parts[2])
            key = ("g:" if parts[3] == "群" else "u:") + target
            if not target:
                yield event.plain_result("请提供有效 QQ 号或 @某人。")
                return
            async with self._lock:
                if key not in self.state["sponsors"]:
                    yield event.plain_result("未找到对应权益记录，账目未改动。")
                    return
                del self.state["sponsors"][key]
                self._save_locked()
            yield event.plain_result("已撤销权益并从公开名单移除；原人工账目保留，退款请线下处理。")
            return

        if action in ("收入", "支出"):
            if len(parts) < 4:
                yield event.plain_result(f"用法：/赞助管理 {action} 金额 用途说明")
                return
            try:
                amount = Decimal(parts[2])
                if not amount.is_finite() or amount <= 0 or amount > Decimal("1000000"):
                    raise InvalidOperation
            except InvalidOperation:
                yield event.plain_result("金额须为大于 0 且不超过 1000000 的有效数字。")
                return
            note = parts[3].strip()[:50]
            async with self._lock:
                self._append_ledger_locked("income" if action == "收入" else "expense", amount, note)
                self._save_locked()
            yield event.plain_result(f"已人工登记{action} {money(amount)} 元｜{note}")
            return

        if action == "目标":
            if len(parts) != 3:
                yield event.plain_result("用法：/赞助管理 目标 金额（0 表示不显示目标）")
                return
            try:
                goal = Decimal(parts[2])
                if not goal.is_finite() or goal < 0 or goal > Decimal("1000000"):
                    raise InvalidOperation
            except InvalidOperation:
                yield event.plain_result("目标须为 0～1000000 的有效数字。")
                return
            async with self._lock:
                self.state["goal"] = money(goal)
                self._save_locked()
            yield event.plain_result(f"本月运营目标已设为 {money(goal)} 元。")
            return

        if action == "查" and len(parts) >= 3:
            target = self._target(event, parts[2])
            if not target:
                yield event.plain_result("请提供有效 QQ 号或 @某人。")
                return
            rows = []
            for prefix, label in (("u:", "个人"), ("g:", "群")):
                row = self.state["sponsors"].get(prefix + target)
                if row:
                    rows.append(f"{label} {target}：{row['tier_name']}，至 {fmt_date(row['expires_at'])}，公开={row['public']}")
            yield event.plain_result("\n".join(rows) if rows else "未找到该号码的个人或群权益。")
            return

        yield event.plain_result(self._admin_help())

    async def _create_sponsor(self, target: str, tier_key: str, amount: Decimal,
                              display_name: str, action: str, operator: str) -> dict:
        """聊天命令与 WebUI 共用的权益写入规则；调用方须持有锁。"""
        tier_name, days, _ = TIERS[tier_key]
        key = ("g:" if tier_key == "群" else "u:") + target
        old = self.state["sponsors"].get(key, {})
        base = max(now_ts(), int(old.get("expires_at", 0))) if action == "续期" else now_ts()
        row = {
            "tier": tier_key, "tier_name": tier_name,
            "expires_at": base + days * DAY,
            "public": bool(display_name), "display_name": display_name,
            "updated_by": operator, "updated_at": now_ts(),
        }
        self.state["sponsors"][key] = row
        self._append_ledger_locked("income", amount, f"{tier_name}人工{action}", confirmed=True, sponsor_key=key)
        return row

    def _append_income_locked(self, amount: Decimal, note: str, target: str) -> None:
        # 目标号码只留在内存调用中，不写入公开账目；具体权益存于受保护的 sponsors 数据。
        self._append_ledger_locked("income", amount, note)

    def _append_ledger_locked(self, kind: str, amount: Decimal, note: str,
                              confirmed: bool = True, sponsor_key: str = "",
                              sponsor_row: dict | None = None) -> None:
        # 旧账目没有 status，按已确认处理；pending 永不进入公开汇总。
        row = {
            "id": uuid.uuid4().hex[:10], "kind": kind,
            "amount": money(amount), "note": note,
            "month": current_month(), "time": now_ts(),
            "status": "confirmed" if confirmed else "pending",
        }
        if sponsor_key:
            row["sponsor_key"] = sponsor_key
        if sponsor_row is not None and not confirmed:
            row["sponsor_row"] = sponsor_row
        self.state["ledger"].append(row)

    @staticmethod
    def _admin_help() -> str:
        return (
            "🛠 赞助管理（仅管理员；请先确认实际到账）\n"
            "/赞助管理 登记 QQ号 1|2|3|群 金额 [公开昵称|-]\n"
            "/赞助管理 续期 QQ号 档位 金额 [公开昵称|-]\n"
            "/赞助管理 撤销 QQ号 个人|群\n"
            "/赞助管理 查 QQ号\n"
            "/赞助管理 收入 金额 用途说明\n"
            "/赞助管理 支出 金额 用途说明\n"
            "/赞助管理 目标 金额\n"
            "登记即新增一笔人工收入；撤销不自动退款。公开昵称前应取得对方同意。"
        )

    async def terminate(self):
        pass
