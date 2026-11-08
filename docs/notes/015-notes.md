# Story 015：从文本文件补录人工 Spam — 实施记录（Notes）

> 状态：已完成。任务说明：[`../015-import-spam-file.md`](../015-import-spam-file.md)

## 实际完成内容

- 新增 `make import-spam-file file=message.txt`，从一个 UTF-8 `.txt` 文件补录一条完整、多行的人工确认 Spam。
- 文件内部换行、空格和原始 Unicode 保留；接受 UTF-8 BOM，只移除文件末尾 CR/LF，避免编辑器末尾换行成为样本内容。
- 补录样本固定为 `source=manual/status=reviewed/label=spam`，使用 `SPAM_ADMIN_ID` 写入 `reviewed_by`；未知 `chat_id/message_id/date` 保持 null。
- 相同文本已是 reviewed/spam 时幂等返回最早已有 ID；若存在 pending、Ham 或其他冲突状态则拒绝自动补录，不覆盖既有标签。
- CLI 直接调用必须显式提供 `--confirm-spam`；缺失、非 `.txt`、非 UTF-8 和空文件均在连接 D1 前拒绝。

## 修改文件、接口和配置

- `Makefile`：新增 `import-spam-file` target 和 help 文案，安全传递 `file` 路径。
- `spamdet/d1_import_spam.py`：文件读取、人工确认、环境配置和 CLI 输出。
- `spamdet/d1.py`：新增 `ManualSpamImportResult`、`D1Client.import_manual_spam()` 和手工文本去重键。
- `tests/test_d1.py`：覆盖 D1 写入字段、参数化 SQL、幂等性和冲突拒绝。
- `tests/test_d1_import_spam.py`：覆盖多行 Unicode、BOM、末尾换行、输入错误和 CLI 分发。
- `docs/015-import-spam-file.md`：Story 目标、范围、边界和验收条件。
- `README.md`、`docs/000-overview.md`、`docs/CONTRACTS.md`、`docs/PROGRESS.md`、本文件：同步命令、契约、状态和交接。
- 未新增环境变量、第三方依赖、D1 Migration 或模型格式。

## 设计决策

- 一个文件明确代表一条样本，不按行拆分；垃圾消息常包含有意义的重复行和内部换行，拆行会破坏真实消息结构并错误扩大样本数。
- 使用既有 `sample_from_seed_text()` 构造统一样本，明确表示人工来源，不伪造 Pyrogram `Message` 或未知 Telegram 标识符。
- 文本和所有写入值通过参数绑定传给 D1；去重键只包含固定版本前缀和文本 SHA-256，不在键中暴露原文。
- INSERT 自身带 `WHERE NOT EXISTS (SELECT 1 FROM samples WHERE text = ?)`，不仅依赖写入前查询，避免并发窗口产生相同文本的重复记录。
- 不自动把相同 pending/Ham 改成 Spam。标签纠正仍使用 Story 012 的现有人工 ID 标注入口，保持冲突处理显式可审计。
- Make target 传入 `--confirm-spam`，但 target 名称和人工执行仍是信任边界；在线规则和模型代码没有调用该入口。

## 实际验证

- `venv/bin/python -m unittest tests.test_d1 tests.test_d1_import_spam -v`：26 项 D1/补录定向测试通过。
- `venv/bin/python -m unittest discover -v`：96 项完整测试全部通过。
- `venv/bin/python -m compileall -q main.py spamdet tests`：通过。
- `venv/bin/python -m spamdet.d1_migrate`：本地 Schema v2 Migration 校验通过。
- `venv/bin/pip check`：通过；本机 pip cache 权限警告不影响结果。
- `make help`：显示新 target 和参数用途。
- `make -n import-spam-file file='local/message with spaces.txt' PYTHON=venv/bin/python`：确认带空格路径通过环境变量加引号传给 CLI。
- `make import-spam-file`：缺少 `file` 时在网络调用前以退出码 2 失败。
- 使用真实 D1 和刚补录的 303 字符多行样本验证：新 CLI 返回“相同 reviewed/spam 样本已存在 id=168，未重复写入”，确认真实环境幂等路径。
- `git diff --check`：通过。

## 已知问题与下一 Story 交接

- 当前入口一次只补录一个文件/一条样本，不支持目录或批量 manifest；需要多条时逐个执行，避免一次错误批量产生大量人工标签。
- 纯文本文件无法携带用户 ID、群 ID、消息 ID、时间、Bio 或显示名称；这些未知字段保持 null。需要保留结构化来源时应另立 Story 设计明确格式，而不是从文本推断。
- 本地 `.env` 不由运维 CLI 自动加载；按 README 导出 D1 和 `SPAM_ADMIN_ID` 环境变量，或在已注入环境变量的容器中执行。
- 该功能只补录 D1，不自动重训、批准或部署模型。

## Git Commit / PR

- Git commit：本提交（`add D1 spam file import`）。
- PR：直接推送当前 `master`，未创建 PR。
