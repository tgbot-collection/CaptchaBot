# Story 007：重新训练 — 实施记录（Notes）

> 状态：已完成。
> 任务说明：[`../007-retrain.md`](../007-retrain.md)

## 实际完成内容
- 新增离线重训 CLI，将 D1 导出、候选构建和人工批准拆成三个明确阶段；没有自动部署或修改运行中 Bot。
- 导出只接受 D1 `reviewed/spam|ham`，与 `spam.txt` 的人工正例合并。保留原始 Unicode、标签和来源引用；相同归一化文本合并来源，冲突标签直接中止。
- 导出 JSONL 使用 canonical JSON dataset hash，并记录格式版本、Schema 兼容性、类别和来源数量。仓库忽略默认本地敏感目录 `local/`。
- Ham 为零或模板组不足时仍用所有确认 Spam 生成相似度候选；不伪造 Ham，也不宣称二分类质量。
- 新增纯标准库字符 2–5 gram TF-IDF + batch Logistic Regression。只有显式 `--classifier` 且 Spam/Ham 各至少两个模板组时训练；模板组在训练集和测试集间不交叉。
- 分组留出报告 Precision、Recall、误报数和混淆矩阵，同时明确样本门槛不是质量保证、分数未校准。
- 支持可选固定回归 JSONL，对候选与旧模型使用同一人工标注集比较；没有回归集时不生成虚假改进结论。
- 新增 `LocalPredictor`，按 manifest 类型安全加载相似度或二分类 JSON 模型。分类结果仍只进入 D1 和管理员通知，不参与删除、封禁或禁言。
- 候选目录和 release 目录均拒绝覆盖。`approve` 在复制前安全加载并核对版本，写入独立 `approval.json`；手动切换 `SPAM_MODEL_PATH` 才会发布。

## 新增或修改的文件
- `spam_learning/retrain.py`：数据集合并/校验、JSONL 导出、模板分组评估、回归比较、候选构建、批准和 CLI。
- `spam_learning/binary_model.py`：确定性稀疏 TF-IDF Logistic Regression 训练、JSON 保存/加载和预测。
- `spam_learning/model_loader.py`：按 manifest `kind` 统一加载相似度或二分类模型。
- `spam_learning/inference.py`、`spam_learning/review.py`、`spam_learning/__init__.py`：接入统一加载器，展示分类建议和未校准分数语义。
- `tests/test_retrain.py`、`tests/test_inference.py`、`tests/test_review.py`：覆盖导出边界、冲突、hash、零 Ham、分类器、批准防覆盖和在线显示。
- `Makefile`、`README.md`、`.gitignore`：增加可复现命令、发布/回滚说明并排除本地敏感导出。
- `docs/CONTRACTS.md`、`docs/PROGRESS.md`、本文件：同步接口契约、状态和交接记录。

## 接口、Schema、配置与兼容性
- CLI：`python -m spam_learning.retrain export --seed spam.txt --output local/spam-retrain.jsonl`。
- CLI：`python -m spam_learning.retrain build --dataset ... --candidates models/candidates [--classifier] [--regression ... --old-model ...]`。
- CLI：`python -m spam_learning.retrain approve --candidate ... --releases models/releases --approved-by ...`。
- `build_dataset(seed_path, reviewed) -> DatasetSummary`：合并、去重、冲突检测并计算 dataset hash。
- `build_candidate(dataset, candidates_dir, ...) -> CandidateSummary`：构建不可变候选；目录名同时含模型版本和 dataset hash 前缀。
- `approve_candidate(candidate_dir, releases_dir, approved_by=...) -> Path`：验证并复制为不可覆盖 release。
- `LocalPredictor.from_path(path)`：兼容既有 `similarity` manifest，并支持新 `logistic_regression` manifest；缺失或损坏仍降级 `unavailable`。
- 二分类输出 `decision=spam|ham`、`kind=logistic_regression`、`score=<未校准 Spam 分数>`。既有相似度输出及 `PredictionResult` 字段不变。
- 未增加环境变量、D1 Migration 或第三方运行时依赖。现有 `SPAM_MODEL_PATH` 继续指向一个模型目录。

## 关键技术决策及原因
- 采用三阶段 CLI，避免一次命令从远程数据直接覆盖部署模型；导出文件可单独审计，候选和批准也可分别复现。
- 使用归一化模板摘要分组，而不是随机逐条切分，降低相似模板在训练集和测试集间泄漏的风险；摘要不暴露用户 ID。
- 二分类器采用纯标准库确定性 full-batch 优化，避免为 Python 3.14、Linux ARM64/AMD64 和 macOS 增加原生科学计算依赖。该实现不宣称概率校准或生产质量保证。
- “两类各两个模板组”只是形成两类齐全、无组交叉留出集的最低可执行条件；报告始终保留类别不平衡和质量风险提示。
- 最终分类候选用全部人工数据训练，评估指标来自相同参数在分组训练子集上的独立留出模型，避免把测试样本用于该次指标计算。
- 批准记录与确定性 manifest 分离，避免操作者信息破坏模型字节复现；批准动作仍不会修改环境变量或重启服务。

## 验证记录
- `venv/bin/python -m unittest discover -v`：67 项测试全部通过；Story 007 新增 6 项，并扩展在线分类加载和审核显示测试。
- 已覆盖 pending 导出拒绝、Schema/标签边界、来源合并、归一化标签冲突、dataset hash 篡改、Ham 为零回退、候选防覆盖、模板组分类评估、统一安全加载和 release 防覆盖。
- `venv/bin/python -m compileall -q main.py spam_learning tests`：通过。
- `venv/bin/python -m spam_learning.d1_migrate`：本地校验通过，无需 Cloudflare 凭证。
- `venv/bin/pip check`：通过，无损坏依赖；本机 pip cache 不可写警告不影响结果。
- 使用真实 `spam.txt` 和零条 D1 记录生成 21 条 Spam 数据集，dataset hash 为 `116d63724f63aae8d73b829a9ebfcaf8c39e1003a47ff25f9f5c794d9230911b`；CLI 带 `--classifier` 正确回退相似度候选 `seed-sim-v1-bdfed518474121c9`，随后显式批准到隔离临时 release 成功。
- `git diff --check`：通过。
- 未连接真实 Cloudflare D1 或 Telegram，未执行远程 Migration、Docker build 或实际模型部署。

## 已知问题、限制与后续事项
- 当前没有项目自带的非敏感固定回归集；CLI 已定义格式，但真实回归内容必须由管理员在本地提供，不能把 Telegram 私密消息提交到 Git。
- 标准库 Logistic Regression 没有概率校准，少量数据和类别不平衡时可能严重偏置；manifest 和 UI 均明确标为未校准建议。
- 模板归一化是保守启发式，不能保证识别所有语义相似模板；评估报告不应被当作线上质量保证。
- 导出的 JSONL 含消息原文和 D1 样本 ID，必须按训练数据实施访问控制和保留/删除策略；项目仍未确定全局 pending/reviewed 数据保留期。
- 真实 D1 导出、真实 Telegram 通知和多架构容器尚未联机验证。

## 下一 Story 交接要点
- Story 008 应选择一个已存在 `approval.json` 的 release 打包进镜像，不应打包 `local/` 数据集、候选目录或未批准模型。
- 镜像内 `SPAM_MODEL_PATH` 应指向获批 release；回滚保留上一镜像/release，不在容器启动时训练或下载模型。
- 多架构验收必须分别验证 `similarity` 和 `logistic_regression` JSON 加载；两者均为纯 Python CPU 推理，无新增第三方模型依赖。
- V1 预测仍只提供建议。Story 008 不得因为新分类器输出 `spam` 而增加自动删除、封禁或禁言。

## Git Commit / PR
- 尚未提交。
