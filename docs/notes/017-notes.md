# Story 017：共享消息内容提取 — 实施记录（Notes）

> 状态：已完成。任务说明：[`../017-shared-content-extraction.md`](../017-shared-content-extraction.md)

## 实际完成内容

- 新增独立 `spamdet.content` 模块，将 Story 016 的结构化消息解析从统一样本 Schema 中移出。
- 新增 `extract_message_content(message) -> ExtractedMessageContent`，统一返回 `text`、`caption`、稳定 entities、内容来源和规则匹配用 `search_text`。
- 普通 text/caption 保持既有存储契约：caption 仍存入 `message.caption`，`message.text` 保持空字符串。
- Checklist、Poll 等结构化可见文字继续按原顺序换行展开，entities 使用 UTF-16 code unit offset。
- `sample_from_message()` 与 `sample_from_admin_forward()` 共用提取器，并可接收已提取结果，避免群 Handler 重复解析。
- 群 Handler 在任何 Sticker/Bio 网络请求之前同步提取一次内容；同一结果用于 `BLACKLIST_MESSAGE`、规则删除前快照和普通群采集。
- 群内结构化内容命中已有关键词时照常立即删除并写 `rule/pending`；未命中时只有允许群才写 `group/pending`，不会自动成为训练标签。

## 修改文件、接口和配置

- `spamdet/content.py`：新增 `ExtractedEntity`、`ExtractedMessageContent` 和公共提取函数。
- `spamdet/schemas.py`：移除具体结构化内容分支，改为消费公共提取结果；两个样本构造函数增加可选 `content` 参数。
- `spamdet/__init__.py`：导出新的公共类型和函数。
- `main.py`：群消息入口只提取一次内容；`delete_rule_message()` 支持接收同一快照。
- `tests/test_content.py`：覆盖 text/caption 契约、Checklist UTF-16 offset 和无文字媒体。
- `tests/test_main_capture.py`：覆盖群内 Checklist 关键词秒删和 Poll 普通 pending 采集。
- `README.md`、`docs/000-overview.md`、`docs/CONTRACTS.md`、`docs/PROGRESS.md`、当前 Story 和本 Notes：同步接口、行为和交接。
- 未新增环境变量、第三方依赖、D1 字段、Migration 或模型格式。

## 设计决策

- 内容解析独立于 Schema：提取器只描述 Telegram 可见文字，Schema 负责把稳定结果放入业务样本，避免规则层和存储层各维护一套媒体分支。
- `search_text` 只提供匹配视图，不改变 caption 的持久化语义。结构化内容没有现成 v2 字段，因此按既有 Story 016 契约确定性放入 text。
- 群 Handler 复用同一个不可变提取结果，避免关键词看到的内容与删除前保存内容不一致，也避免重复遍历复杂 Checklist/Poll。
- 提取完全同步、纯本地，不读取 `Message.raw`，不访问 Telegram/D1/Bio/模型，因此不破坏关键词立即删除约束。
- 内容提取不代表人工标签。管理员私聊转发、规则命中和普通群消息继续分别使用 manual/reviewed/spam、rule/pending、group/pending。
- 无文字二进制媒体仍返回空内容，不执行 OCR/ASR，也不制造可被规则或模型学习的占位符。

## 实际验证

- `venv/bin/python -m unittest tests.test_content tests.test_main_capture -v`：19 项定向测试全部通过。
- `venv/bin/python -m unittest discover -v`：110 项完整测试全部通过。
- 群内 Checklist 任务包含 `洗钱` 时，在任何 `get_chat()` 调用前命中现有 `BLACKLIST_MESSAGE`，删除后样本保留完整标题和任务文本。
- 未命中规则的 Poll 在允许群内保存为 `source=group/status=pending/label=null`，问题和选项均进入样本文本。
- 普通 caption 仍保持 `text=""/caption=<原文>`；包含 emoji 的 Checklist entity offset 继续按 UTF-16 正确映射。
- `venv/bin/python -m compileall -q main.py spamdet tests`：通过。
- `venv/bin/python -m spamdet.d1_migrate`：本地 Schema v2 Migration 校验通过。
- `venv/bin/pip check`：通过；pip cache 权限警告不影响结果。
- `git diff --check`：通过。
- 未连接真实 Telegram 或 Cloudflare D1，未执行真实结构化群消息或远程写入。

## 已知问题与下一 Story 交接

- 结构化内容字段按固定 `kurigram==2.2.26` 公共类型实现；真实群内 Checklist/Poll 更新尚未联机验证。
- 现有群 Handler 对未提前命中关键词的消息仍会执行既有 `get_chat(message.from_user.id)`；本 Story 只保证本地关键词路径不会为内容提取新增网络等待。
- 只有有地址文字的 Location 才能形成内容；坐标、Photo、Voice、VideoNote、Dice 和无文字 PaidMedia 保持空内容。
- 内容提取接口今后增加新 Telegram 媒体类型时，应同时补公共模块测试、群规则测试和管理员转发回归，不要在 Handler 或 Schema 中另写分支。

## Git Commit / PR

- Git commit：本提交（`share message content extraction`）。
- PR：尚未创建。
