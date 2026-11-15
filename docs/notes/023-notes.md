# Story 023：二分类模型无已知特征时返回 unknown

## 实现与决策

- `spamdet/binary_model.py` 在归一化向量为空时返回 `unknown` 和空分数，保留模型版本。有有效特征时沿用既有打分。
- `spamdet/inference.py` 更新 `/model` 状态文案。`spamdet/retrain.py` 在评估中增加按人工标签区分的 `unknown_by_label`；Spam 的未知判定仍计入召回率分母。固定回归保留相似度模型原有映射。
- `tests/test_retrain.py` 覆盖未知输入、二分类回归统计和召回率。契约、Story 和进度记录同步更新。

## 验证及交接

- 123 项测试及 `git diff --check` 通过。
- 使用现有二分类候选运行 `make test-model`，确认「你好」「啊」「笑死」均返回 `unknown`，而「日入3000」仍为 `spam 0.9351`。
- 未访问 D1、修改现有候选文件或运行重训。现有 manifest 保留原评估结果，新生成的评估报告才统计未知判定。类别权重沿用现有设置。

## Git Commit / PR

尚未提交。
