# 共享接口约定（草案；Story 001 必须结合真实仓库确认）

## 统一样本结构 v1
以下只是示例，**不是真实 Telegram 记录**：

```json
{
  "schema_version": 1,
  "source": "keyword",
  "message": {"chat_id": -100123, "message_id": 42, "date": "2026-11-04T08:00:00Z", "text": "帮我收米 日入上W", "caption": null, "entities": [], "media_type": null},
  "user": {"id": 12345, "username": null, "first_name": "Example", "last_name": null, "is_premium": null, "bio": null},
  "via_bot": {"id": null, "username": null},
  "matched_rule": "收米",
  "model": {"version": "v0", "kind": "similarity", "decision": "spam_like", "score": 0.83},
  "label": null,
  "status": "pending"
}
```

除 `schema_version`、`source`、`message.text`（纯媒体消息可为空字符串）、`status` 外，其他字段可以为空。原始文本和原始 Unicode 必须保留，归一化由独立函数处理。

手工种子样本：`source=manual`，`message.text=<每行文本>`，无法取得的字段全部 null，`label=spam`（仅限用户确认的样本）。不能伪装为 Pyrogram `Message` 实例。只保存必要的安全快照，不序列化 Pyrogram 内部对象或会话凭证。

## D1 存储约定
建议 `samples` 字段：`id`（整数主键）、`schema_version`、`source`、`message_json`、`text`、`status`（`pending`/`reviewed`）、`label`（NULL/`spam`/`ham`）、`model_version`、`prediction`、`prediction_score`、`created_at`、`reviewed_at`、`reviewed_by`、`review_chat_id`、`review_message_id`、`dedupe_key`。命中规则和可选 Bio 存在 `message_json` 中。为状态/标签和去重键建立索引。

在线消息按 `source + chat_id + message_id` 去重；手工种子使用归一化文本 hash；**不能静默合并相互矛盾的标签**。

回调原子更新模式：

```sql
UPDATE samples SET status='reviewed', label=?, reviewed_at=CURRENT_TIMESTAMP, reviewed_by=? WHERE id=? AND status='pending';
```

必须检查受影响行数；已审核则显示原状态，不覆盖标签。Bot 在修改 D1 前先验证管理员权限。`callback_data` 使用 `sd:<id>:s` 或 `sd:<id>:h`，满足 Telegram 64 字节限制。所有 D1 SQL 必须参数绑定，不能拼接用户输入。

## 预测接口
`predict(sample) -> {kind, decision, score, model_version, nearest_example?}`。

V0 的 `score` 表示**相似度**，不是 `P(spam)`；`unknown` 不等于 `ham`。模型不存在时返回 `unavailable`，审核仍可继续。推理不得触发管理动作。

## 故障处理
- D1 写入失败：记录脱敏错误，不发送指向不存在记录的审核按钮；关键词删除不受影响。
- Bio 查询失败：存 null，不能拖慢删除。
- D1 写入成功但 Telegram 通知失败：记录仍是 pending；以后可人工补偿。
- 模型失败：记为 `unavailable`，继续采集和审核。
- 回调失败：按钮保持可重试，不能提前显示成功。

## 隐私和运维
只采集必要字段；限制 D1 Token 权限和管理员 ID；V1 不采集头像。不存整个 `Message` 内部状态，只保存业务需要的 JSON 快照。上线前明确 pending/reviewed 原始个人信息的保留期限和删除机制。日志不得包含消息全文或 API Token。
