# Story 005：初始模型 — 实施记录（Notes）

> 状态：已完成。
> 任务说明：[`../005-seed-model.md`](../005-seed-model.md)

## 实际完成内容
- 新增纯标准库字符 n-gram TF-IDF 相似度模型，只使用 `spam.txt` 中用户确认的 Spam 正例，不生成或伪造 Ham。
- 实现 UTF-8 种子读取：保留每条原始 Unicode，跳过空行、归一化后为空的行和完全重复原文；不构造 Pyrogram `Message`。
- 实现确定性预处理：NFKC、Latin `casefold`、可配置零宽控制符移除、空白折叠、URL/mention 标记化；不转换简繁体或替换中文字符。
- 实现带文本边界的字符 2–5 gram、sublinear TF、平滑 IDF、L2 归一化和最近正例余弦相似度。边界特征保证单字符文本也可训练和预测；少于 100 条的小数据保留全部特征，不使用会丢失稀有模式的 `min_df` 裁剪。
- 实现 `spam_like` / `unknown` / `unavailable` 预测语义。默认阈值 `0.65` 是可配置并写入 manifest 的启发式相似度阈值，不称为概率；空文本在有效模型中返回 `unknown/0.0`。
- 实现安全、可复现的 JSON 模型包：内容哈希版本号、内容哈希命名 artifact、最后原子替换 manifest、SHA-256/格式/路径/结构校验；未使用 `pickle` 或 `joblib`。
- 新增训练和演示 CLI。Story 005 未修改 `main.py`，未接入线上删除、D1、审核通知或任何管理动作。
- 新增 `make train-spam-model` 入口，并允许通过 Make 变量覆盖 Python、种子文件和模型输出目录。

## 新增或修改的文件
- `spam_learning/seed_model.py`：预处理、种子读取、TF-IDF 训练、模型保存/加载、预测器和 CLI。
- `tests/test_seed_model.py`：归一化、去重、空语料、短文本、Unicode、复现性、损坏模型、路径穿越、降级和 CLI 测试。
- `Makefile`：提供 `train-spam-model` 训练目标。
- `README.md`：增加训练、演示、阈值语义、模型安全和 Story 边界说明。
- `docs/CONTRACTS.md`：落地预测结果、字段映射、文本选择、模型格式和预处理契约。
- `docs/PROGRESS.md`、本文件：更新 Story 状态和真实交接记录。

## 接口、Schema、配置与兼容性
- `normalize_text(text, config=PreprocessConfig()) -> str`：只生成特征副本，不修改原始种子。
- `read_seed_examples(path, config) -> (examples, skipped_empty, skipped_duplicate)`：严格读取 UTF-8/UTF-8 BOM 文本并保留首个完全重复原文。
- `train_seed_model(input_path, output_dir, *, threshold=0.65, preprocess_config=..., ngram_range=(2, 5)) -> TrainingSummary`：训练并发布模型包。
- `load_seed_model(model_dir) -> SeedSimilarityModel`：严格加载本地可信 JSON 模型；错误抛出 `ModelLoadError`。
- `SeedSimilarityModel.predict(sample)` 与 `predict_text(text) -> PredictionResult`：有效模型输出 `spam_like` 或 `unknown`。
- `SimilarityPredictor.from_path(path)`：面向后续在线接入的容错加载器；缺失、损坏或版本不支持时输出 `unavailable`，`score/model_version` 为 null。
- `PredictionResult` 使用 `model_version` 遵循预测接口；Story 006 写入统一样本时需显式映射到既有 `ModelSuggestion.version`。
- 训练 CLI：`python -m spam_learning.seed_model --input spam.txt --output models/spam_learning [--text ...]`；加载演示使用 `--model`。
- Make 入口：`make train-spam-model`；可覆盖 `PYTHON`、`SPAM_SEED_INPUT`、`SPAM_MODEL_OUTPUT`，默认分别为 `python`、`spam.txt`、`models/spam_learning`。
- 现有 `SPAM_MODEL_PATH` 默认值没有改变；Story 005 不在 Bot 启动时读取它。未增加第三方依赖、环境变量、数据库 Migration 或 Docker bind mount。

## 关键技术决策及原因
- 当前只有 21 条正例且没有 Ham，无法训练或评估可靠二分类器，因此采用最近 Spam 的相似度检索；`unknown` 只表示未达到相似度阈值。
- 采用纯标准库稀疏向量和 JSON，而不是为不到 100 条数据引入原生科学计算依赖；这减少 Python 3.14、Linux ARM64/AMD64 和 macOS 的安装风险，并避免不可信反序列化代码执行。
- 使用文本首尾边界参与 n-gram，使单字符、短前后缀和完整词边界可表达，同时保持字符模型对中文和 Unicode 混淆的适应性。
- 默认阈值 `0.65` 作为保守启发式值固化到模型，不使用仅有正例的数据伪造准确率、召回率或概率校准结果。Story 007 获得人工 Ham 后才能进行真正的阈值评估或二分类选择。
- artifact 先写入并 `fsync`，manifest 最后原子替换；加载器只会看到旧的完整版本或新的完整版本。内容哈希版本避免时间戳破坏复现性。
- 模型训练保留输入顺序，因此相似度完全相同时稳定选择最早的原始样本；精确重复原文只保留首个。

## 验证记录
- `venv/bin/python -m unittest discover -v`：49 项测试全部通过，其中 Story 005 新增 8 项。
- `venv/bin/python -m spam_learning.seed_model --input spam.txt --output <临时目录>/model`：通过；实际读取 21 条可用正例，生成 `seed-sim-v1-8d54e5f7750b83a2`，无空行或完全重复原文被跳过。
- `make train-spam-model PYTHON=venv/bin/python SPAM_MODEL_OUTPUT=<临时目录>/model`：通过，生成相同模型版本。
- CLI 测试实际展示最近样本、`相似度=1.000000` 和 `decision=spam_like`，输出未使用“概率”措辞。
- `venv/bin/python -m compileall -q main.py spam_learning tests`：通过。
- `venv/bin/pip check`：通过，无损坏依赖。
- `git diff --check`：通过。
- 复现性测试在两个不同目录以相同输入和参数训练，manifest、artifact 和模型版本均字节级一致。
- 损坏 artifact、SHA-256 不一致、artifact 路径穿越、缺失模型、空语料、零宽文本和单字符中文均有自动化覆盖。
- 未连接真实 Telegram、D1 或 Redis，未执行 Docker build；Story 005 是纯离线模型，不需要这些外部服务。

## 已知问题、限制与后续事项
- 默认阈值没有 Ham 数据可用于误报率评估，只是明确记录的启发式值。不得将当前结果描述为经过验证的 Spam 分类质量。
- JSON 模型保存原始 Spam 种子以支持最近样本展示；模型目录应按训练数据对待，只打包到受控镜像，不公开分发或加载不可信来源。
- 纯 Python 线性扫描适合当前不足 100 条种子；未来数据量显著增长时，Story 007 可在保持预测契约的前提下更换索引或二分类实现。
- 当前未清理模型目录内不再由 manifest 引用的旧内容哈希 artifact，以便发布失败时保留可恢复版本；版本清理应由后续明确的部署/保留策略处理。

## 下一 Story 交接要点
- Story 006 应在启动或安全懒加载阶段创建一次 `SimilarityPredictor.from_path(config.model_path)`，不能在每条消息上重复读文件。
- 推理必须继续位于删除后的有界后台路径；无论 `spam_like`、`unknown` 还是 `unavailable` 都不能改变删除、封禁或禁言行为。
- 将 `PredictionResult` 写入样本时显式构造 `ModelSuggestion(version=result.model_version, ...)`；`unavailable` 可作为建议状态展示，但不能阻断 D1 pending 写入和人工审核。
- 通知和日志应称 `score` 为相似度；日志不得记录查询文本或 `nearest_example`。管理员通知可按既有私聊权限和长度限制展示最近样本。
- Story 007 合并 D1 数据时只能使用 `status=reviewed` 的人工标签；种子仍是 `source=manual/reviewed/spam`，不能把未审核规则命中当正例。

## Git Commit / PR
- 尚未提交。
