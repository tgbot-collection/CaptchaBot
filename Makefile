# 默认使用系统 Python；本地开发通常覆盖为 PYTHON=venv/bin/python。
PYTHON ?= python
# 人工确认的初始 Spam 正例，每行一条。
SPAM_SEED_INPUT ?= spam.txt
# 初始模型输出目录；Docker 构建也会使用该训练入口。
SPAM_MODEL_OUTPUT ?= models/spamdet
# D1 reviewed 数据与种子合并后的本地敏感数据集。
SPAM_RETRAIN_DATASET ?= local/spam-retrain.jsonl
# 尚未批准的候选模型目录。
SPAM_CANDIDATES ?= models/candidates
# 人工批准后的不可覆盖 release 目录。
SPAM_RELEASES ?= models/releases

.DEFAULT_GOAL := help
.PHONY: help train-spam-model export-spam-dataset build-spam-candidate approve-spam-candidate reset-d1

# 显示可用 target；这是默认 target，不修改任何文件或远程状态。
help:
	@echo "make train-spam-model       从 spam.txt 重建初始相似度模型"
	@echo "make export-spam-dataset    从 D1 导出 reviewed 数据并合并种子"
	@echo "make build-spam-candidate   从本地数据集构建未批准候选模型"
	@echo "make approve-spam-candidate 人工批准候选，需要 CANDIDATE 和 APPROVED_BY"
	@echo "make reset-d1               永久删除并重建 D1 samples 表，需要双重确认"

# 仅在 spam.txt 或模型代码变化后使用；日常运行 Bot 不需要执行。
train-spam-model:
	$(PYTHON) -m spamdet.seed_model --input "$(SPAM_SEED_INPUT)" --output "$(SPAM_MODEL_OUTPUT)"

# 人工重训的第一步；需要有效 D1 凭证，导出文件含敏感原文。
export-spam-dataset:
	$(PYTHON) -m spamdet.retrain export --seed "$(SPAM_SEED_INPUT)" --output "$(SPAM_RETRAIN_DATASET)"

# 人工重训的第二步；只生成候选，不批准、不部署。
build-spam-candidate:
	$(PYTHON) -m spamdet.retrain build --dataset "$(SPAM_RETRAIN_DATASET)" --candidates "$(SPAM_CANDIDATES)"

# 人工重训的第三步；必须明确候选目录和批准人，仍不会自动部署。
approve-spam-candidate:
	@test -n "$(CANDIDATE)" || (echo "必须提供 CANDIDATE=候选目录"; exit 2)
	@test -n "$(APPROVED_BY)" || (echo "必须提供 APPROVED_BY=批准人"; exit 2)
	$(PYTHON) -m spamdet.retrain approve --candidate "$(CANDIDATE)" --releases "$(SPAM_RELEASES)" --approved-by "$(APPROVED_BY)"

# 仅限允许丢弃全部样本的开发阶段。先删表，成功后立即执行最终 Migration 重建。
# 示例：make reset-d1 CONFIRM_RESET_D1=YES CONFIRM_D1_DATABASE_ID="$$D1_DATABASE_ID"
reset-d1:
	@test "$(CONFIRM_RESET_D1)" = "YES" || (echo "拒绝重置：必须提供 CONFIRM_RESET_D1=YES"; exit 2)
	@test -n "$(D1_DATABASE_ID)" || (echo "拒绝重置：环境中缺少 D1_DATABASE_ID"; exit 2)
	@test "$(CONFIRM_D1_DATABASE_ID)" = "$(D1_DATABASE_ID)" || (echo "拒绝重置：CONFIRM_D1_DATABASE_ID 与目标不一致"; exit 2)
	$(PYTHON) -m spamdet.d1_reset --confirm-reset
	$(PYTHON) -m spamdet.d1_migrate --apply
