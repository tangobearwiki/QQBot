# 模型分流插件

## 默认规则

- 普通用户纯文本：`Sensenova/glm-5.2`
- 普通用户图片、视频或引用媒体：`Ark/ark-code-latest`
- 有效个人赞助者（档位 1～3）：`Ark/ark-code-latest` 优先，含纯文本

权益读取自本机 `astrbot_plugin_sponsor/sponsor.json`，每次 LLM 请求重新检查，登记、续期、撤销及到期即时生效。数据缺失、损坏或过期时自动按普通用户处理，不阻断聊天。群组赞助不会给成员共享个人优先权益。

## 配置项

在 AstrBot WebUI 插件配置中设置：

- `text_provider_id`：普通纯文本 Provider
- `multimodal_provider_id`：普通多模态 Provider
- `enable_sponsor_priority`：赞助者优先路由开关
- `sponsor_min_tier`：最低档位，1/2/3
- `sponsor_provider_id`：赞助者优先 Provider，默认 Ark

修改配置后在 WebUI 重载插件，或重启 AstrBot。本功能只选择已配置的 Provider，不保证外部服务额度、速度或可用性；Ark 限流时仍可能触发框架原有重试/回退。
