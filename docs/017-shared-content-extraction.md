# Story 017：共享消息内容提取

## 目标

将 Telegram `Message` 的可见文字提取从统一样本 Schema 中拆为独立公共接口，并让管理员转发、
群内规则匹配和群消息采集共用同一份提取结果。

## 范围

- 新增无网络 I/O 的 `extract_message_content(message)` 公共函数。
- 普通 `text` 与 `caption` 保持现有分离存储契约。
- 无 `text/caption` 时，继续支持 Story 016 已确认的结构化内容展开和 UTF-16 entity offset 重映射。
- `sample_from_message()` 与 `sample_from_admin_forward()` 共用该接口。
- `group_message_preprocess()` 的本地关键词判断和普通群消息非空判断使用同一提取结果。
- 内容提取异常不得引入 Telegram、D1、Bio 或模型网络等待。

## 不在范围内

- 不改变 `source/status/label` 语义；群消息不会因结构化内容被直接标记 Spam。
- 不增加模型驱动的删除、封禁或禁言。
- 不为无文字媒体执行 OCR、ASR 或伪造占位文本。
- 不新增环境变量、依赖、D1 字段或 Migration。

## 验收条件

1. 独立接口返回持久化用 text/caption、关键词匹配用 search text、稳定 entities 和内容来源。
2. 普通文本、caption、Checklist、Poll 等结构化消息在管理员转发与群消息路径得到一致文字。
3. 群内结构化消息可被现有 `BLACKLIST_MESSAGE` 本地规则命中，并继续先快照、立即删除、后台入队。
4. 未命中规则的结构化群消息仅在 `SPAM_CAPTURE_GROUPS` 允许时保存为 `group/pending`。
5. 管理员私聊转发仍直接为 `manual/reviewed/spam`；其他群消息仍须人工审核。
6. 既有 text/caption Schema、entities、模型特征和 D1 接口保持兼容。
