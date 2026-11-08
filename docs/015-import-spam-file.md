# Story 015：从文本文件补录人工 Spam

## 目标

提供明确的 Make 运维入口，将一个 UTF-8 `.txt` 文件中的完整、多行消息补录为 D1 人工确认 Spam，避免临时 SQL 和手工构造样本 JSON。

## 范围

- `make import-spam-file file=message.txt`：一个文件代表一条完整 Spam，不按行拆分。
- 保留内部换行、空格和原始 Unicode；接受 UTF-8 BOM，只移除文件末尾的 CR/LF。
- 补录为 `source=manual/status=reviewed/label=spam`，使用 `SPAM_ADMIN_ID` 写入 `reviewed_by`。
- 未知的 Telegram `chat_id/message_id/date` 保持 `null`，不伪造 Pyrogram `Message`。
- 相同文本已是 reviewed/spam 时返回原 ID；存在 pending、Ham 或其他冲突状态时拒绝自动补录。
- CLI 必须显式提供 `--confirm-spam`；Make 目标名称和参数表达人工确认意图并传入该标志。

## 不可破坏的边界

- 只有人工确认的文件可以补录；规则命中和模型建议不能调用该入口自动产生训练标签。
- 所有 SQL 值参数化，不拼接文件内容、管理员 ID 或去重键。
- 不改变在线删除、采集、审核通知、callback 或模型推理路径。
- 不覆盖或纠正既有相反标签；冲突必须由人工使用现有标注入口处理。

## 验收条件

- 多行中文和 Unicode 原样写入独立 `text` 列及统一样本 JSON。
- 新样本为 manual/reviewed/spam，审核人为 `SPAM_ADMIN_ID`，未知 Telegram 字段为 null。
- 重复执行不会创建重复样本；冲突状态不会被静默覆盖。
- 缺失文件、非 `.txt`、非 UTF-8、空文件、缺少人工确认或缺少 Make `file` 参数时均在写入前失败。
- 定向测试、完整测试、编译检查、Migration 校验、Make dry-run 和 `git diff --check` 通过。
