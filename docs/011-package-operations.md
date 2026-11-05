# Story 011：包名与 D1 运维入口整理

## 目标

将过长的 Python 包名 `spam_learning` 统一重命名为 `spamdet`，并为开发期 D1 重建及现有模型工作流提供易发现、有说明且带安全保护的 Makefile 入口。

## 范围与验收

- Python 包目录、运行时代码、测试、Docker 构建和当前使用文档统一改为 `spamdet`；不保留 `spam_learning` 兼容包或旧 CLI 别名。
- 默认模型目录同步改为 `models/spamdet`，Docker 镜像内路径改为 `/CaptchaBot/models/spamdet`；`SPAM_MODEL_PATH` 仍可覆盖。
- 增加 `python -m spamdet.d1_reset --confirm-reset`，只执行受信任常量 SQL `DROP TABLE IF EXISTS samples`，不接受用户传入 SQL，不记录 Token。
- Makefile 增加 `reset-d1`：必须同时满足 `CONFIRM_RESET_D1=YES` 且 `CONFIRM_D1_DATABASE_ID` 与环境中的 `D1_DATABASE_ID` 完全一致；确认后先删除 `samples` 表，再执行 `python -m spamdet.d1_migrate --apply` 重建。
- `reset-d1` 任一步失败都必须以非零状态停止；不得吞掉失败或声称重建成功。本 Story 的自动化测试不得访问真实 D1，也不得实际执行远程重置。
- Makefile 为每个变量和 target 增加简体中文注释，并提供 `make help`，明确 target 的用途、前置条件和是否属于日常必需操作。
- Docker runner 安装最小的 `make`，确保 Makefile 运维入口可在实际运行容器内使用。
- README 解释每个 Make target：哪些是开发/首次建库使用，哪些只在人工重训或发布时使用，哪些不是日常必需。
- 所有现有测试、编译、本地 Migration 校验、模型确定性构建和 Docker 包路径验证通过。

## 安全边界

- `reset-d1` 会永久删除 D1 `samples` 表及其中全部 pending/reviewed 数据，只用于当前允许重建数据库的开发阶段。
- 不删除整个 D1 database，不操作其他表，不自动备份，不从 `.env` 文件隐式猜测目标。
- 操作者必须显式导出 `D1_ACCOUNT_ID`、`D1_DATABASE_ID`、`D1_API_TOKEN`，并在命令行再次提供完全一致的数据库 ID。
- 本 Story 只实现和测试入口；除非维护者另行明确要求执行，本次不得运行真实 `make reset-d1`。

## 非目标

- 不修改 Spam 判断、删除、审核、训练标签或模型特征语义。
- 不增加 D1 自动备份、定时清理、远程数据库创建/删除或 Cloudflare Worker。
- 不重写此前已完成 Story Notes 中对当时旧包名的历史记录；Story 011 Notes 负责记录最终重命名。

