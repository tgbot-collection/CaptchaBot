# Captcha Bot

Captcha challenge for group.

[https://t.me/benny_captcha_bot](https://t.me/benny_captcha_bot)


# screenshot

![](assets/1.png)
![](assets/2.png)

# Usage:

add bot to your group, assign it with admin privileges, and you're good to go.

# Deploy

environment variables:

* API_ID
* API_HASH
* BOT_TOKEN
* REDIS

Spam 学习功能已接入现有规则删除路径，功能默认关闭：

* `SPAM_LEARNING_ENABLED`：默认 `false`
* `SPAM_ADMIN_ID`：唯一接收审核通知的 Telegram 管理员用户 ID（正整数）
* `D1_ACCOUNT_ID`
* `D1_DATABASE_ID`
* `D1_API_TOKEN`
* `SPAM_MODEL_PATH`：默认 `models/spam_learning`
* `SPAM_CAPTURE_QUEUE_SIZE`：有界队列容量，默认 `100`
* `SPAM_CAPTURE_WORKERS`：后台采集 worker 数，默认 `2`
* `SPAM_BIO_TIMEOUT_SECONDS`：Bio 查询超时，默认 `2.0`
* `SPAM_BIO_CACHE_TTL_SECONDS`：Bio 缓存 TTL，默认 `300.0`
* `SPAM_BIO_CACHE_SIZE`：Bio 缓存最大条目数，默认 `1000`
* `SPAM_CAPTURE_SHUTDOWN_TIMEOUT_SECONDS`：关停排空超时，默认 `5.0`

启用 `SPAM_LEARNING_ENABLED` 时，单个管理员 ID 和三项 D1 配置必须存在。
所有密钥只能通过环境变量或 Secret 注入。
规则删除前只做同步内存快照；Bio 和 D1 请求在有界后台队列中执行。队列已满时丢弃采集任务，不影响删除。
只有 D1 pending 写入成功后才会私聊该管理员。管理员可在 Bot 私聊中使用 `/dataset`
查看聚合统计，不会返回消息全文或个人信息。

审核通知发送失败时样本仍保留为 pending，V1 不自动补发。可先用 `/dataset` 确认
pending 数量，然后在 Cloudflare D1 Console 中定位未审核记录：

```sql
SELECT id, created_at, review_chat_id, review_message_id
FROM samples
WHERE status = 'pending'
ORDER BY id ASC;
```

`review_chat_id`/`review_message_id` 为 NULL 通常表示未成功记录审核通知，需要人工查看 D1
中该样本并决定后续处理；不得因为规则命中而直接将它标为 Spam。

## 初始相似度模型

Story 005 提供只使用人工确认 Spam 正例的离线字符 n-gram TF-IDF 模型。它只输出
`spam_like`、`unknown` 或 `unavailable` 建议；分数是余弦相似度，不是 Spam 概率，
也不会触发删除、封禁或禁言。

训练默认读取 UTF-8 的 `spam.txt`，每行一条，输出到现有 `SPAM_MODEL_PATH` 默认目录：

```shell
python -m spam_learning.seed_model --input spam.txt --output models/spam_learning
```

也可以使用 Make 目标；默认值与上面的命令一致：

```shell
make train-spam-model
```

需要使用虚拟环境或其他路径时可覆盖变量，例如：

```shell
make train-spam-model PYTHON=venv/bin/python SPAM_SEED_INPUT=spam.txt SPAM_MODEL_OUTPUT=models/spam_learning
```

训练并立即演示，或单独加载模型演示：

```shell
python -m spam_learning.seed_model --input spam.txt --output models/spam_learning --text "测试文本"
python -m spam_learning.seed_model --model models/spam_learning --text "测试文本"
```

输出会显示最近的原始种子和“相似度”。默认阈值为 `0.65`，只是没有 Ham 数据时的
保守启发式值，可在训练时用 `--threshold` 修改并固化到 manifest。默认移除常见零宽
控制符；只有明确需要保留时才使用 `--keep-zero-width`。

模型包由 `manifest.json` 和内容哈希命名的 JSON artifact 构成。加载时校验 SHA-256、
格式版本、路径和结构；不使用 `pickle` 或 `joblib`，也不应加载来源不可信的模型目录。
相同语料和参数会生成相同模型版本与字节内容。Story 005 不会在 Bot 启动时加载模型，
线上建议接入属于 Story 006。

## D1 Migration

Story 002 的 Migration 位于 `migrations/001_create_samples.sql`。不需要真实凭证即可先用本地 SQLite 校验：

```shell
python -m spam_learning.d1_migrate
```

远程执行前，为 Token 配置目标账号内最小的 D1 Read 和 D1 Write 权限，并设置：

```shell
export D1_ACCOUNT_ID=...
export D1_DATABASE_ID=...
export D1_API_TOKEN=...
python -m spam_learning.d1_migrate --apply
```

远程迁移通过 Cloudflare D1 REST API 执行，不需要 Cloudflare Worker。Migration 使用
`IF NOT EXISTS`，可以重复执行；不要在命令行参数或日志中填写 Token。

# Run

## Normal

first run redis, then `python main.py`

## Docker

create `env/joingroup.env`, fill in the variables

```shell
docker-compose up -d
```

# License

Apache License Version 2.0
