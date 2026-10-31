"""赞助插件的受鉴权 WebUI API；不包含支付接口。"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from .main import DAY, TIERS, current_month, fmt_date, fmt_time, money, now_ts

from astrbot.api import logger

if TYPE_CHECKING:
    from .main import SponsorPlugin


class SponsorWebApi:
    def __init__(self, plugin: "SponsorPlugin"):
        self.plugin = plugin

    def register(self) -> None:
        prefix = "/astrbot_plugin_sponsor/page"
        routes = (
            ("dashboard", self.dashboard, ["GET"], "赞助管理工作台"),
            ("action", self.action, ["POST"], "赞助登记、确认、续期、撤销、账目和投票管理"),
        )
        for endpoint, handler, methods, desc in routes:
            self.plugin.context.register_web_api(f"{prefix}/{endpoint}", handler, methods, desc)
        logger.info("[sponsor] WebUI API 已注册: #/plugin-view/astrbot_plugin_sponsor/sponsor")

    @staticmethod
    async def _body() -> dict:
        from astrbot.api.web import request
        data = await request.json(default={})
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _ok(data=None, message: str = "") -> dict:
        return {"status": "ok", "data": {"success": True, "message": message, **(data or {})}}

    @staticmethod
    def _fail(message: str) -> dict:
        return {"status": "ok", "data": {"success": False, "message": message}}

    def _dashboard_data(self) -> dict:
        p = self.plugin
        now, month = now_ts(), current_month()
        income, expense = p._summary(month)
        sponsors = []
        for key, row in p.state["sponsors"].items():
            if not isinstance(row, dict) or not key.startswith(("u:", "g:")):
                continue
            sponsors.append({
                "key": key,
                "kind": "group" if key.startswith("g:") else "personal",
                "target": key[2:],
                "tier": str(row.get("tier", "")),
                "tier_name": str(row.get("tier_name", "支持者")),
                "expires_at": int(row.get("expires_at", 0)),
                "expires_text": fmt_date(row.get("expires_at")),
                "active": int(row.get("expires_at", 0)) > now,
                "public": bool(row.get("public")),
                "display_name": str(row.get("display_name", "")),
                "updated_text": fmt_time(row.get("updated_at")),
            })
        sponsors.sort(key=lambda r: (not r["active"], r["expires_at"]))
        ledger = []
        for row in sorted(p.state["ledger"], key=lambda r: int(r.get("time", 0)), reverse=True)[:300]:
            status = str(row.get("status", "confirmed"))
            ledger.append({
                "id": str(row.get("id", "")),
                "kind": str(row.get("kind", "")),
                "amount": money(row.get("amount")),
                "note": str(row.get("note", "未填写")),
                "month": str(row.get("month", "")),
                "time_text": fmt_time(row.get("time")),
                "status": status if status in ("pending", "confirmed") else "confirmed",
                "sponsor_key": str(row.get("sponsor_key", "")),
            })
        pending_income = Decimal("0")
        for row in p.state["ledger"]:
            if row.get("month") == month and row.get("kind") == "income" and row.get("status") == "pending":
                try:
                    pending_income += Decimal(str(row.get("amount", "0")))
                except InvalidOperation:
                    pass
        candidates = []
        for cid, row in p.state["candidates"].items():
            if not isinstance(row, dict):
                continue
            status = str(row.get("status", "open"))
            candidates.append({
                "id": str(cid), "title": str(row.get("title", "未命名")),
                "votes": len(row.get("voters", [])),
                "created_text": fmt_time(row.get("created_at")),
                "status": status if status in ("open", "planned", "done", "rejected") else "open",
            })
        candidates.sort(key=lambda r: r["votes"], reverse=True)
        goal = Decimal(p.state.get("goal", "0"))
        return {
            "month": month, "income": money(income), "expense": money(expense),
            "balance": money(income - expense), "goal": money(goal),
            "progress": float(min(Decimal("100"), income / goal * 100)) if goal > 0 else 0,
            "pending_income": money(pending_income),
            "pending_count": sum(1 for r in ledger if r["status"] == "pending"),
            "active_count": sum(1 for r in sponsors if r["active"]),
            "sponsors": sponsors, "ledger": ledger, "candidates": candidates,
            "server_time": fmt_time(now),
            "tiers": [
                {"value": "1", "label": "支持者", "days": 30},
                {"value": "2", "label": "高级支持者", "days": 30},
                {"value": "3", "label": "共创支持者", "days": 30},
                {"value": "群", "label": "群组支持", "days": 30},
            ],
        }

    async def dashboard(self) -> dict:
        # 通过 AstrBot Plugin Page bridge 访问，继承控制台登录鉴权。
        async with self.plugin._lock:
            return self._ok(self._dashboard_data())

    async def action(self) -> dict:
        body = await self._body()
        action = str(body.get("action", "")).strip()
        try:
            async with self.plugin._lock:
                result = await self._dispatch(action, body)
                if result.get("data", {}).get("success"):
                    result["data"]["dashboard"] = self._dashboard_data()
            return result
        except (ValueError, InvalidOperation) as exc:
            return self._fail(str(exc) or "输入无效，请检查后重试")
        except Exception as exc:
            logger.exception("[sponsor] WebUI 操作失败: %s", exc)
            return self._fail("保存失败，请查看 AstrBot 日志；原数据未有意删除")

    async def _dispatch(self, action: str, body: dict) -> dict:
        p = self.plugin
        if action == "register":
            target = self._target(body.get("target"))
            tier = str(body.get("tier", ""))
            if tier not in TIERS:
                raise ValueError("请选择有效档位")
            amount = self._amount(body.get("amount"), allow_zero=True)
            display_name = str(body.get("display_name", "")).strip()
            if len(display_name) > 20:
                raise ValueError("公开昵称最多 20 个字符")
            public = bool(body.get("public")) and bool(display_name)
            if body.get("public") and not display_name:
                raise ValueError("公开名单需要填写昵称，或关闭公开选项")
            confirmed = bool(body.get("confirmed"))
            tier_name, days, _ = TIERS[tier]
            key = ("g:" if tier == "群" else "u:") + target
            row = {
                "tier": tier, "tier_name": tier_name,
                "expires_at": now_ts() + days * DAY,
                "public": public, "display_name": display_name if public else "",
                "updated_by": "dashboard", "updated_at": now_ts(),
            }
            if confirmed:
                p.state["sponsors"][key] = row
            p._append_ledger_locked(
                "income", amount, f"{tier_name}网页登记", confirmed=confirmed,
                sponsor_key=key, sponsor_row=row,
            )
            p._save_locked()
            return self._ok(message="已确认到账并开通 30 天权益" if confirmed else "已存入待确认；确认到账后才开通权益并计入公示")

        if action == "confirm":
            row = self._ledger(body.get("id"))
            if row.get("status") != "pending":
                raise ValueError("该记录已确认，无需重复操作")
            if row.get("kind") != "income":
                raise ValueError("只有待确认收入需要执行此操作")
            key, sponsor_row = str(row.get("sponsor_key", "")), row.get("sponsor_row")
            if not key.startswith(("u:", "g:")) or not isinstance(sponsor_row, dict):
                raise ValueError("该待确认记录缺少权益信息，不能自动开通；请核对原始记录")
            activated = dict(sponsor_row)
            activated["expires_at"] = now_ts() + 30 * DAY
            activated["updated_at"] = now_ts()
            p.state["sponsors"][key] = activated
            row["status"], row["confirmed_at"] = "confirmed", now_ts()
            row.pop("sponsor_row", None)
            p._save_locked()
            return self._ok(message="已确认到账、开通权益并计入本月公示")

        if action == "renew":
            key = str(body.get("key", ""))
            row = p.state["sponsors"].get(key)
            if not isinstance(row, dict):
                raise ValueError("未找到该权益，可能已被撤销")
            amount, days = self._amount(body.get("amount"), allow_zero=True), self._days(body.get("days", 30))
            row["expires_at"] = max(now_ts(), int(row.get("expires_at", 0))) + days * DAY
            row["updated_at"], row["updated_by"] = now_ts(), "dashboard"
            p._append_ledger_locked("income", amount, f"{row.get('tier_name', '支持者')}网页续期", confirmed=True, sponsor_key=key)
            p._save_locked()
            return self._ok(message=f"已续期 {days} 天并记录实际到账 {money(amount)} 元")

        if action == "revoke":
            key = str(body.get("key", ""))
            if key not in p.state["sponsors"]:
                raise ValueError("未找到该权益，可能已被撤销")
            del p.state["sponsors"][key]
            p._save_locked()
            return self._ok(message="权益已撤销；历史账目保留，未执行退款")

        if action == "ledger":
            kind = str(body.get("kind", ""))
            if kind not in ("income", "expense"):
                raise ValueError("请选择收入或支出")
            amount, note = self._amount(body.get("amount")), str(body.get("note", "")).strip()
            if not note or len(note) > 50:
                raise ValueError("用途说明须为 1～50 个字符")
            p._append_ledger_locked(kind, amount, note, confirmed=True)
            p._save_locked()
            return self._ok(message="账目已保存并计入公示")

        if action == "goal":
            p.state["goal"] = money(self._amount(body.get("goal"), allow_zero=True))
            p._save_locked()
            return self._ok(message="本月运营目标已更新")

        if action == "candidate":
            cid, status = str(body.get("id", "")), str(body.get("status", ""))
            labels = {"open": "投票中", "planned": "已排期", "done": "已完成", "rejected": "不采用"}
            if status not in labels or cid not in p.state["candidates"]:
                raise ValueError("候选功能或状态无效")
            p.state["candidates"][cid]["status"] = status
            p._save_locked()
            return self._ok(message=f"功能状态已改为“{labels[status]}”")
        return self._fail("不支持的操作")

    def _ledger(self, row_id) -> dict:
        for row in self.plugin.state["ledger"]:
            if row.get("id") == str(row_id):
                return row
        raise ValueError("未找到该账目，可能已被其他管理员处理")

    @staticmethod
    def _target(value) -> str:
        target = str(value or "").strip()
        if not target.isdigit() or not (4 <= len(target) <= 12):
            raise ValueError("请输入 4～12 位 QQ 号或群号")
        return target

    @staticmethod
    def _amount(value, allow_zero: bool = False) -> Decimal:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValueError("金额格式无效")
        minimum = Decimal("0") if allow_zero else Decimal("0.01")
        if not amount.is_finite() or amount < minimum or amount > Decimal("1000000"):
            raise ValueError("金额须为有效数字，最高 1000000 元")
        return amount

    @staticmethod
    def _days(value) -> int:
        try:
            days = int(value)
        except (ValueError, TypeError):
            raise ValueError("续期天数无效")
        if not (1 <= days <= 3650):
            raise ValueError("续期天数须为 1～3650 天")
        return days
