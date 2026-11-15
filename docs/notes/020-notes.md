# Story 020：合并人工重训 Make 入口

## 实现与决策

- `Makefile` 将三个旧训练 Make 目标替换为 `retrain-spam-model`。新目标顺序调用 `spamdet.retrain export` 与 `build`，共用 `SPAM_RETRAIN_DATASET`。默认构建相似度候选，`CLASSIFIER=1` 传入 `--classifier`。批准仍通过独立目标执行。
- `Dockerfile` 直接调用 `spamdet.seed_model`，以仓库 `spam.txt` 生成内置模型。沿用原输入、镜像内路径和生成参数，构建期间无需 D1 凭证。
- `README.md`、`manual.md`、`docs/CONTRACTS.md`、`docs/020-retrain-make.md`、`docs/PROGRESS.md` 更新使用说明、契约和验收记录。

## 实际验证

- 用记录参数的替身验证 Make 在 `-j4` 下按顺序导出与构建、两步共用数据集路径、`CLASSIFIER=1` 正确传参、导出失败时停止，以及旧目标已移除。
- 121 项自动化测试全部通过，`git diff --check` 通过。
- 构建了 Linux arm64 完整镜像，在禁网容器中通过 `LocalPredictor` 成功加载内置模型 `seed-sim-v2-aa3f115430e7bb8e`。

## 限制与交接

- 未访问真实 D1，未实跑真实数据重训，未构建 Linux amd64 镜像。运行新目标前需要载入现有 D1 环境变量。候选目录由命令打印，评估后再显式批准。

## Git Commit / PR

尚未提交。
