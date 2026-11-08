# Story 014：消息时间统一为 UTC — 实施记录（Notes）

> 状态：已完成。任务说明：[`../014-message-date-utc.md`](../014-message-date-utc.md)

## 实际完成内容

- 修正 `_utc_iso()`：Kurigram 2.2.26 返回的无时区 `datetime` 先按进程本地时区解释，再转换为 UTC；不再把上海墙上时间直接标记为 `Z`。
- 已带时区的 `datetime` 继续通过 `astimezone(timezone.utc)` 转换，输出统一为 ISO 8601 `Z`。
- 新增受保护的一次性 D1 回填 CLI，使用代码内固定 SQL 将旧容器误标的上海时间减 8 小时。
- 回填 SQL 只匹配 `message.date` 与 D1 UTC `created_at` 相差 7.5–8.5 小时的 Schema v2 记录；修正后重复执行不会再次偏移。
- 已对真实远程 D1 执行回填：148 条带日期记录全部符合旧错误特征并完成修正，剩余候选为 0。

## 修改文件、接口和配置

- `spamdet/schemas.py`：修正无时区 Kurigram 时间的 UTC 转换。
- `spamdet/d1_message_date_backfill.py`：固定历史回填 SQL、显式确认 CLI 和环境配置入口。
- `tests/test_message_dates.py`：覆盖上海无时区转换、aware 时间转换、SQL 目标范围、幂等性和 CLI 确认。
- `docs/014-message-date-utc.md`：Story 目标、范围、边界和验收条件。
- `README.md`、`docs/000-overview.md`、`docs/CONTRACTS.md`、`docs/PROGRESS.md`、本文件：同步 UTC 契约、Story 状态和真实执行结果。
- 未新增环境变量、第三方依赖、D1 字段或模型格式。

## 设计决策

- Kurigram 2.2.26 的 `timestamp_to_datetime()` 使用 `datetime.fromtimestamp()`，返回进程本地的无时区时间；因此使用 `value.timestamp()` 按同一进程本地时区还原 Unix 时间，再构造 UTC `datetime`，可同时适配上海和 UTC 容器。
- 不读取或序列化 Kurigram `Message.raw`，继续只使用公共 `message.date`，避免引入内部对象耦合。
- 不直接对所有历史日期无条件减 8 小时。回填以 `created_at` 为 UTC 参照识别旧错误特征，既避免误改异时区记录，也保证重复执行安全。
- 不修改 `created_at`：它由 Cloudflare D1 的 `CURRENT_TIMESTAMP` 生成，本来就是正确 UTC。
- 不回填新的本地展示字段。存储层统一 UTC，日志和界面需要本地时间时再按 `Asia/Shanghai` 转换。

## 实际验证

- `venv/bin/python -m unittest tests.test_message_dates -v`：5 项新增测试通过。
- `venv/bin/python -m unittest discover -v`：90 项完整测试全部通过。
- `venv/bin/python -m compileall -q main.py spamdet tests`：通过。
- `venv/bin/python -m spamdet.d1_migrate`：本地 Schema v2 Migration 校验通过。
- `venv/bin/pip check`：通过；本机 pip cache 权限警告不影响结果。
- `git diff --check`：通过。
- 远程执行前：148 条样本、148 条非空 `message.date`、148 条符合约 8 小时时差条件，ID 范围 3–167。
- 远程执行后：148 条日期均保留，符合旧错误条件的剩余记录为 0；`message.date` 与 `created_at` 差值范围为 -3–0 秒。
- 排除 `message.date` 后的全表 canonical SHA-256 在回填前后均为 `d3bc61d5577ebbd69fd2293b04d51346cf4746bb5dee0b54ec4e5cd0b5ff0f8c`，确认其他字段未变化。
- 日期集合 SHA-256 从 `0b97fbbdce4e9649e6409b1421c9fe94ecbb2dd2a33310b785719e1a4e74c865` 变为 `be983fbda0f1ba191641dd523477479fd7beb570c197571a32a6f8ec5286f961`，确认历史日期确已更新。

## 已知问题与下一 Story 交接

- 修复代码需要重新构建并部署 Bot 镜像后，新消息才会按正确 UTC 写入；远程 D1 历史数据已经修正，不依赖部署。
- 回填只自动处理与 `created_at` 相差 7.5–8.5 小时的记录；本次真实库 148 条全部命中，没有遗留不符合条件的历史日期。
- 容器日志继续按 `TZ=Asia/Shanghai` 显示本地时间，这是展示行为，不与数据库 UTC 契约冲突。
- Story 014 没有改变 Laebi Daniel 消息未进入 Telegram Handler 的既有调查结论；两者是独立问题。

## Git Commit / PR

- Git commit：本提交（`fix message timestamps in D1`）。
- PR：尚未创建。
