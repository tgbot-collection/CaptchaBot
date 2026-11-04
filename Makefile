PYTHON ?= python
SPAM_SEED_INPUT ?= spam.txt
SPAM_MODEL_OUTPUT ?= models/spam_learning
SPAM_RETRAIN_DATASET ?= local/spam-retrain.jsonl
SPAM_CANDIDATES ?= models/candidates
SPAM_RELEASES ?= models/releases

.PHONY: train-spam-model export-spam-dataset build-spam-candidate approve-spam-candidate
train-spam-model:
	$(PYTHON) -m spam_learning.seed_model --input "$(SPAM_SEED_INPUT)" --output "$(SPAM_MODEL_OUTPUT)"

export-spam-dataset:
	$(PYTHON) -m spam_learning.retrain export --seed "$(SPAM_SEED_INPUT)" --output "$(SPAM_RETRAIN_DATASET)"

build-spam-candidate:
	$(PYTHON) -m spam_learning.retrain build --dataset "$(SPAM_RETRAIN_DATASET)" --candidates "$(SPAM_CANDIDATES)"

approve-spam-candidate:
	@test -n "$(CANDIDATE)" || (echo "必须提供 CANDIDATE=候选目录"; exit 2)
	@test -n "$(APPROVED_BY)" || (echo "必须提供 APPROVED_BY=批准人"; exit 2)
	$(PYTHON) -m spam_learning.retrain approve --candidate "$(CANDIDATE)" --releases "$(SPAM_RELEASES)" --approved-by "$(APPROVED_BY)"
