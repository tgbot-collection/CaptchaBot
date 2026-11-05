# Story 011：包名与 D1 运维入口整理 — 实施记录（Notes）

> 状态：已完成。任务说明：[`../011-package-operations.md`](../011-package-operations.md)

## 实际完成内容

- 将 Python 包目录从 `spam_learning` 统一重命名为 `spamdet`，更新运行时代码、测试、CLI、Docker 和当前使用文档；不保留旧包兼容层。
- 默认模型目录从 `models/spam_learning` 改为 `models/spamdet`，Docker 镜像内默认路径改为 `/CaptchaBot/models/spamdet`。
- 新增 `spamdet.d1_reset`：只有显式 `--confirm-reset` 才运行，且只执行代码常量 `DROP TABLE IF EXISTS samples`，不接受外部 SQL。
- Makefile 新增带双重确认的 `reset-d1`。它要求 `CONFIRM_RESET_D1=YES`，并要求 `CONFIRM_D1_DATABASE_ID` 与环境中的 `D1_DATABASE_ID` 完全一致；删表成功后再运行 `python -m spamdet.d1_migrate --apply`。
- Makefile 为所有变量和 target 增加中文注释，并把 `help` 设为默认 target；README 增加每个 target 的用途、前置条件和是否日常必需的说明。
- 未改变关键词删除、采集、审核、训练标签或模型特征逻辑，未执行真实远程 D1 重置。

## 修改文件与接口

- `spamdet/`：由原包整体重命名；相对模块接口不变。
- `spamdet/d1_reset.py`：固定 SQL 的开发期删表 CLI，入口为 `python -m spamdet.d1_reset --confirm-reset`。
- `models/spamdet/`：默认内置模型目录的新名称。
- `main.py`、`tests/`：导入路径统一切换为 `spamdet`。
- `spamdet/config.py`：默认 `SPAM_MODEL_PATH` 改为 `models/spamdet`。
- `Dockerfile`：复制新包，镜像默认模型路径改为 `/CaptchaBot/models/spamdet`。
- `Makefile`：变量/target 中文注释、默认 `help`、安全 `reset-d1` 和全部新 CLI 路径。
- `tests/test_d1_reset.py`：固定 SQL 与强制确认测试。
- `README.md`、`docs/000-overview.md`、`docs/CONTRACTS.md`、`docs/PROGRESS.md`、当前 Story 和本 Notes：同步最终命令与运维边界。
- 以前完成的 Story/Notes 保留当时的旧包名，作为历史记录，不做追溯性改写。

## Make target 说明与必要性

- `help`：默认只读入口，任何时候可用，不是运行前置步骤。
- `train-spam-model`：只有 `spam.txt` 或模型实现变化后需要；Docker 构建会自动调用，日常启动 Bot 不需要手工运行。
- `export-spam-dataset`：人工重训第一步，需要 D1 凭证，会生成含敏感原文的本地文件；平时不需要。
- `build-spam-candidate`：人工重训第二步，只构建未批准候选；平时不需要。
- `approve-spam-candidate`：人工重训第三步，需要明确候选和批准人，仍不自动部署；平时不需要。
- `reset-d1`：仅限当前允许丢弃全部开发样本时使用；正常运行、重训、发布均不需要。

## 关键技术决策

- 不增加旧包 alias，避免两个包名长期并存、CLI 文档分叉和 Docker 重复复制。当前项目允许直接重建，因此一次性切换更清晰。
- `reset-d1` 只删除 `samples` 表，不删除 D1 database 或其他表；索引随表删除，最终 Migration 负责重建表和索引。
- Make 层同时要求固定确认词和数据库 ID 复述，Python 层再要求 `--confirm-reset`，防止误触 target 或直接误用 CLI。
- 删表和建表保留为两个严格顺序的进程。任一步失败时 Make 立即停止，不吞错、不输出虚假成功；但删表成功后建表失败仍可能暂时没有 `samples` 表，因此命令只能在停采集的维护窗口运行。
- 未增加交互式确认，保证 CI/SSH 下行为确定；安全性由显式参数和目标 ID 匹配提供。

## 实际验证

- `venv/bin/python -m unittest discover -q`：78 项测试全部通过。
- `tests/test_d1_reset.py` 验证只执行固定 `DROP TABLE IF EXISTS samples`，且缺少 `--confirm-reset` 时退出码为 2。
- `make help`：成功显示五个 target 的用途。
- `make reset-d1`：在缺少 `CONFIRM_RESET_D1=YES` 时于网络调用前退出码 2。
- `make reset-d1 CONFIRM_RESET_D1=YES D1_DATABASE_ID=expected CONFIRM_D1_DATABASE_ID=wrong`：数据库 ID 不匹配时于网络调用前退出码 2。
- `make train-spam-model PYTHON=venv/bin/python SPAM_MODEL_OUTPUT=<临时目录>/model`：通过新 `spamdet` CLI 生成确定性模型 `seed-sim-v2-aa3f115430e7bb8e`。
- `venv/bin/python -m compileall -q main.py spamdet tests`：通过。
- `venv/bin/python -m spamdet.d1_migrate`：本地 Migration 校验通过。
- `venv/bin/pip check`：通过；pip cache 权限警告不影响结果。
- `docker buildx build --platform linux/arm64 --load -t captchabot:story011 .`：通过。
- 容器内从 `spamdet.model_loader` 加载 `/CaptchaBot/models/spamdet`：`available=True`，版本为 `seed-sim-v2-aa3f115430e7bb8e`。
- `git diff --check`：通过。

## 已知问题与交接

- 未执行真实 `make reset-d1`，也未连接 Cloudflare D1；远程 Token 权限、删表和重建响应仍需维护窗口中验证。
- `reset-d1` 不自动备份且不可恢复。删表成功、Migration 失败时需要修复凭证或网络后再次执行 `python -m spamdet.d1_migrate --apply`。
- 已有部署若显式设置旧 `SPAM_MODEL_PATH=models/spam_learning`，升级时必须改为现存路径或移除覆盖以使用新默认值。
- 已批准 release 仍位于 `models/releases`，不受默认初始模型目录重命名影响。
- `python -m spamdet.seed_model` 仍会输出既有 `runpy` RuntimeWarning；模型构建、hash 和加载结果正确，本 Story 未扩展范围重构包级导入。
- 历史 Story 和 Notes 中的 `spam_learning` 是当时实现记录，不代表当前可用包名。

## Git Commit / PR

- 尚未提交。

