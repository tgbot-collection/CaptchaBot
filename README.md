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
* `SPAM_ADMIN_IDS`：逗号分隔的 Telegram 管理员用户 ID
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

启用 `SPAM_LEARNING_ENABLED` 时，管理员 ID 和三项 D1 配置必须存在。
所有密钥只能通过环境变量或 Secret 注入。
规则删除前只做同步内存快照；Bio 和 D1 请求在有界后台队列中执行。队列已满时丢弃采集任务，不影响删除。

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
