# Story 012：D1 批量人工标注入口

## 目标

提供简短、可审计的 Makefile 运维入口，把一个或多个已有 D1 样本人工标注为 Spam 或 Ham，并完整维护审核状态字段。

## 范围与验收

- 支持 `make ham id=1,2,3` 和 `make spam id=1,2,3`；`id` 只接受逗号分隔的正整数，重复 ID 去重。
- 操作要求 `D1_ACCOUNT_ID`、`D1_DATABASE_ID`、`D1_API_TOKEN` 和 `SPAM_ADMIN_ID`；主功能关闭时也可独立执行。
- 每条目标记录更新为 `status=reviewed`，写入指定 `label`、`reviewed_at=CURRENT_TIMESTAMP` 和 `reviewed_by=SPAM_ADMIN_ID`。
- 同步更新 `message_json` 内的 `status` 和 `label`，避免独立列与统一样本 JSON 不一致。
- 允许纠正已经 reviewed 的旧标签；纠正时重新记录本次操作时间和管理员。
- 一个批次中任一 ID 不存在时整批不更新，并以非零状态失败；所有 SQL 值必须参数绑定。
- Telegram 按钮审核继续只允许 `pending -> reviewed`，重复点击不覆盖首次结果；运维入口不改变在线审核语义。
- 自动化测试不得连接真实 D1，也不得修改真实数据。

## 非目标

- 不根据模型、规则命中或历史预测自动生成标签；调用者必须已经人工确认。
- 不删除样本，不修改 Telegram 审核通知，不自动训练、批准或部署模型。
- 不修复 Story 011 的 `reset-d1` 数据库 ID 二次确认实现偏差；该问题单独交接。
