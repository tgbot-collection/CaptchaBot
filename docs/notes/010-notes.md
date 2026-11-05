# Story 010：多字段 Spam 特征 — 实施记录（Notes）

> 状态：已完成。任务说明：[`../010-profile-features.md`](../010-profile-features.md)

## 实际完成内容

- 统一样本升级为 `schema_version=2`，`UserSnapshot` 增加严格校验的 `bio_state`：`present`、`empty`、`unavailable`、`not_requested`。
- 新增训练和在线推理共用的多字段特征层。模型只使用 message/caption、Bio、display_name；username、user ID、source、matched_rule、bio_state、标签和历史预测明确排除。
- 三个通道分别使用 `m:`、`b:`、`n:` 字符 n-gram 前缀，默认权重为 1.0、0.75、0.25；权重进入 manifest 和模型版本 hash。
- Telegram invite、普通 URL 和 mention 分别标记化。原始 Unicode 继续保存在样本和离线数据集中，不修改审核内容。
- 普通群消息复用既有 `get_chat()` 已取得的 Bio，不增加网络请求；规则提前命中仍在删除后的有界 worker 中短超时补充 Bio。失败缓存同时保存 `bio_state`。
- 相似度和 Logistic Regression 模型格式升级为 v2，并统一通过完整 `SpamSample` 使用多通道特征；`predict_text()` 保留为仅 message 通道的 CLI/回归便捷接口。
- 离线数据集升级为 format v2，保存原始及归一化的三个通道、用户分组摘要、消息模板摘要和 Bio 模板摘要。相同用户、消息模板或 Bio 模板通过连通分量整体切分。
- 重训候选记录 Spam/Ham 的 Bio、显示名称和独立用户覆盖；若只有一类存在 Bio，拒绝构建二分类候选。可评估时在同一留出集比较 message-only、message+bio、完整三通道。
- 初始 D1 Migration 直接要求 `schema_version=2`，在线去重键前缀升级为 v2。按项目维护者确认，不提供旧库升级、旧记录回填或旧模型兼容层。
- 重新生成 Docker 内置初始模型 `seed-sim-v2-aa3f115430e7bb8e`，删除旧 v1 artifact。V1/V2 预测仍只提供建议，没有增加任何模型驱动的管理动作。

## 修改文件与接口

- `docs/010-profile-features.md`：Story 目标、范围、验收、特征和重建约定。
- `spam_learning/features.py`：`FeatureChannels`、`ChannelWeights`、统一特征提取和通道 n-gram。
- `spam_learning/schemas.py`：Schema v2、`bio_state`、快照序列化与重建。
- `main.py`、`spam_learning/capture.py`：复用群消息 Bio、规则后台 Bio 状态和缓存。
- `spam_learning/seed_model.py`、`spam_learning/binary_model.py`、`spam_learning/inference.py`：v2 多通道模型训练、加载、推理和状态说明。
- `spam_learning/retrain.py`：dataset format v2、覆盖审计、连通分量切分、消融评估和多通道候选构建。
- `spam_learning/d1.py`、`migrations/001_create_samples.sql`：v2 去重前缀和全新建表约束。
- `models/spam_learning/manifest.json`、`models/spam_learning/seed-sim-v2-aa3f115430e7bb8e.json`：重新生成的确定性初始模型。
- `tests/`：覆盖 Bio 状态、群消息复用、字段排除、Bio 推理、单边 Bio 拒绝、v2 Migration 和既有回归。
- `README.md`、`docs/000-overview.md`、`docs/CONTRACTS.md`、`docs/PROGRESS.md`、本文件：同步最终契约、重建步骤和交接信息。

## 关键技术决策

- 不保存完整 Pyrogram `Message`。现有精选快照已经包含审核和特征需要的字段，避免头像 file ID、群权限及客户端内部字段扩大隐私和版本耦合。
- 不将三个字段裸拼接。通道前缀防止相同字符在消息、Bio、姓名中混为同一语义；通道权重允许在 manifest 中明确审计和复现。
- username 继续保存供人工审核，但不参与特征；user ID 只生成不可逆 actor 摘要用于防止评估泄漏。
- `bio_state` 用于区分空 Bio、请求失败和未请求，但不作为模型特征，避免模型学习采集路径或缺失状态。
- 初始 `spam.txt` 没有 Bio/姓名，因此内置模型虽为 v2，当前仍只有 message 词汇。必须积累人工 reviewed D1 数据并重训后，Bio 和 display_name 才能对线上分数产生作用。
- 项目尚未正式积累数据，直接采用最终 Schema 和模型格式；不为可丢弃的旧数据库、导出文件或模型增加迁移复杂度。

## 实际验证

- `venv/bin/python -m unittest discover -q`：76 项测试全部通过。
- 新增测试确认 username 和 user ID 改变不会改变特征；相同短消息配合经过标记化的 Spam Bio 可由多通道相似度模型识别为 `spam_like`。
- 新增测试确认普通群消息保存 Handler 已取得的 Bio 且不由 worker 再次查询；Bio 成功、空值和超时分别记录正确状态。
- 新增测试确认只有 Spam 或只有 Ham 一侧包含 Bio 时，二分类候选拒绝构建。
- `venv/bin/python -m compileall -q main.py spam_learning tests`：通过。
- `venv/bin/python -m spam_learning.d1_migrate`：全新 Schema v2 在本地 SQLite 校验通过。
- `venv/bin/pip check`：通过；本机 pip cache 权限警告不影响结果。
- `make train-spam-model PYTHON=venv/bin/python SPAM_MODEL_OUTPUT=models/spam_learning`：生成 `seed-sim-v2-aa3f115430e7bb8e`，25 条正例，无空行或重复被跳过。
- 初始模型对用户提供的短消息 `有时间就行，日入30oo+` 仍为 `unknown/0.194288`，符合预期：仓库 `spam.txt` 尚无该 Bio 语料，不能伪造多字段收益。
- `docker buildx build --platform linux/arm64 --load -t captchabot:story010 .`：通过，镜像内生成同一 v2 模型版本。
- 容器离线加载验证：`available=True`、`kind=similarity`、版本正确，确认种子文本输出 `spam_like`。
- `git diff --check`：通过。

## 已知问题与交接

- 未连接真实 Telegram 或 Cloudflare D1，未执行远程建表、数据库删除、真实审核或真实数据重训；不能将这些写成已验证。
- 现有远程 D1 如果已创建旧表，`CREATE TABLE IF NOT EXISTS` 不会升级它。部署前必须由维护者确认目标数据库后重建，再执行最终 `001_create_samples.sql`。
- Bio 和 display_name 的实际质量提升需要两类都有覆盖的人工 reviewed 数据。当前内置模型格式已支持，但没有资料字段训练语料。
- 只有 ARM64 镜像在本 Story 本地构建；AMD64 仍由既有多架构 CI 配置负责，未在本次重复本地构建。
- `python -m spam_learning.seed_model` 仍会输出既有 `runpy` RuntimeWarning；模型 hash、加载和推理结果正确，本 Story 未重构包级公共导入。
- D1 个人资料仍按当前项目决定长期保留；正式运行前应继续明确访问控制和人工删除流程。

## Git Commit / PR

- 尚未提交。

