import asyncio
import subprocess
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.config import AstrBotConfig

BACKUP_DIR = Path("/opt/backup")
BACKUP_SCRIPT = "/opt/backup/bot_backup.sh"
STATE_FILE = Path("/opt/backup/.last_push_date")
PROBE_FILE = Path("/opt/backup/.last_probe_date")
TICK_SECONDS = 1800  # 每 30 分钟自检一次是否到推送时间
FILE_TIMEOUT = 900


def _latest_backup() -> Path | None:
    files = sorted(
        BACKUP_DIR.glob("bot_backup_*.tar.gz"),
        key=lambda p: p.stat().st_mtime,
    )
    return files[-1] if files else None


def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


@register(
    "备份管家",
    "xiaonvpu",
    "定时备份 AstrBot 数据与 NapCat 配置，并按计划把备份包私聊推送给主人",
    "2.0.0",
)
class BackupButlerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._task: asyncio.Task | None = None

    # ---------- 配置读取 ----------

    def _owner(self) -> int:
        return int(self.config.get("owner_qq", 3376784421))

    def _schedule(self) -> tuple[int, int, int]:
        hour = max(0, min(23, int(self.config.get("push_hour", 8))))
        minute = max(0, min(59, int(self.config.get("push_minute", 5))))
        interval = max(1, int(self.config.get("push_interval_days", 1)))
        return hour, minute, interval

    # ---------- 生命周期 ----------

    async def initialize(self):
        hour, minute, interval = self._schedule()
        logger.info(
            f"[backup_butler] initialize：推送目标 {self._owner()}，"
            f"计划 {hour:02d}:{minute:02d} 每 {interval} 天一次"
        )
        self._task = asyncio.create_task(self._loop())

    async def terminate(self):
        if self._task:
            self._task.cancel()

    # ---------- QQ 通道 ----------

    def _client(self):
        platform = self.context.get_platform("aiocqhttp")
        if platform is None:
            raise RuntimeError("aiocqhttp 平台未就绪")
        return platform.get_client()

    async def _probe(self) -> None:
        """发一条文本验证通道；每天最多一次，重启不重复骚扰。"""
        if PROBE_FILE.exists() and PROBE_FILE.read_text().strip() == _today_str():
            logger.info("[backup_butler] 今日已自检过，跳过")
            return
        client = self._client()
        hour, minute, interval = self._schedule()
        await asyncio.wait_for(
            client.call_action(
                "send_private_msg",
                user_id=self._owner(),
                message=(
                    "🤖 备份管家已上线："
                    f"每 {interval} 天的 {hour:02d}:{minute:02d}"
                    "会把备份包发到这里。"
                ),
            ),
            timeout=60,
        )
        PROBE_FILE.write_text(_today_str())
        logger.info("[backup_butler] 自检：私聊文本通道正常")

    async def _push(self, path: Path) -> None:
        client = self._client()
        await asyncio.wait_for(
            client.call_action(
                "send_private_msg",
                user_id=self._owner(),
                message=(
                    f"📦 备份管家：今日备份包 {path.name}"
                    f"（约 {path.stat().st_size // 1048576}M）发送中…"
                ),
            ),
            timeout=60,
        )
        await asyncio.wait_for(
            client.call_action(
                "upload_private_file",
                user_id=self._owner(),
                file=str(path),
                name=path.name,
            ),
            timeout=FILE_TIMEOUT,
        )
        STATE_FILE.write_text(_today_str())
        logger.info(f"[backup_butler] 已推送 {path.name}")

    # ---------- 定时循环 ----------

    def _due_today(self) -> bool:
        hour, minute, interval = self._schedule()
        now = datetime.now()
        if (now.hour, now.minute) < (hour, minute):
            return False
        if STATE_FILE.exists():
            last = STATE_FILE.read_text().strip()
            if last == _today_str():
                return False
            try:
                delta = (
                    datetime.strptime(_today_str(), "%Y%m%d")
                    - datetime.strptime(last, "%Y%m%d")
                ).days
            except ValueError:
                return True
            if delta < interval:
                return False
        return True

    async def _tick(self) -> None:
        if not self._due_today():
            return
        path = _latest_backup()
        if path is None or _today_str() not in path.name:
            logger.info("[backup_butler] 今日无备份包，补跑备份脚本")
            subprocess.run(
                [BACKUP_SCRIPT], check=True, timeout=600, capture_output=True
            )
            path = _latest_backup()
        if path:
            await self._push(path)
        else:
            logger.error("[backup_butler] 备份脚本执行后仍无备份包")

    async def _loop(self):
        await asyncio.sleep(90)  # 等 NapCat 反连就绪
        try:
            await self._probe()
        except Exception as e:
            logger.error(f"[backup_butler] 启动自检失败: {e}")
        while True:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"[backup_butler] 定时推送失败: {e}")
                # 失败也记当天：当日不再自动重试，避免 30 分钟一次的骚扰
                STATE_FILE.write_text(_today_str())
            await asyncio.sleep(TICK_SECONDS)

    # ---------- 手动命令 ----------

    @filter.command("备份推送")
    async def manual_push(self, event: AstrMessageEvent):
        path = _latest_backup()
        if path is None:
            yield event.plain_result("备份目录里还没有备份包哦～")
            return
        yield event.plain_result(
            f"开始推送 {path.name}（约 {path.stat().st_size // 1048576}M）…"
        )
        try:
            await self._push(path)
        except Exception as e:
            logger.error(f"[backup_butler] 手动推送失败: {e}")
            yield event.plain_result(f"推送失败了：{e}")
