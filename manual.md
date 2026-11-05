# CaptchaBot 部署手册

本文说明如何部署当前仓库中的 Telegram Captcha Bot、规则删除、D1 消息采集、管理员人工审核和建议性 Spam 模型。

## 1. 部署前须知

- 原有关键词、Bio、Sticker、emoji、姓名和 ID 规则仍是自动删除依据。
- 模型只向管理员提供建议，不会根据预测自动删除、封禁或禁言。
- 只有管理员通过 Inline Keyboard 人工确认的 `spam` / `ham` 才会用于重训。
- `SPAM_LEARNING_ENABLED` 默认关闭。首次部署建议先确认原 Bot 正常工作，再创建并迁移 D1，最后开启采集。
- D1 通过 Cloudflare REST API 访问，不需要部署 Cloudflare Worker。
- D1 和 Telegram 都是外部服务。正式启用前应在测试群验证删除、写入和回调权限。

## 2. 准备 Telegram Bot

### 2.1 创建 Bot 并取得 `BOT_TOKEN`

1. 在 Telegram 中打开官方 [@BotFather](https://t.me/BotFather)。
2. 发送 `/newbot`，按提示设置名称和以 `bot` 结尾的 username。
3. 保存 BotFather 返回的 Token，作为 `BOT_TOKEN`。
4. 不要把 Token 提交到 Git、写入 Dockerfile 或发送到聊天记录。Token 泄漏时，在 BotFather 中使用 `/token` 重新生成。

### 2.2 取得 `API_ID` 和 `API_HASH`

1. 使用 Bot 所属的 Telegram 账号登录 [my.telegram.org](https://my.telegram.org)。
2. 打开 **API development tools**。
3. 创建一个 Telegram Application。
4. 保存页面显示的 `api_id` 和 `api_hash`，分别配置为 `API_ID` 和 `API_HASH`。

`API_HASH` 应按密钥管理，不要提交到仓库。

### 2.3 允许 Bot 接收群消息

要使用“全部群消息”或“指定群全部消息”采集，Bot 必须能收到普通群消息：

1. 在 BotFather 中选择该 Bot。
2. 将 Bot 添加到目标群组并设为管理员。Telegram 的管理员 Bot 即使开启 Privacy Mode，也能收到群内用户发送的所有消息。
3. 至少授予删除消息权限。验证码的限制、解禁和封禁流程还需要相应的限制成员/封禁成员权限。
4. 只有不把 Bot 设为管理员时，才需要在 **Bot Settings → Group Privacy** 中关闭 Privacy Mode；也可以使用 `/setprivacy` 后选择 **Disable**。修改 Privacy Mode 后，Telegram 可能要求将 Bot 从群中移除再重新添加才能生效。

本项目需要删除、限制或封禁权限，正常部署时本来就应将 Bot 设为管理员，因此通常不需要关闭 Privacy Mode。普通 Bot 默认仍不能看到其他 Bot 发送的消息；如确实需要 Bot-to-Bot 消息，必须另行评估并启用 Telegram 的 Bot-to-Bot Communication Mode。

### 2.4 准备唯一审核管理员

1. 确认唯一审核管理员的 Telegram 数字用户 ID，将其配置为 `SPAM_ADMIN_ID`。
2. 管理员必须先私聊该 Bot 并发送 `/start`。Telegram Bot 通常不能主动向从未私聊过它的用户发消息。
3. `SPAM_ADMIN_ID` 必须是正整数，不是 username，也不能配置多个 ID。

## 3. 创建 Cloudflare D1

### 3.1 通过 Dashboard 创建

1. 登录 [Cloudflare Dashboard](https://dash.cloudflare.com)。
2. 进入 **Storage & Databases → D1 SQL Database**。Cloudflare 页面名称以后可能略有变化，也可以直接在 Dashboard 搜索 `D1`。
3. 点击 **Create Database**。
4. 输入数据库名称，例如 `captchabot-spam-prod`。
5. 可选设置 location hint，然后创建数据库。
6. 打开新数据库，记录其 UUID，作为 `D1_DATABASE_ID`。

也可以使用 Wrangler 创建：

```shell
npx wrangler@latest d1 create captchabot-spam-prod
```

命令输出中的 `database_id` 就是 `D1_DATABASE_ID`。本项目不需要 Worker binding，也不需要创建 Worker。

### 3.2 取得 `D1_ACCOUNT_ID`

`D1_ACCOUNT_ID` 是 Cloudflare Account ID，不是登录邮箱、Zone ID 或数据库 ID。可以在 Cloudflare Dashboard 的账号概览或开发者/API 页面查看。D1 API 请求路径同时需要 Account ID 和 Database ID。

## 4. 创建 Cloudflare API Token

本项目应使用 API Token，不要使用 Global API Key。

1. 在 Cloudflare Dashboard 打开 **My Profile → API Tokens**。
2. 点击 **Create Token**，选择 **Create Custom Token**。
3. 建议命名为 `captchabot-d1-prod`。
4. 配置账号级 D1 权限：
   - `Account` → `D1` → `Read`；
   - `Account` → `D1` → `Edit` 或 `Write`。
5. 在 **Account Resources** 中选择 **Include → Specific account → 你的目标账号**，不要授权所有不相关账号。
6. 如服务器出口 IP 固定，可选配置 Client IP Address Filtering。
7. 创建 Token 后立即复制并安全保存。Token secret 通常只显示一次。
8. 将 Token 配置为 `D1_API_TOKEN`。

Bot 运行时既需要写入 pending、审核结果和通知位置，也需要读取统计、待审核记录和训练数据，因此必须同时具备所需的 D1 读写能力。不要额外授予 DNS、Workers、域名或账号管理权限。

## 5. 所有环境变量

### 5.1 Bot 基础配置

| 环境变量 | 必填 | 默认值 | 说明 |
|---|---:|---|---|
| `API_ID` | 是 | 无 | 从 `my.telegram.org` 获取的 Telegram Application API ID。 |
| `API_HASH` | 是 | 无 | 与 `API_ID` 配套的 Telegram API Hash，按密钥保存。 |
| `BOT_TOKEN` | 是 | 无 | BotFather 生成的 Bot Token。 |
| `REDIS` | 否 | `localhost` | Redis hostname 或 IP。Compose 内应使用服务名 `redis`。当前代码不支持在此变量中附带端口、密码或 URL。 |
| `MODE` | 否 | 无 | 只有值严格等于 `dev` 时，封禁 5 秒后会自动解封。生产环境不要设置为 `dev`，建议省略或设为 `prod`。 |

### 5.2 原有规则配置

以下变量都是可选的逗号分隔列表。空值表示不配置该类黑名单。

| 环境变量 | 说明 | 示例 |
|---|---|---|
| `BLACKLIST_MESSAGE` | 消息正文或 caption 关键词；比较时忽略大小写，并转换为简体中文后做包含匹配。 | `收米,日入过万` |
| `BLACKLIST_NAME` | 用户 username、first name、last name，以及部分转发频道名称关键词。 | `客服,官方招聘` |
| `BLACKLIST_ID` | Telegram 用户或转发来源数字 ID。 | `123456789,987654321` |
| `BLACKLIST_EMOJI` | Premium emoji 对应的 Sticker Set 名称。 | `EmojiSetA,EmojiSetB` |
| `BLACKLIST_STICKER` | Sticker Set 的 `set_name`。 | `spam_pack_1,spam_pack_2` |

配置解析不支持转义逗号；关键词本身包含逗号时不能直接用该格式表达。

### 5.3 D1、审核和模型配置

| 环境变量 | 开启 Spam 学习时是否必填 | 默认值 | 说明 |
|---|---:|---|---|
| `SPAM_LEARNING_ENABLED` | 是 | `false` | 总开关。接受 `true/false`、`1/0`、`yes/no`、`on/off`。 |
| `SPAM_ADMIN_ID` | 是 | 无 | 唯一审核管理员的 Telegram 数字用户 ID，必须是正整数。 |
| `D1_ACCOUNT_ID` | 是 | 无 | Cloudflare Account ID。 |
| `D1_DATABASE_ID` | 是 | 无 | D1 Database UUID。 |
| `D1_API_TOKEN` | 是 | 无 | 具有目标账号 D1 读写权限的最小权限 API Token。 |
| `SPAM_MODEL_PATH` | 否 | `models/spam_learning` | 本地可信模型目录。正式镜像内默认是 `/CaptchaBot/models/spam_learning`。 |

### 5.4 采集范围配置

| `SPAM_CAPTURE_GROUPS` | 实际行为 |
|---|---|
| 未设置或空值 | 不采集普通消息；仍采集所有群中被现有规则自动删除的消息。 |
| `all` | 采集 Bot 可见的所有群中的普通文本/caption，并采集所有规则删除消息。 |
| `-100123,-100456` | 只采集所列群的普通文本/caption；其他群的规则删除消息仍会采集。 |

群 ID 必须是负整数。普通消息仅限未命中规则且具有非空 text 或 caption 的消息；纯媒体和 service message 当前不采集。队列满时允许丢样，以保证原有删除路径不被 D1 拖慢。

### 5.5 性能和超时配置

通常先使用默认值，观察日志和 D1 写入量后再调整。所有值都必须大于零。

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `SPAM_CAPTURE_QUEUE_SIZE` | `100` | 内存采集队列容量；满时丢弃新采集任务。 |
| `SPAM_CAPTURE_WORKERS` | `2` | 后台采集 worker 数量。 |
| `SPAM_INFERENCE_CONCURRENCY` | `2` | 本地模型推理最大并发数。 |
| `SPAM_BIO_TIMEOUT_SECONDS` | `2.0` | 后台查询发送者 Bio 的单次超时秒数。 |
| `SPAM_BIO_CACHE_TTL_SECONDS` | `300.0` | 进程内 Bio 缓存 TTL。 |
| `SPAM_BIO_CACHE_SIZE` | `1000` | 进程内 Bio 缓存最大条目数。 |
| `SPAM_CAPTURE_SHUTDOWN_TIMEOUT_SECONDS` | `5.0` | 停止进程时等待队列排空的最长秒数。 |

## 6. 环境文件示例

仓库的 `docker-compose.yml` 会读取 `env/captcha.env`。`env/` 已被 Git 忽略，不要强制提交该文件。

```dotenv
# Telegram
API_ID=12345678
API_HASH=replace_with_telegram_api_hash
BOT_TOKEN=123456789:replace_with_bot_token

# Compose 中 Redis 服务名
REDIS=redis
MODE=prod

# 原有规则；不用的变量可以留空
BLACKLIST_MESSAGE=收米,日入过万
BLACKLIST_NAME=
BLACKLIST_ID=
BLACKLIST_EMOJI=
BLACKLIST_STICKER=

# Spam 学习与 D1
SPAM_LEARNING_ENABLED=true
SPAM_ADMIN_ID=123456789
D1_ACCOUNT_ID=replace_with_cloudflare_account_id
D1_DATABASE_ID=replace_with_d1_database_uuid
D1_API_TOKEN=replace_with_minimum_scope_api_token

# 正式镜像内置模型路径
SPAM_MODEL_PATH=/CaptchaBot/models/spam_learning

# 留空表示只采集所有群中被规则删除的消息
# all 表示采集所有可见群的普通文本/caption
# 或填写 -1001234567890,-1009876543210
SPAM_CAPTURE_GROUPS=

# 可选调优
SPAM_CAPTURE_QUEUE_SIZE=100
SPAM_CAPTURE_WORKERS=2
SPAM_INFERENCE_CONCURRENCY=2
SPAM_BIO_TIMEOUT_SECONDS=2.0
SPAM_BIO_CACHE_TTL_SECONDS=300.0
SPAM_BIO_CACHE_SIZE=1000
SPAM_CAPTURE_SHUTDOWN_TIMEOUT_SECONDS=5.0
```

建议限制文件权限：

```shell
chmod 600 env/captcha.env
```

## 7. 初始化 D1 Schema

必须在开启 Spam 学习前执行 `migrations/001_create_samples.sql`。这是尚未部署项目的最终建表 Schema，直接支持 `rule`、`manual` 和 `group` 三种来源；D1 最终只有一个业务表 `samples`。

### 7.1 本地校验 Migration

在仓库根目录执行：

```shell
venv/bin/python -m spam_learning.d1_migrate
```

成功时输出：

```text
D1 Migration 本地校验通过
```

此步骤只使用内存 SQLite，不会连接 Cloudflare。

### 7.2 对远程 D1 执行 Migration

使用本地虚拟环境：

```shell
set -a
source env/captcha.env
set +a
venv/bin/python -m spam_learning.d1_migrate --apply
```

或者使用已经构建的容器镜像：

```shell
docker run --rm \
  --env-file env/captcha.env \
  captchabot:local \
  python -m spam_learning.d1_migrate --apply
```

Migration 成功后，在 D1 Console 执行：

```sql
SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name;
PRAGMA table_info(samples);
SELECT COUNT(*) AS total FROM samples;
```

应该能看到 `samples` 表。对于已有生产数据库，执行迁移前应先备份或导出；不要在迁移未确认成功时开启 `SPAM_CAPTURE_GROUPS`。

## 8. Docker 部署

### 8.1 构建当前仓库代码

当前 `docker-compose.yml` 中的镜像是 `bennythink/captchabot`，它会拉取公共镜像，不会自动构建当前工作区。要部署你当前审查过的代码，先构建自己的镜像：

```shell
docker build -t captchabot:local .
```

然后将 `docker-compose.yml` 中 captcha 服务的镜像临时改为：

```yaml
image: captchabot:local
```

也可以把镜像推送到自己的私有 registry，再在服务器 Compose 中引用固定版本标签。生产环境不要只使用会漂移的 `latest` 标签。

### 8.2 推荐首次上线顺序

1. 创建 `env/captcha.env`，暂时设置 `SPAM_LEARNING_ENABLED=false`。
2. 启动 Redis 和 Bot，确认原验证码及规则删除功能正常。
3. 创建 D1 和最小权限 API Token。
4. 使用同一环境文件运行远程 Migration。
5. 管理员私聊 Bot 发送 `/start`。
6. 设置 `SPAM_LEARNING_ENABLED=true`。
7. 选择 `SPAM_CAPTURE_GROUPS` 模式。
8. 重启 Bot。

启动及查看日志：

```shell
docker compose up -d
docker compose ps
docker compose logs -f captcha
```

修改环境变量后必须重建容器，仅 `restart` 不一定重新读取 env file：

```shell
docker compose up -d --force-recreate captcha
```

## 9. 上线验证

建议使用单独测试群完成以下检查：

1. **原规则删除**：发送命中 `BLACKLIST_MESSAGE` 的测试消息，确认消息立即删除。
2. **D1 pending**：在 D1 Console 查询最新记录：

   ```sql
   SELECT id, source, status, label, prediction, created_at
   FROM samples
   ORDER BY id DESC
   LIMIT 20;
   ```

3. **管理员通知**：确认规则删除消息只有在 D1 写入成功后才私聊管理员。
4. **人工标签**：点击 `✅ Spam` 或 `❌ Not Spam`，再查询：

   ```sql
   SELECT id, status, label, reviewed_at, reviewed_by
   FROM samples
   ORDER BY id DESC
   LIMIT 20;
   ```

5. **管理员命令**：
   - `/dataset`：查看 total、pending、reviewed、spam、ham；
   - `/model`：查看模型类型、版本和可用性；
   - `/review`：领取一条普通群消息进行人工审核。
6. **普通消息采集**：只在设置了 `SPAM_CAPTURE_GROUPS=all` 或指定群 ID 时验证。普通消息不会主动轰炸管理员，需要使用 `/review` 领取。
7. **权限失败**：使用非管理员账号尝试命令和按钮，确认无法读取或写入审核状态。

## 10. 日常运维

### 10.1 查看未审核数量

```sql
SELECT source, COUNT(*) AS count
FROM samples
WHERE status = 'pending'
GROUP BY source;
```

查找未记录通知位置的规则样本：

```sql
SELECT id, created_at
FROM samples
WHERE source = 'rule'
  AND status = 'pending'
  AND review_message_id IS NULL
ORDER BY id ASC;
```

V1 不会自动补发此类通知。不要因为规则命中就直接把样本批量标为 Spam。

### 10.2 紧急关闭采集

将环境文件改为：

```dotenv
SPAM_LEARNING_ENABLED=false
```

然后重新创建 Bot 容器。原有验证码和规则删除仍会继续工作，D1、模型和审核服务不会启动。

如果只想停止普通消息采集、继续审核规则删除消息，将 `SPAM_CAPTURE_GROUPS` 清空并重建容器。

### 10.3 D1 数据保留

当前程序不自动清理 D1。删除前应先确认不会破坏审核、重训来源或审计要求。建议制定 pending 与 reviewed 的保留期限，并在删除前导出备份。

## 11. 从 D1 导出和重训

以下命令只导出 `status=reviewed` 且标签为 `spam`/`ham` 的人工审核记录，并与 `spam.txt` 合并：

```shell
set -a
source env/captcha.env
set +a
venv/bin/python -m spam_learning.retrain export \
  --seed spam.txt \
  --output local/spam-retrain.jsonl
```

只构建相似度候选：

```shell
venv/bin/python -m spam_learning.retrain build \
  --dataset local/spam-retrain.jsonl \
  --candidates models/candidates
```

当真实 Spam/Ham 各自至少能形成两个模板组时，可以显式请求二分类候选：

```shell
venv/bin/python -m spam_learning.retrain build \
  --dataset local/spam-retrain.jsonl \
  --candidates models/candidates \
  --classifier
```

检查候选 manifest、数据量、类别分布和评估结果后，人工批准：

```shell
venv/bin/python -m spam_learning.retrain approve \
  --candidate models/candidates/<candidate-directory> \
  --releases models/releases \
  --approved-by <operator-name>
```

批准不会自动部署。要把 release 打进镜像：

1. 确认 `models/releases/<model-version>` 位于 Docker build context 中。
2. 重新构建镜像。
3. 将 `SPAM_MODEL_PATH` 设置为容器内的 `/CaptchaBot/models/releases/<model-version>`。
4. 重建 Bot 容器。
5. 用管理员 `/model` 确认实际加载的版本。

回滚时将 `SPAM_MODEL_PATH` 改回上一个 release 或镜像内置的 `/CaptchaBot/models/spam_learning`，然后重建容器。不要在运行中的容器里直接覆盖模型目录。

## 12. 常见故障

### 启动时报缺少 Spam 配置

当 `SPAM_LEARNING_ENABLED=true` 时，以下变量缺一不可：

- `SPAM_ADMIN_ID`
- `D1_ACCOUNT_ID`
- `D1_DATABASE_ID`
- `D1_API_TOKEN`

### Bot 看不到普通群消息

- 检查 Bot 是否仍是群管理员；管理员 Bot 无需关闭 Privacy Mode；
- 如果 Bot 不是管理员，检查 BotFather 的 Group Privacy 是否已关闭，并在修改后将 Bot 重新加入群；
- 检查 Bot 是否已加入目标群；
- 检查 `SPAM_CAPTURE_GROUPS` 是否为 `all` 或包含正确的负数群 ID；
- 检查消息是否只有纯媒体而没有 caption；
- 检查采集队列是否已满。

### 自动删除后没有管理员通知

- 确认管理员已经私聊 Bot 并发送 `/start`；
- 确认 `SPAM_ADMIN_ID` 是正确的数字用户 ID；
- 先在 D1 查询是否已经生成 pending；
- 如果 D1 没有记录，检查 Token 权限、Account ID、Database ID 和 Migration；
- 如果 D1 有记录但 `review_message_id` 为 NULL，说明通知或通知位置写入失败。

### D1 返回权限错误

- 不要把 Zone ID 当作 Account ID；
- 确认 Token 的 Account Resources 包含正确账号；
- 确认 Token 具有 D1 Read 和 D1 Edit/Write；
- Token 重新生成后要更新环境文件并重建容器。

### 模型显示 unavailable

- 检查 `SPAM_MODEL_PATH` 是否是容器内路径；
- 检查目录内是否同时包含 `manifest.json` 和 manifest 引用的 JSON artifact；
- 不要加载来源不可信或手工修改过的模型文件；
- 即使模型不可用，规则删除、D1 pending 和人工审核仍应继续工作。

## 13. 官方参考资料

- [Telegram：创建 Application 并取得 API ID/API Hash](https://core.telegram.org/api/obtaining_api_id)
- [Telegram：通过 BotFather 创建和管理 Bot](https://core.telegram.org/bots/features#botfather)
- [Cloudflare D1：创建数据库](https://developers.cloudflare.com/d1/get-started/)
- [Cloudflare：创建 API Token](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/)
- [Cloudflare：API Token 权限列表](https://developers.cloudflare.com/fundamentals/api/reference/permissions/)
- [Cloudflare D1 Query REST API](https://developers.cloudflare.com/api/resources/d1/subresources/database/methods/query/)
- [Docker Compose：使用 env_file](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/)

