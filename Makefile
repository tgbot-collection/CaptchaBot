PYTHON ?= python
SPAM_SEED_INPUT ?= spam.txt
SPAM_MODEL_OUTPUT ?= models/spam_learning

.PHONY: train-spam-model
train-spam-model:
	$(PYTHON) -m spam_learning.seed_model --input "$(SPAM_SEED_INPUT)" --output "$(SPAM_MODEL_OUTPUT)"
