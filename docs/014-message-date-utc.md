# Story 014：消息时间统一为 UTC

## 目标

修正 Kurigram 无时区本地时间的序列化方式，使新样本的 `message.date` 与 D1 `created_at` 一样使用 UTC；一次性修复由 `TZ=Asia/Shanghai` 旧容器写入的历史数据。

## 范围

- 无时区 `datetime` 按当前进程本地时区解释后转换为 UTC，而不是直接附加 UTC 标签。
- 已带时区的 `datetime` 正常转换为 UTC。
- 历史回填只修改 `message_json.message.date`，固定减 8 小时；不修改 `created_at`、消息内容、标签、状态或审核信息。
- 回填只匹配 `message.date` 与 `created_at` 相差 7.5–8.5 小时的 Schema v2 记录，使 SQL 重复执行时不会再次减 8 小时。
- 回填 CLI 使用代码内固定 SQL，且必须显式确认历史数据来自 `TZ=Asia/Shanghai` 容器。

## 不可破坏的边界

- 不改变关键词删除、采集队列、D1 pending 顺序、人工审核或模型行为。
- 不把容器本地时间写成带 `Z` 的伪 UTC。
- 不自动推断或修正不符合明确 8 小时时差条件的历史记录。
- 不接受调用者提供任意 SQL、时区或偏移量。

## 验收条件

- 上海本地无时区时间 `2026-11-08 21:37:53` 序列化为 `2026-11-08T13:37:53Z`。
- 已带时区时间继续正确转换为 UTC。
- 历史 SQL 只修正符合旧错误特征的记录，第二次执行不重复偏移。
- 远程执行前后统计一致，除 `message_json.message.date` 外的字段不变。
- 定向测试、完整测试、编译检查、Migration 校验和 `git diff --check` 通过。
