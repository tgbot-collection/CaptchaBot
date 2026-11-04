# Story 001：初始化架构与接口约定

## 目标
检查现有 Pyrogram Bot，以最小改动为后续 Spam 学习模块建立基础，不改变当前关键词管理行为。

## 开发任务
- 阅读 `AGENTS.md`、总览和接口约定；检查关键词删除 Handler、事件循环/线程、Dockerfile、依赖和配置。
- 根据真实代码设计模块目录，例如 `spam_learning/{schemas,config,storage,capture,model,review}.py`；**不要创建没有用途的空模块**。
- 定义带版本的 `SpamSample` 类型、真实 Pyrogram `Message` 的快照提取方式，以及手工纯文本种子样本表示。Bio 缺失必须为 null，不能猜测。
- 添加默认关闭的功能开关和配置校验：`SPAM_LEARNING_ENABLED`、`SPAM_ADMIN_IDS`、D1 环境变量、模型路径。禁止硬编码密钥。
- 记录实际集成入口；如果真实项目与草案不一致，说明原因并更新 `CONTRACTS.md`。

## 验收标准
- 原有关键词删除行为完全不变。
- 能表达纯文本样本，不需要伪造 Pyrogram `Message`。
- 本 Story **不接入** D1、Telegram 审核通知或模型推理。
- 填写 `docs/notes/001-notes.md`，更新 `docs/PROGRESS.md`，记录 Story 002 的接入要点。
