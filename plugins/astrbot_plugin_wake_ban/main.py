import json
import os
import re
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At
from astrbot.api.star import Context, Star, register

DATA_DIR = "./data/plugin_data/astrbot_plugin_wake_ban"
DATA_FILE = os.path.join(DATA_DIR, "banned.json")
DURATION_RE = re.compile(r"^(?:([1-9]\d*)([smhd])|永久|perm|permanent)$", re.I)
DURATION_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


@register(
    "astrbot_plugin_wake_ban",
    "serUNzmcsYXp2R1",
    "对话唤醒权限管理，支持临时封禁及到期自动解除",
    "1.2.0",
    "https://github.com/serUNzmcsYXp2R1/astrbot_plugin_wake_ban",
)
class WakeBanPlugin(Star):
    """管理用户/群聊的对话唤醒权限（仅封禁对话，不封工具命令）。

    /ban @某人|QQ号 [时长] [理由]
    /ban group [时长] [理由]
    时长：30s、10m、2h、7d；不填时长则永久封禁。
    /unban @某人|QQ号、/unban group：提前解除；/banlist：查看名单。
    """

    def __init__(self, context: Context):
        super().__init__(context)
        os.makedirs(DATA_DIR, exist_ok=True)
        self.banned_users: dict[str, dict] = {}
        self.banned_groups: dict[str, dict] = {}
        self._load()
        self._purge_expired()

    # ---------- 存储与到期 ----------

    def _load(self):
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict) and "users" in raw:
                    self.banned_users = raw.get("users", {}) or {}
                    self.banned_groups = raw.get("groups", {}) or {}
                else:  # 兼容 v1.0 纯用户字典
                    self.banned_users = raw or {}
        except Exception as e:
            logger.error(f"[wake_ban] 读取禁用名单失败: {e}")

    def _save(self):
        try:
            tmp_file = DATA_FILE + ".tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(
                    {"users": self.banned_users, "groups": self.banned_groups},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, DATA_FILE)
        except Exception as e:
            logger.error(f"[wake_ban] 写入禁用名单失败: {e}")

    @staticmethod
    def _is_active(info: dict, now: float | None = None) -> bool:
        expires_at = info.get("expires_at")
        # 旧记录和 expires_at=None 都是永久封禁；坏数据按永久处理，避免误放。
        if expires_at is None:
            return True
        try:
            return float(expires_at) > (time.time() if now is None else now)
        except (TypeError, ValueError):
            return True

    def _purge_expired(self) -> bool:
        now = time.time()
        changed = False
        for records, kind in (
            (self.banned_users, "用户"),
            (self.banned_groups, "群"),
        ):
            expired = [key for key, info in records.items() if not self._is_active(info, now)]
            for key in expired:
                records.pop(key, None)
                changed = True
                logger.info(f"[wake_ban] {kind} {key} 的临时封禁已到期，自动解除")
        if changed:
            self._save()
        return changed

    # ---------- 参数与显示 ----------

    def _self_id(self, event: AstrMessageEvent) -> str:
        try:
            return str(event.get_self_id() or "")
        except Exception:
            return ""

    def _get_at_target(self, event: AstrMessageEvent) -> tuple[str, str]:
        chain = event.message_obj.message if event.message_obj else []
        self_id = self._self_id(event)
        for seg in chain or []:
            if isinstance(seg, At) and str(seg.qq) not in ("all", self_id):
                return str(seg.qq), (seg.name or "")
        return "", ""

    def _parse_target(self, event: AstrMessageEvent) -> tuple[str, str]:
        qq, name = self._get_at_target(event)
        if qq:
            return qq, name
        parts = (event.message_str or "").split()
        for part in parts[1:]:
            if part.isdigit():
                return part, ""
        return "", ""

    @staticmethod
    def _parse_duration(token: str) -> tuple[bool, int | None]:
        """返回 (该 token 是否为合法时长, 秒数)；None 表示永久。"""
        match = DURATION_RE.fullmatch(token.strip())
        if not match:
            return False, None
        if not match.group(1):
            return True, None
        return True, int(match.group(1)) * DURATION_SECONDS[match.group(2).lower()]

    def _parse_ban_args(
        self, event: AstrMessageEvent, target_qq: str = "", group: bool = False
    ) -> tuple[str, str]:
        """返回 (错误提示, 理由)；合法参数直接写入 self._pending_duration。

        @ 目标通过消息段定位，不依赖 @昵称(QQ号) 文本，兼容带空格的昵称。
        """
        self._pending_duration = None
        parts = (event.message_str or "").strip().split()
        if group:
            rest = parts[2:]
        elif self._get_at_target(event)[0]:
            # 命令与目标之后的文本。At 段可靠时，第一个 @...(...) 即目标。
            text = " ".join(parts[1:])
            match = re.search(r"@.*?\(\s*" + re.escape(target_qq) + r"\s*\)", text)
            rest = text[match.end():].strip().split() if match else []
        else:
            rest = parts[2:]

        if rest:
            is_duration, seconds = self._parse_duration(rest[0])
            if is_duration:
                self._pending_duration = seconds
                rest = rest[1:]
            elif re.fullmatch(r"\d+", rest[0]):
                return "时长请带单位，例如 30m、2h、7d。", ""
            elif re.fullmatch(r"\d+[smhdSMHD]", rest[0]):
                return "无效时长：数值须为正整数，例如 30m、2h、7d。", ""
        return "", " ".join(rest).strip()

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _format_time(timestamp: float) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))

    def _make_record(self, event, name: str, reason: str, seconds: int | None) -> dict:
        now = time.time()
        record = {
            "name": name,
            "reason": reason,
            "by": str(event.get_sender_id()),
            "time": self._now(),
            "expires_at": now + seconds if seconds is not None else None,
        }
        return record

    def _ban_text(self, info: dict) -> str:
        expires_at = info.get("expires_at")
        return "永久" if expires_at is None else f"至 {self._format_time(float(expires_at))}"

    def _remaining_text(self, info: dict) -> str:
        expires_at = info.get("expires_at")
        if expires_at is None:
            return "永久"
        seconds = max(0, int(float(expires_at) - time.time()))
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        remaining = (
            f"{days}天{hours}小时" if days else
            f"{hours}小时{minutes}分钟" if hours else
            f"{minutes}分{seconds}秒"
        )
        return f"{self._format_time(float(expires_at))} 到期（剩余 {remaining}）"

    # ---------- 命令 ----------

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("ban")
    async def ban(self, event: AstrMessageEvent):
        """/ban @某人|QQ号 [时长] [理由]，或 /ban group [时长] [理由]"""
        parts = (event.message_str or "").split()
        sub = parts[1].lower() if len(parts) > 1 else ""
        if sub in ("group", "群"):
            gid = str(event.get_group_id() or "")
            if not gid:
                yield event.plain_result("⚠️ 群级禁用需要在群聊中使用 /ban group。")
                return
            error, reason = self._parse_ban_args(event, group=True)
            if error:
                yield event.plain_result(error)
                return
            seconds = self._pending_duration
            info = self._make_record(event, "", reason, seconds)
            self.banned_groups[gid] = info
            self._save()
            logger.info(f"[wake_ban] 群 {gid} 对话唤醒已禁用：{self._ban_text(info)}")
            yield event.plain_result(
                f"🚫 已禁用本群的对话唤醒权限（{self._ban_text(info)}）。"
                "工具类查询不受影响，/unban group 可提前恢复。"
            )
            return

        qq, name = self._parse_target(event)
        if not qq:
            yield event.plain_result(
                "用法：/ban @某人|QQ号 [时长] [理由]；/ban group [时长] [理由]。"
                "时长如 30m、2h、7d，不填则永久。"
            )
            return
        error, reason = self._parse_ban_args(event, target_qq=qq)
        if error:
            yield event.plain_result(error)
            return
        seconds = self._pending_duration
        info = self._make_record(event, name, reason, seconds)
        self.banned_users[qq] = info
        self._save()
        logger.info(f"[wake_ban] 用户 {qq} 对话唤醒已禁用：{self._ban_text(info)}")
        yield event.plain_result(
            f"🚫 已禁用 {name or qq} 的对话唤醒权限（{self._ban_text(info)}）。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("unban")
    async def unban(self, event: AstrMessageEvent):
        """解除禁用: /unban @某人|QQ号 或 /unban group"""
        self._purge_expired()
        parts = (event.message_str or "").split()
        sub = parts[1].lower() if len(parts) > 1 else ""
        if sub in ("group", "群"):
            gid = str(event.get_group_id() or "")
            if gid in self.banned_groups:
                self.banned_groups.pop(gid, None)
                self._save()
                yield event.plain_result("✅ 已恢复本群的对话唤醒权限。")
            else:
                yield event.plain_result("本群不在禁用名单中。")
            return

        qq, name = self._parse_target(event)
        if not qq:
            yield event.plain_result("用法：/unban @某人|QQ号 或 /unban group")
            return
        if qq in self.banned_users:
            self.banned_users.pop(qq, None)
            self._save()
            yield event.plain_result(f"✅ 已恢复 {name or qq} 的对话唤醒权限。")
        else:
            yield event.plain_result(f"{name or qq} 不在禁用名单中。")

    @filter.command("banlist")
    async def banlist(self, event: AstrMessageEvent):
        """查看禁用名单；临时封禁显示到期时间和剩余时间。"""
        self._purge_expired()
        lines = ["📋 对话唤醒禁用名单"]
        if self.banned_groups:
            lines.append("\n【群级】")
            for gid, info in self.banned_groups.items():
                reason = f"｜理由：{info['reason']}" if info.get("reason") else ""
                lines.append(
                    f"  群 {gid}{reason}｜{info.get('time', '')}｜{self._remaining_text(info)}"
                )
        if self.banned_users:
            lines.append("\n【用户】")
            for qq, info in self.banned_users.items():
                name = info.get("name") or ""
                reason = f"｜理由：{info['reason']}" if info.get("reason") else ""
                lines.append(
                    f"  {qq} {name}{reason}｜{info.get('time', '')}｜{self._remaining_text(info)}"
                )
        if not self.banned_groups and not self.banned_users:
            lines.append("（空）")
        yield event.plain_result("\n".join(lines))

    # ---------- 拦截 ----------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def gate(self, event: AstrMessageEvent):
        """到期惰性自动解封；仅拦截对话唤醒，斜杠命令和工具查询放行。"""
        try:
            self._purge_expired()
            sender = str(event.get_sender_id() or "")
            gid = str(event.get_group_id() or "")
            user_blocked = bool(sender) and sender in self.banned_users
            group_blocked = bool(gid) and gid in self.banned_groups
            if not user_blocked and not group_blocked:
                return
            text = (event.message_str or "").strip()
            if text.startswith("/"):
                return
            is_group = bool(gid)
            chain = event.message_obj.message if event.message_obj else []
            self_id = self._self_id(event)
            has_at_self = any(
                isinstance(seg, At) and str(seg.qq) == self_id for seg in (chain or [])
            )
            if is_group and has_at_self:
                who = "群聊整体" if group_blocked else sender
                logger.info(f"[wake_ban] 已拦截被禁对象 {who} 在群 {gid} 的对话唤醒")
                yield event.plain_result("🚫 对话唤醒权限已被限制，如有疑问请联系群管理员。")
                event.stop_event()
            elif not is_group and user_blocked:
                logger.info(f"[wake_ban] 已拦截被禁用户 {sender} 的私聊对话唤醒")
                yield event.plain_result("🚫 你的对话唤醒权限已被限制，如有疑问请联系群管理员。")
                event.stop_event()
        except Exception as e:
            logger.error(f"[wake_ban] 拦截器异常: {e}")
