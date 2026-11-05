# Story 012：D1 批量人工标注入口 — 实施记录（Notes）

> 状态：已完成。任务说明：[`../012-manual-label.md`](../012-manual-label.md)

## 实际完成内容

- Makefile 增加 `spam` 和 `ham` target，支持 `make ham id=1,2,3` 形式的批量人工标注。
- 新增 `spamdet.d1_label` CLI，严格解析逗号分隔的正整数 ID，重复 ID 按首次出现去重。
- 新增 D1 原子批量标注接口，统一更新 `status=reviewed`、`label`、`reviewed_at`、`reviewed_by` 及 `message_json` 内嵌状态。
- 批次中存在缺失 ID 时整批不更新；已 reviewed 样本可以通过运维入口纠正标签。
- 在线 Telegram callback 仍只执行 `pending -> reviewed`，不会因本 Story 改成可覆盖审核。

## 修改文件、接口和配置

- `Makefile`：增加 `spam`、`ham` 和 help 文案；要求 `id` 参数。
- `spamdet/d1_label.py`：新增批量标注 CLI。
- `spamdet/d1.py`：新增 `D1Client.label_samples()`；既有 `review()` 同步维护 `message_json`。
- `spamdet/config.py`：新增 `require_admin_id()`，允许主功能关闭时的人工运维显式读取管理员 ID。
- `tests/test_d1.py`、`tests/test_d1_label.py`：覆盖批量更新、纠正、JSON 同步、缺失 ID 全不更新、参数解析和 CLI 分发。
- `README.md`、`docs/000-overview.md`、`docs/CONTRACTS.md`、`docs/PROGRESS.md`、当前 Story 和本 Notes：同步命令、契约和交接。
- 未新增环境变量、依赖或 Migration；使用既有 `SPAM_ADMIN_ID` 和 D1 配置。

## 设计决策

- 批量 SQL 使用单条参数化 `UPDATE` 和 `json_each(?)`，避免拼接 ID；`NOT EXISTS` 在更新前确认所有 ID 存在，保证缺失 ID 时零更新。
- 运维入口允许纠正人工误标，因此不限定 pending；每次操作都会刷新 `reviewed_at` 和 `reviewed_by`，保留最后一次人工操作信息。
- `message_json` 是统一样本结构的一部分，必须与独立查询列同步；既有 callback 审核也一并修正该一致性问题。
- 不修改审核通知引用。该命令只负责数据标签，不向 Telegram 发送或编辑消息。

## 实际验证

- `venv/bin/python -m unittest discover -v`：83 项测试全部通过，包含新增批量 Ham、已审核 Spam 纠正为 Ham、重复 ID 去重、JSON 同步和缺失 ID 整批不更新测试。
- `venv/bin/python -m unittest tests.test_d1 tests.test_d1_label -v`：23 项 D1/CLI 定向测试通过。
- `make help`：成功显示 `spam`、`ham` 的批量用法。
- `make -n ham id=1,2,3`：确认通过导出的 `SPAM_SAMPLE_IDS` 向 `python -m spamdet.d1_label --label ham --ids` 传入批量 ID。
- `make ham`：在网络调用前因缺少 `id` 以退出码 2 失败，未接触真实 D1。
- 使用包含引号和 shell 分隔符的恶意 `id` 值验证：值不会被 shell 重新解释，CLI 在网络调用前以退出码 2 拒绝。
- `venv/bin/python -m compileall -q main.py spamdet tests`：通过。
- `venv/bin/python -m spamdet.d1_migrate`：本地 Migration 校验通过。
- `venv/bin/pip check`：通过；本机 pip cache 权限警告不影响结果。
- `git diff --check`：通过。
- 未配置或连接真实 Cloudflare D1，未执行真实批量标签修改。

## 已知问题与下一 Story 交接

- 未连接真实 Cloudflare D1，远程 API 对 `json_each/json_set/UPDATE RETURNING` 的实际响应仍需在真实环境首次操作时核对。
- Story 011 文档和 Notes 声称 `reset-d1` 会比较 `CONFIRM_D1_DATABASE_ID` 与 `D1_DATABASE_ID`，但当前 Makefile 只有固定确认词校验。此为进入 Story 012 前已存在的实现/文档偏差，本 Story未擅自扩范围修复。
- D1 只保留最后一次 `reviewed_at/reviewed_by/label`，没有标签变更历史表；若需要完整审计，应另立 Story 设计 append-only 审计记录。
- Git commit / PR：尚未提交。
