# 共享接口约定

## 统一样本结构 v1
以下只是示例，**不是真实 Telegram 记录**：

```json
{
  "schema_version": 1,
  "source": "rule",
  "message": {"chat_id": -100123, "message_id": 42, "date": "2026-11-04T08:00:00Z", "text": "帮我收米 日入上W", "caption": null, "entities": [], "media_type": null},
  "user": {"id": 12345, "username": null, "first_name": "Example", "last_name": null, "is_premium": null, "bio": null},
  "via_bot": {"id": null, "username": null},
  "matched_rule": "blacklist_message:收米",
  "model": {"version": "v0", "kind": "similarity", "decision": "spam_like", "score": 0.83},
  "label": null,
  "status": "pending"
}
```

除 `schema_version`、`source`、`message.text`（纯媒体消息可为空字符串）、`status` 外，其他字段可以为空。原始文本和原始 Unicode 必须保留，归一化由独立函数处理。

手工种子样本：`source=manual`，`message.text=<每行文本>`，无法取得的字段全部 null，`label=spam`（仅限用户确认的样本）。不能伪装为 Pyrogram `Message` 实例。只保存必要的安全快照，不序列化 Pyrogram 内部对象或会话凭证。

Story 001 结合真实 Kurigram/Pyrogram `Message` 后确定：

- `sample_from_message(message, matched_rule=..., bio=None)` 只同步读取消息已有字段，不执行 Telegram API 请求；缺失 Bio 保持 `null`。
- `message.text` 与 `message.caption` 分开保存；纯媒体或只有 caption 时 `message.text=""`，不把 caption 伪装成 text。
- `message.date` 输出 UTC ISO 8601；`media_type` 和 entity type 输出稳定字符串。entities 只保存类型、UTF-16 offset/length 以及 URL、关联用户 ID、语言、custom emoji ID 等必要字段。
- 缺失 `from_user` 时保留字段全为 null 的 user 快照；Inline Bot 只保存 `via_bot.id` 和 `via_bot.username`。
- 手工种子是已确认正例，因此使用 `source=manual`、`status=reviewed`、`label=spam`；在线规则命中样本使用 `source=rule`、`status=pending`、`label=null`。Story 003 在 D1 尚未部署时将原草案的 `keyword` 扩展为 `rule`，避免对非关键词规则使用错误语义。
- 基础配置环境变量为 `SPAM_LEARNING_ENABLED`、`SPAM_ADMIN_ID`、`D1_ACCOUNT_ID`、`D1_DATABASE_ID`、`D1_API_TOKEN`、`SPAM_MODEL_PATH`。功能默认关闭；开启时三项 D1 标识/凭证和单个正整数管理员 ID 必填。Story 004 根据实际运维需求将原多管理员草案 `SPAM_ADMIN_IDS` 收敛为单管理员，不保留旧环境变量兼容。
- Story 003 增加 `SPAM_CAPTURE_QUEUE_SIZE`、`SPAM_CAPTURE_WORKERS`、`SPAM_BIO_TIMEOUT_SECONDS`、`SPAM_BIO_CACHE_TTL_SECONDS`、`SPAM_BIO_CACHE_SIZE`、`SPAM_CAPTURE_SHUTDOWN_TIMEOUT_SECONDS`；具体默认值见 `README.md` 和 `docs/notes/003-notes.md`。

## D1 存储约定
Story 002 已确定 `samples` 字段：`id`（整数主键）、`schema_version`、`source`、`message_json`、`text`、`status`（`pending`/`reviewed`）、`label`（NULL/`spam`/`ham`）、`model_version`、`prediction`、`prediction_score`、`created_at`、`reviewed_at`、`reviewed_by`、`review_chat_id`、`review_message_id`、`dedupe_key`。命中规则和可选 Bio 存在完整样本 JSON `message_json` 中。数据库通过 CHECK 约束强制 pending/NULL 与 reviewed/人工标签配对，并为状态/标签、reviewed 导出和唯一去重键建立索引。

在线消息的 `dedupe_key` 是带版本前缀的 `source + chat_id + message_id` SHA-256，不直接暴露这些标识符。同一消息即使命中多条规则，重复调用 `create_pending()` 也返回已有 ID，不创建新记录，也不覆盖已有状态或标签。手工种子的导入与文本 hash 留给 Story 007；**不能静默合并相互矛盾的标签**。

Story 002 的公共存储接口：

- `D1Client.create_pending(sample) -> int`：只接受带 `chat_id/message_id` 的 `rule/pending` 样本。
- `D1Client.review(id, label, admin_id) -> ReviewOutcome`：返回 `changed` 或 `already_reviewed`；ID 不存在抛出 `D1NotFoundError`。
- `D1Client.set_review_message(id, chat_id, message_id) -> None`：在 Telegram 通知发送成功后记录单管理员私聊位置；不存在的样本抛出 `D1NotFoundError`。
- `D1Client.get(id) -> StoredSample`：`message_json` 已解析为 `StoredSample.sample` 字典。
- `D1Client.stats() -> SampleStats`：提供 total、pending、reviewed、spam、ham 数量。
- `D1Client.export_reviewed(page_size=200) -> list[StoredSample]`：使用 `id` keyset 分页并按 `id ASC` 稳定排序。

D1 Query REST 客户端使用参数化 SQL、5 秒默认超时、最多 2 次默认重试和默认 4 并发上限。只重试网络/超时、429 和有限的 5xx；同时校验 Cloudflare 顶层 `success` 与每条 SQL 的 `result[].success`。异常文本不包含 Token 或 SQL 参数。

回调原子更新模式：

```sql
UPDATE samples SET status='reviewed', label=?, reviewed_at=CURRENT_TIMESTAMP, reviewed_by=? WHERE id=? AND status='pending';
```

必须检查受影响行数；已审核则显示原状态，不覆盖标签。Bot 在修改 D1 前先验证操作者是 `SPAM_ADMIN_ID`。`callback_data` 使用 `sd:<id>:s` 或 `sd:<id>:h`，满足 Telegram 64 字节限制。所有 D1 SQL 必须参数绑定，不能拼接用户输入。

## 预测接口
`predict(sample) -> {kind, decision, score, model_version, nearest_example?}`。

V0 的 `score` 表示**相似度**，不是 `P(spam)`；`unknown` 不等于 `ham`。模型不存在时返回 `unavailable`，审核仍可继续。推理不得触发管理动作。

Story 005 已将接口实现为 `spam_learning.seed_model.PredictionResult` 和
`SeedSimilarityModel.predict(sample)`；文本便捷接口是 `predict_text(text)`。有效模型只会输出
`spam_like` 或 `unknown`，缺失、损坏或不受支持的模型由
`SimilarityPredictor.from_path()` 降级为 `unavailable`，此时 `score` 和 `model_version` 为 null。
有效模型中的空文本或没有已知特征的文本返回 `unknown`、`score=0.0`。

`PredictionResult.model_version` 是预测接口字段；统一样本 JSON 中已有的
`model.version` 保持不变。Story 006 接入时必须显式将前者映射到
`ModelSuggestion.version`，不能新增第二个含义重复的样本字段。

初始模型只使用 `message.text`，为空时回退到 `message.caption`，不使用种子中不存在的
Bio、昵称或用户名特征。预处理和训练参数全部进入 manifest：NFKC、Latin casefold、默认移除
已知零宽控制符、空白折叠、URL/mention 标记化、带文本边界的字符 2–5 gram、sublinear TF、
平滑 IDF 和 L2 归一化。原始 Unicode 种子独立保留，用于 `nearest_example`。

模型目录包含原子发布的 `manifest.json` 和内容哈希命名的 JSON artifact。模型版本由有序去重
语料及全部训练参数的 SHA-256 派生，不写入当前时间；相同输入和参数必须字节级可复现。
加载器校验 artifact SHA-256、格式版本、文件名和数据结构，不加载 `pickle`/`joblib`，且只允许
使用本地可信模型目录。默认 `spam_like` 阈值 `0.65` 是无 Ham 条件下的启发式相似度阈值，
不是经二分类校准的概率或质量承诺。

## 故障处理
- D1 写入失败：记录脱敏错误，不发送指向不存在记录的审核按钮；现有规则删除不受影响。
- Bio 查询失败：存 null，不能拖慢删除。
- D1 写入成功但 Telegram 通知失败：记录仍是 pending；以后可人工补偿。
- 模型失败：记为 `unavailable`，继续采集和审核。
- 回调失败：按钮保持可重试，不能提前显示成功。

## 隐私和运维
只采集必要字段；限制 D1 Token 权限和管理员 ID；V1 不采集头像。不存整个 `Message` 内部状态，只保存业务需要的 JSON 快照。上线前明确 pending/reviewed 原始个人信息的保留期限和删除机制。日志不得包含消息全文、完整 Bio、私密 Telegram 标识符或 API Token。
