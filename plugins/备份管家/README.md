# astrbot_plugin_backup_butler 备份管家

一个 AstrBot 插件：**定时备份机器人数据，并把备份包自动私聊推送到你的 QQ**。

## 它做什么

- 每天（或按你设定的间隔）配合服务器脚本 `/opt/backup/bot_backup.sh` 生成备份包
- 备份内容：**AstrBot 全部插件、数据库（SQLite 快照，保证一致性）、知识库、配置文件** + **NapCat 配置**
- 自动排除缓存目录（temp/logs/dist），备份包小而精（实测约 50~60M）
- 备份完成后通过 NapCat 的 `upload_private_file` 把压缩包**私聊发给你**
- 推送成功会记录日期做幂等，不会重复打扰；服务器 cron 停了它还会自动补跑备份

## 配置项

在 AstrBot WebUI 的插件管理页修改，改完即生效：

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `owner_qq` | int | 3376784421 | 接收备份包的 QQ 号 |
| `push_hour` | int | 8 | 推送时刻·小时（0-23） |
| `push_minute` | int | 5 | 推送时刻·分钟（0-59） |
| `push_interval_days` | int | 1 | 每隔几天推一次（1=每天，2=隔天，7=每周） |

> 服务器侧的打包时间（cron `0 8 * * *`）由 `/opt/backup/bot_backup.sh` 承担；
> 插件在到达推送时刻时如果发现今天还没有备份包，会自己补跑一次脚本再推送，
> 所以只改插件配置就能整体搬家，不必动 crontab。

## 命令

| 命令 | 说明 |
|---|---|
| `/备份推送` | 立即把最新的备份包私聊发给你（手动要一份） |

## 备份包怎么恢复

`bot_backup_日期.tar.gz` 解开后得到：

```
data/            # AstrBot 数据目录（除缓存外全部内容）
  data_v4.db.snapshot   # 数据库快照（恢复时改名为 data_v4.db）
  plugins/              # 全部插件源码
  cmd_config.json       # 主配置
  knowledge_base/ ...   # 知识库、表情包等
NapCat/          # NapCat 配置目录（对应 ~/.config/QQ/NapCat）
```

恢复步骤（整台机器挂了的时候）：

1. 装好 AstrBot 与 NapCat，能启动即可
2. 把 `data/` 覆盖回 AstrBot 数据目录，`data_v4.db.snapshot` 改名为 `data_v4.db`（删除旧的 db/-wal/-shm）
3. 把 `NapCat/` 覆盖回 `~/.config/QQ/NapCat`，重启两个服务

## 注意事项

- 大文件经 QQ 通道传输偶尔慢（NapCat 上传需要几分钟），推送消息发出后请耐心等待
- QQ 登录态（扫码凭据）出于安全与易碎性**不在备份范围内**，恢复后需重新扫码
- 备份在服务器 `/opt/backup/` 保留 7 天，日志在 `/var/log/bot_backup.log`

## 依赖

- AstrBot v4.29+（`aiocqhttp` 平台适配器）
- 服务器脚本 `/opt/backup/bot_backup.sh`（本插件会自动补跑它）
