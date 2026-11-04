# Story 004：Telegram 管理员审核

## 依赖
Story 001–003。

## 开发任务
- D1 pending 写入成功后，私聊 `SPAM_ADMIN_ID` 配置的唯一管理员：展示截断并转义的消息、昵称/用户名、可用 Bio、命中规则、可选模型预测。必须区分 `unknown` 与 `ham`。
- Inline Keyboard **恰好两个按钮**：`✅ Spam`、`❌ Not Spam`；不增加 Skip。未点击一直 pending。
- `callback_data` 只携带样本 ID/动作；严格校验操作者是否为唯一配置的管理员，及时应答 callback。
- 点击后原子更新 D1 为 `reviewed` 并写入 `spam` 或 `ham`；**写入成功后**再修改通知。安全处理重复点击、过期 callback 和 D1 故障。
- 新增仅管理员可用的 `/dataset` 统计命令。禁止在群里公开完整个人信息。
- 私聊通知发送失败时保留 D1 pending，记录脱敏错误；说明后续如何人工找回 pending（V1 不要求自动补发）。

## 验收标准
- `pending -> reviewed` 流程符合共享接口约定。
- 未审核样本不会获得训练标签。
- 没有模型时审核流程仍正常。
- 填写 `docs/notes/004-notes.md` 并更新进度。
