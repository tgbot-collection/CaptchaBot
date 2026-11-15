# 默认使用系统 Python；本地开发通常覆盖为 PYTHON=venv/bin/python。
PYTHON ?= python
# 人工确认的初始 Spam 正例，每行一条。
SPAM_SEED_INPUT ?= spam.txt
# D1 reviewed 数据与种子合并后的本地敏感数据集。
SPAM_RETRAIN_DATASET ?= local/spam-retrain.jsonl
# 尚未批准的候选模型目录。
SPAM_CANDIDATES ?= models/candidates
# 人工批准后的不可覆盖 release 目录。
SPAM_RELEASES ?= models/releases
# 设为 1 时尝试构建二分类候选。
CLASSIFIER ?= 0

.DEFAULT_GOAL := help
.PHONY: help retrain-spam-model approve-spam-candidate reset-d1

# 显示可用 target；这是默认 target，不修改任何文件或远程状态。
help:
	@echo "make retrain-spam-model     从 D1 导出已审核数据并构建候选，CLASSIFIER=1 尝试二分类"
	@echo "make approve-spam-candidate 人工批准候选，需要 CANDIDATE 和 APPROVED_BY"
	@echo "make spam id=1,2,3       将 D1 样本批量人工标注为 Spam"
	@echo "make ham id=1,2,3        将 D1 样本批量人工标注为 Ham"
	@echo "make import-spam-file file=message.txt 从 UTF-8 文本文件补录一条人工确认的 Spam"
	@echo "make reset-d1               永久删除并重建 D1 samples 表，需要双重确认"

# 导出成功后才构建候选；本地数据集包含敏感原文。
retrain-spam-model:
	$(PYTHON) -m spamdet.retrain export --seed "$(SPAM_SEED_INPUT)" --output "$(SPAM_RETRAIN_DATASET)"
	$(PYTHON) -m spamdet.retrain build --dataset "$(SPAM_RETRAIN_DATASET)" --candidates "$(SPAM_CANDIDATES)" $(if $(filter 1,$(CLASSIFIER)),--classifier)

# 检查候选后明确指定候选目录和批准人。
approve-spam-candidate:
	@test -n "$(CANDIDATE)" || (echo "必须提供 CANDIDATE=候选目录"; exit 2)
	@test -n "$(APPROVED_BY)" || (echo "必须提供 APPROVED_BY=批准人"; exit 2)
	$(PYTHON) -m spamdet.retrain approve --candidate "$(CANDIDATE)" --releases "$(SPAM_RELEASES)" --approved-by "$(APPROVED_BY)"

# 仅限允许丢弃全部样本的开发阶段。先删表，成功后立即执行最终 Migration 重建。
# 示例：make reset-d1 CONFIRM_RESET_D1=YES
reset-d1:
	@test "$(CONFIRM_RESET_D1)" = "YES" || (echo "拒绝重置：必须提供 CONFIRM_RESET_D1=YES"; exit 2)
	$(PYTHON) -m spamdet.d1_reset --confirm-reset
	$(PYTHON) -m spamdet.d1_migrate --apply
