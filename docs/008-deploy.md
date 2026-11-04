# Story 008：Linux ARM64/AMD64 Docker 打包与验收

## 依赖
Story 001–007。

## 开发任务
- 检查现有 Docker 配置，使用 Buildx 或 CI 构建 `linux/arm64` 和 `linux/amd64` 镜像；推理依赖必须支持 CPU-only。
- 模型文件随镜像打包；不需要模型 bind mount 或 SQLite volume。配置通过环境变量/Secret 注入；D1 Token 使用最小权限。
- 若原生依赖或 pickle 跨架构兼容性有问题，采用对应架构构建或可移植导出，并分别验证两种架构模型加载。
- Smoke Test：原有关键词删除、D1 pending、管理员按钮、重复回调、重启后回调、Bio 缺失、D1 故障、模型缺失、中文/Unicode、空消息/纯媒体消息。
- 在有代表性的 CPU 环境测量冷启动、峰值 RSS、P95 推理延迟；只报告真实测量，不编造结果。
- 记录部署、Migration、密钥配置、上线、回滚、样本保留/删除政策和故障排查。

## 验收标准
- 两种架构镜像都可构建、运行，模型纯 CPU 推理。
- 不依赖 Docker bind mount 或 Cloudflare Worker。
- 可随时关闭新模块，不影响原有关键词管理。
- 填写 `docs/notes/008-notes.md` 并更新进度。
