# Story 008：Linux ARM64/AMD64 Docker 打包与验收

## 依赖
Story 001–007。

## 开发任务
- 检查现有 Docker 配置，使用 Buildx 或 CI 构建 `linux/arm64` 和 `linux/amd64` 镜像；推理依赖必须支持 CPU-only。
- 构建镜像时使用 `make train-spam-model` 从仓库内人工确认的 `spam.txt` 生成确定性初始相似度模型，模型文件随镜像打包；不需要模型 bind mount 或 SQLite volume。配置通过环境变量/Secret 注入；D1 Token 使用最小权限。
- 若原生依赖或 pickle 跨架构兼容性有问题，采用对应架构构建或可移植导出，并分别验证两种架构模型加载。
- 运行现有自动化回归，并在容器内验证模型存在、安全加载及中文/Unicode 推理；真实 Telegram、D1 和 Migration 联机验收不属于本 Story。
- 记录镜像构建、密钥配置、上线、回滚和故障排查。D1 样本不自动清理，保留期限和人工删除由项目维护者决定。

## 验收标准
- 两种架构镜像都可构建、运行，模型纯 CPU 推理。
- 不依赖 Docker bind mount 或 Cloudflare Worker。
- 可随时关闭新模块，不影响原有关键词管理。
- 本 Story 不执行冷启动、峰值 RSS 或 P95 推理延迟等性能验收。
- 填写 `docs/notes/008-notes.md` 并更新进度。
