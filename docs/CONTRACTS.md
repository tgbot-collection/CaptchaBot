# 共享接口约定

## 统一样本结构 v2
以下只是示例，**不是真实 Telegram 记录**：

```json
{
  "schema_version": 2,
  "source": "rule",
  "message": {"chat_id": -100123, "message_id": 42, "date": "2026-11-04T08:00:00Z", "text": "帮我收米 日入上W", "caption": null, "entities": [], "media_type": null},
  "user": {"id": 12345, "username": null, "first_name": "Example", "last_name": null, "is_premium": null, "bio": null, "bio_state": "empty"},
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

- `sample_from_message(message, matched_rule=..., bio=None, bio_state=...)` 只同步读取消息已有字段，不执行 Telegram API 请求；缺失 Bio 保持 `null`。
- `message.text` 与 `message.caption` 分开保存；纯媒体或只有 caption 时 `message.text=""`，不把 caption 伪装成 text。
- `message.date` 输出 UTC ISO 8601；`media_type` 和 entity type 输出稳定字符串。entities 只保存类型、UTF-16 offset/length 以及 URL、关联用户 ID、语言、custom emoji ID 等必要字段。
- 缺失 `from_user` 时保留字段全为 null 的 user 快照；Inline Bot 只保存 `via_bot.id` 和 `via_bot.username`。
- 手工种子是已确认正例，因此使用 `source=manual`、`status=reviewed`、`label=spam`；在线规则命中样本使用 `source=rule`、`status=pending`、`label=null`。Story 003 在 D1 尚未部署时将原草案的 `keyword` 扩展为 `rule`，避免对非关键词规则使用错误语义。
- 基础配置环境变量为 `SPAM_LEARNING_ENABLED`、`SPAM_ADMIN_ID`、`D1_ACCOUNT_ID`、`D1_DATABASE_ID`、`D1_API_TOKEN`、`SPAM_MODEL_PATH`。功能默认关闭；开启时三项 D1 标识/凭证和单个正整数管理员 ID 必填。Story 004 根据实际运维需求将原多管理员草案 `SPAM_ADMIN_IDS` 收敛为单管理员，不保留旧环境变量兼容。
- Story 003 增加 `SPAM_CAPTURE_QUEUE_SIZE`、`SPAM_CAPTURE_WORKERS`、`SPAM_BIO_TIMEOUT_SECONDS`、`SPAM_BIO_CACHE_TTL_SECONDS`、`SPAM_BIO_CACHE_SIZE`、`SPAM_CAPTURE_SHUTDOWN_TIMEOUT_SECONDS`；具体默认值见 `README.md` 和 `docs/notes/003-notes.md`。
- Story 006 增加 `SPAM_INFERENCE_CONCURRENCY`，默认 `2`，必须为正整数；只限制本地模型推理，不改变采集队列和 D1 客户端的既有并发上限。
- Story 009 增加 `SPAM_CAPTURE_GROUPS`：空值关闭普通群消息采集；`all` 允许全部群，负数群 ID 逗号列表仅允许指定群。普通群消息为 `source=group/status=pending/label=null`，仅通过管理员 `/review` 领取；重训仍只导出 reviewed。由于 D1 尚未部署，最终 `001_create_samples.sql` 直接支持 `rule/manual/group`，不保留额外升级脚本。配置名与既有 `SPAM_CAPTURE_*` 保持一致。
- Story 010 将样本升级为 `schema_version=2`，`bio_state` 仅允许 `present/empty/unavailable/not_requested`。普通群消息复用 Handler 已取得的 Bio，不新增请求；规则提前命中的 Bio 仍在删除后的有界 worker 中补充。项目尚未正式积累数据，因此直接重建 D1 和模型，不提供旧库升级、回填或旧模型兼容层。
- Story 011 将 Python 包统一重命名为 `spamdet`，默认模型目录改为 `models/spamdet`，不保留旧包兼容层。开发期 `make reset-d1` 只在双重确认数据库 ID 后删除 `samples` 表并执行最终 Migration；它不删除 D1 database 或其他表。
- Story 012 增加 `make spam id=1,2,3` 和 `make ham id=1,2,3`。两者只接受人工确认的标签，使用 `SPAM_ADMIN_ID` 作为 `reviewed_by`，并原子批量同步独立列与 `message_json` 中的 `status/label`。任一 ID 不存在时整批不更新；运维入口可纠正 reviewed 标签，但 Telegram callback 仍不覆盖首次审核结果。
- Story 013 不修改统一样本 Schema。`ReviewService.notify_pending()` 在 D1 pending 已成功写入后，使用已有 `message.chat_id` 查询群资料：公开群名链接到 `https://t.me/<username>`，私有超级群名链接到基于 `chat_id/message_id` 生成的 `https://t.me/c/<channel>/<message_id>`。查询或链接生成失败时仍发送无链接的审核通知；该查询不得进入规则删除关键路径，也不得记录群标识符或内容。
- Story 014 规定 `message.date` 始终保存为 UTC ISO 8601 `Z` 时间。Kurigram 返回的无时区 `datetime` 必须先按进程本地时区解释，再转换为 UTC，不能直接附加 UTC 标签。D1 `created_at` 继续使用 Cloudflare 的 UTC `CURRENT_TIMESTAMP`。由 `TZ=Asia/Shanghai` 旧容器写入且与 `created_at` 相差约 8 小时的历史 `message.date` 通过受保护的一次性固定 SQL 回填；其他字段不变。

## D1 存储约定
Story 002 已确定 `samples` 字段：`id`（整数主键）、`schema_version`、`source`、`message_json`、`text`、`status`（`pending`/`reviewed`）、`label`（NULL/`spam`/`ham`）、`model_version`、`prediction`、`prediction_score`、`created_at`、`reviewed_at`、`reviewed_by`、`review_chat_id`、`review_message_id`、`dedupe_key`。命中规则和可选 Bio 存在完整样本 JSON `message_json` 中。数据库通过 CHECK 约束强制 pending/NULL 与 reviewed/人工标签配对，并为状态/标签、reviewed 导出和唯一去重键建立索引。

在线消息的 `dedupe_key` 是带版本前缀的 `source + chat_id + message_id` SHA-256，不直接暴露这些标识符。同一消息即使命中多条规则，重复调用 `create_pending()` 也返回已有 ID，不创建新记录，也不覆盖已有状态或标签。手工种子的导入与文本 hash 留给 Story 007；**不能静默合并相互矛盾的标签**。

Story 002 的公共存储接口：

- `D1Client.create_pending(sample) -> int`：只接受带 `chat_id/message_id` 的 `rule/group pending` 样本。
- `D1Client.review(id, label, admin_id) -> ReviewOutcome`：返回 `changed` 或 `already_reviewed`；ID 不存在抛出 `D1NotFoundError`。
- `D1Client.set_review_message(id, chat_id, message_id) -> None`：在 Telegram 通知发送成功后记录单管理员私聊位置；不存在的样本抛出 `D1NotFoundError`。
- `D1Client.get(id) -> StoredSample`：`message_json` 已解析为 `StoredSample.sample` 字典。
- `D1Client.stats() -> SampleStats`：提供 total、pending、reviewed、spam、ham 数量。
- `D1Client.export_reviewed(page_size=200) -> list[StoredSample]`：使用 `id` keyset 分页并按 `id ASC` 稳定排序。
- `D1Client.label_samples(sample_ids, label, admin_id) -> list[int]`：人工运维专用的原子批量标注/纠正接口；更新 `status/label/reviewed_at/reviewed_by` 和统一样本 JSON，返回已更新 ID。任一 ID 不存在时抛出 `D1NotFoundError` 且整批不变。

D1 Query REST 客户端使用参数化 SQL、5 秒默认超时、最多 2 次默认重试和默认 4 并发上限。只重试网络/超时、429 和有限的 5xx；同时校验 Cloudflare 顶层 `success` 与每条 SQL 的 `result[].success`。异常文本不包含 Token 或 SQL 参数。

回调原子更新模式：

```sql
UPDATE samples SET status='reviewed', label=?, reviewed_at=CURRENT_TIMESTAMP, reviewed_by=? WHERE id=? AND status='pending';
```

必须检查受影响行数；已审核则显示原状态，不覆盖标签。Bot 在修改 D1 前先验证操作者是 `SPAM_ADMIN_ID`。`callback_data` 使用 `sd:<id>:s` 或 `sd:<id>:h`，满足 Telegram 64 字节限制。所有 D1 SQL 必须参数绑定，不能拼接用户输入。

## 预测接口
`predict(sample) -> {kind, decision, score, model_version, nearest_example?}`。

V0 的 `score` 表示**相似度**，不是 `P(spam)`；`unknown` 不等于 `ham`。模型不存在时返回 `unavailable`，审核仍可继续。推理不得触发管理动作。

Story 005 已将接口实现为 `spamdet.seed_model.PredictionResult` 和
`SeedSimilarityModel.predict(sample)`；文本便捷接口是 `predict_text(text)`。有效模型只会输出
`spam_like` 或 `unknown`，缺失、损坏或不受支持的模型由
`SimilarityPredictor.from_path()` 降级为 `unavailable`，此时 `score` 和 `model_version` 为 null。
有效模型中的空文本或没有已知特征的文本返回 `unknown`、`score=0.0`。

`PredictionResult.model_version` 是预测接口字段；统一样本 JSON 中已有的
`model.version` 保持不变。Story 006 接入时必须显式将前者映射到
`ModelSuggestion.version`，不能新增第二个含义重复的样本字段。模型不可用时二者均为
`null`；`ModelSuggestion.version` 因此是可空字段，不能伪造版本字符串。

Story 010 起，`predict(sample)` 使用三个隔离通道：message（text 为空时回退 caption）、Bio、
display_name（first_name + last_name）。各通道字符 n-gram 使用 `m:/b:/n:` 前缀，默认权重为
`1.0/0.75/0.25`；`username`、`user.id`、`source`、`matched_rule`、`bio_state`、人工标签和历史预测
绝不作为模型特征。`predict_text(text)` 仍是仅提供 message 通道的便捷接口。预处理和训练参数全部
进入 manifest：NFKC、Latin casefold、默认移除已知零宽控制符、空白折叠、Telegram invite、
URL/mention 标记化、带文本边界的字符 2–5 gram、sublinear TF、平滑 IDF、通道权重和 L2 归一化。
原始 Unicode 独立保留，用于审核和 `nearest_example`。仅从 `spam.txt` 构建的初始模型没有 Bio/
显示名称语料，因此这些通道要在 D1 人工审核数据重训后才产生已知特征。

模型目录包含原子发布的 `manifest.json` 和内容哈希命名的 JSON artifact。模型版本由有序去重
语料及全部训练参数的 SHA-256 派生，不写入当前时间；相同输入和参数必须字节级可复现。
加载器校验 artifact SHA-256、格式版本、文件名和数据结构，不加载 `pickle`/`joblib`，且只允许
使用本地可信模型目录。默认 `spam_like` 阈值 `0.65` 是无 Ham 条件下的启发式相似度阈值，
不是经二分类校准的概率或质量承诺。

Story 006 的在线接入约定：

- Bot 启动时从 `SPAM_MODEL_PATH` 通过 `LocalPredictor` 加载一次受支持的本地模型；模型缺失或损坏不阻止启动。
- 推理只发生在规则删除后的 `CaptureService` worker 中，通过线程池执行并由独立 semaphore 限流；任何推理异常都降级为 `unavailable`，不能阻止 D1 pending 和人工审核。
- `PredictionResult` 显式映射为 `ModelSuggestion` 后，完整建议进入 `message_json`，并由 D1 独立列保存模型版本、decision 和 score。相似度模型的 score 是余弦相似度；二分类模型的 score 是未校准 Spam 分数。
- 推理耗时日志只包含 decision 和 `duration_ms`，不包含消息原文、Bio、Telegram 私密标识符或 `nearest_example`。
- 管理员私聊 `/model` 显示可用性、类型、版本和对应 decision/score 语义。V1 manifest 为保证字节级复现不写构建时间，因此显示“未记录”，不能用文件 mtime 冒充构建日期。

Story 007 的离线重训和模型发布约定：

- `python -m spamdet.retrain export` 只调用 `D1Client.export_reviewed()`，并再次验证每条记录为
  `status=reviewed`、`label=spam/ham` 和兼容的 `schema_version=2`。pending、规则命中和历史预测
  均不能成为训练标签。
- 导出 format v2 JSONL 保留 message、Bio、display_name 原始 Unicode、各自归一化副本和来源引用。
  相同归一化多字段样本、相同标签时合并来源；出现相反标签时中止，不静默覆盖。
- 用户 ID 只生成不可逆 actor 摘要用于离线分组，绝不进入模型。相同用户、消息模板或 Bio 模板
  通过连通分量整体切分，不能横跨训练集和测试集；数据集使用 canonical JSON SHA-256 校验。
- 构建报告记录 Spam/Ham 的 Bio、显示名称、用户和模板覆盖。若只有一类包含 Bio，拒绝构建
  多字段二分类候选。条件允许时在同一留出集比较 message-only、message+bio 和完整三通道。
- 相似度候选使用所有确认 Spam，Ham 为零也必须可构建。`--classifier` 只有在两类都能形成至少
  两个模板组、从而得到两类齐全且无模板组交叉的训练/测试集时才生效；否则回退相似度候选。
  该条件只表示能执行评估，不是质量保证。
- 二分类类型为 `logistic_regression`，继续使用字符 2–5 gram TF-IDF，输出 `spam` 或 `ham`；
  `score` 是未校准 Spam 分数，不能当成可靠概率，更不能触发管理动作。
- `LocalPredictor` 按受信任本地 `manifest.json` 的 `kind` 加载 `similarity` 或
  `logistic_regression` JSON artifact。两种模型都校验格式、内容哈希、路径和结构，不加载
  `pickle`/`joblib`。
- 候选 manifest 记录 dataset hash、类别/来源数量、参数、Schema 兼容性、可用的分组留出指标
  及固定回归报告。没有固定回归集时不生成虚假对比；任何候选都必须用 `approve` 子命令显式批准。
- 候选目录和 release 目录均拒绝覆盖。批准只复制经过安全加载校验的候选并写入独立
  `approval.json`，不改变 Bot 配置、不重启进程、不自动部署。回滚通过恢复上一 release 的
  `SPAM_MODEL_PATH` 并重启完成。

## 故障处理
- D1 写入失败：记录脱敏错误，不发送指向不存在记录的审核按钮；现有规则删除不受影响。
- Bio 查询失败：存 `bio=null/bio_state=unavailable`，不能拖慢删除。
- D1 写入成功但 Telegram 通知失败：记录仍是 pending；以后可人工补偿。
- 审核通知读取群资料失败：显示无链接的“未知群组”并继续发送通知；不能回滚或丢弃已写入的 pending。
- 模型失败：记为 `unavailable`，继续采集和审核。
- 回调失败：按钮保持可重试，不能提前显示成功。

## 隐私和运维
只采集必要字段；限制 D1 Token 权限和管理员 ID；V1 不采集头像。不存整个 `Message` 内部状态，只保存业务需要的 JSON 快照。上线前明确 pending/reviewed 原始个人信息的保留期限和删除机制。日志不得包含消息全文、完整 Bio、私密 Telegram 标识符或 API Token。
