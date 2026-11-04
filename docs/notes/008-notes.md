# Story 008：Docker 部署 — 实施记录（Notes）

> 状态：已完成。
> 任务说明：[`../008-deploy.md`](../008-deploy.md)

## 实际完成内容
- 保留现有 GitHub Actions Buildx 双架构发布方式，继续构建 `linux/amd64` 和 `linux/arm64`。
- Docker 构建阶段使用既有 `make train-spam-model`，从仓库内 21 条人工确认的 `spam.txt` 生成确定性初始相似度模型。
- 将生成的 JSON 模型复制到运行镜像 `/CaptchaBot/models/spam_learning`，并将镜像默认 `SPAM_MODEL_PATH` 指向该目录；运行时不需要模型 bind mount、SQLite volume、在线下载或在线训练。
- 模型构建产物为 `seed-sim-v1-8d54e5f7750b83a2`，AMD64 与 ARM64 镜像生成并加载相同版本。
- 为 pip 安装步骤增加 BuildKit cache、30 秒网络超时和 5 次有限重试，降低多架构构建遇到 wheel 下载断流时从头下载的概率。
- 保留 builder 中的 `build-essential`，用于在两个目标架构分别编译 `tgcrypto`；编译工具不会进入最终 runner 镜像。
- 根据本 Story 确认的范围，不执行性能指标验收，不扩展大规模端到端测试，不修改 Compose、正式镜像标签策略或既有业务 Handler。
- D1 pending/reviewed 样本不自动清理，默认长期保留；实际人工删除和最终保留期限由项目维护者决定。

## 新增或修改的文件
- `Dockerfile`：安装构建依赖、构建初始模型、复制模型到 runner、设置镜像内默认路径，并为 pip 增加下载缓存和有限重试。
- `.github/workflows/ai-test.yaml`：在 `ai` 分支使用现有 QEMU/Buildx 方式构建并推送 AMD64/ARM64 镜像。
- `models/spam_learning/manifest.json`、`models/spam_learning/seed-sim-v1-8d54e5f7750b83a2.json`：初始相似度模型包；Docker 构建仍会从 `spam.txt` 确定性重建并覆盖为相同内容。
- `README.md`：记录镜像内模型构建、默认路径、无需 bind mount，以及 D1 不自动清理的策略。
- `docs/008-deploy.md`：按已确认范围更新模型打包、容器验证、性能验收和数据保留要求。
- `docs/PROGRESS.md`、本文件：记录 Story 完成状态和真实验收结果。

## 接口、Schema、配置与兼容性
- 镜像默认设置 `SPAM_MODEL_PATH=/CaptchaBot/models/spam_learning`；运行时仍可用环境变量覆盖，但覆盖路径必须真实存在于容器内。
- `SPAM_LEARNING_ENABLED` 继续默认关闭。关闭时不会创建 D1、采集、审核或推理服务，原有关键词管理路径保持兼容。
- 镜像内模型仍使用既有安全 JSON 格式，不使用 `pickle`/`joblib`，只执行 CPU 推理。
- 初始模型使用 Story 005 的相似度契约，只输出 `spam_like`、`unknown` 或 `unavailable`；分数不是概率，不能触发删除、封禁或禁言。
- 本 Story 经项目维护者确认直接打包由 `spam.txt` 构建的初始模型，不要求已有 Story 007 `approval.json` release。Story 007 的人工重训和批准流程仍可独立用于后续模型发布。
- 未增加环境变量、D1 Migration、数据库字段、Telegram callback 协议或 Python 运行时依赖。

## 关键技术决策及原因
- 在镜像构建期调用现有 Make 目标，而不是提交后再依赖运行时训练，确保每个镜像自带可用模型且部署不依赖 bind mount。
- 模型由纯 Python 标准库生成，artifact 是确定性 JSON；两个架构独立构建仍得到相同版本和推理结果。
- `tgcrypto==1.2.5` 在 Python 3.14 Linux 上需要从源码编译，因此 builder 保留完整 `build-essential`。实际尝试只安装 `gcc`/`make` 时因缺少 `stdint.h` 失败，恢复完整工具链后 AMD64 和 ARM64 均编译成功。
- pip 下载曾因外部连接 `IncompleteRead` 中断，因此增加 BuildKit cache 和有限超时/重试；这只改善构建可靠性，不改变运行镜像依赖。
- 按项目维护者确认，不修改既有多架构 YAML、浮动标签和 Compose，不执行冷启动、峰值 RSS 或 P95 推理延迟测量。

## 验证记录
- `venv/bin/python -m unittest discover -v`：67 项现有测试全部通过，包含关键词删除关键路径、D1 pending/审核、重复 callback、Bio 缺失/超时、模型缺失、Unicode、纯媒体快照和安全模型加载回归。
- `venv/bin/python -m compileall -q main.py spam_learning tests`：通过。
- `venv/bin/python -m spam_learning.d1_migrate`：本地 Migration 校验通过，无需真实 Cloudflare 凭证。
- `venv/bin/pip check`：通过，无损坏依赖；本机 pip cache 权限警告不影响结果。
- `make train-spam-model PYTHON=venv/bin/python SPAM_MODEL_OUTPUT=<临时目录>/model`：通过，读取 21 条正例并生成 `seed-sim-v1-8d54e5f7750b83a2`；中文推理成功。
- `docker buildx build --platform linux/amd64 --load -t captchabot:story008-amd64 .`：通过。
- `docker buildx build --platform linux/arm64 --load -t captchabot:story008-arm64 .`：通过。
- AMD64 容器离线验证：`machine=x86_64`，模型路径为 `/CaptchaBot/models/spam_learning`，`available=True`，版本正确；确认中文样本输出 `spam_like`。
- ARM64 容器离线验证：`machine=aarch64`，模型路径、可用性、版本、decision 和 score 与 AMD64 一致。
- `git diff --check`：通过。
- 未连接真实 Telegram、Cloudflare D1 或 Redis，未执行远程 Migration、真实审核通知或生产镜像推送；这些结果不写成已验证。
- 按项目维护者要求未执行冷启动、峰值 RSS、P95 推理延迟等性能验收。

## 已知问题、限制与后续事项
- `python -m spam_learning.seed_model` 会因包级公共导入输出一条 `runpy` RuntimeWarning，但实际模型版本、artifact 和推理结果正确；本 Story 未为了消除提示重构公共导入。
- D1 样本不会自动清理，将长期保留；如以后需要删除，由项目维护者另行确认范围并人工执行。
- 真实 Telegram、真实 D1、远程 Migration 和 GitHub Actions 推送结果未在本地验收；上线时仍需使用真实凭证观察首次运行日志。
- 初始模型来自 21 条 Spam 且没有 Ham，只能提供启发式相似度建议，不代表可靠二分类质量。
- Docker Compose、正式镜像标签和回滚标签策略按项目维护者决定保持现状。

## 下一 Story 交接要点
- Story 008 是当前规划的最后一个 Story；后续如更换人工批准 release、增加自动清理或调整发布标签，应作为新的独立 Story 处理。
- 更新 `spam.txt` 后必须重新构建镜像，不能在运行容器中训练或替换模型。
- 任何后续模型仍只能提供建议，不能改变现有规则作为唯一自动删除依据的约束。
- 生产启用前需配置 `SPAM_ADMIN_ID` 和三项 D1 环境变量；密钥不得写入镜像、仓库或日志。

## Git Commit / PR
- Story 008 模型打包与文档提交：`cae7c6abd3b3cd8712b24a21a0c547b880eefd0a`（`step 8`）。
- `ai` 分支多架构 workflow 提交：`3b79f73266b1b83945d551fe1971b50b76f2dcb2`、`bb5a3ee0fc73b30588dc9cf5734d119e0f9c36bb`。
- 本次补充的 pip 构建缓存、完整 Notes 和进度更新：尚未提交。
