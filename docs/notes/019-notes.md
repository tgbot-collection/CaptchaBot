# Story 019：本地 D1 样本审核网页

## 实际实现

- `review.html` 提供内联 CSS 和 JavaScript，支持状态或标签筛选、倒序分页、悬停或键盘聚焦预览 JSON、一键复制原始 JSON（使用带无障碍名称的眼睛和复制图标）、显示总数和筛选条数、当前页全选（点击整行表头）、点击样本行切换勾选及单条和批量操作。数据用纯文本绘制，保留 Unicode 和 JSON 原文。
- `review.command` 是可执行的 macOS 启动文件。优先使用仓库 `venv/bin/python`，否则使用 `python3`。
- `spamdet/web_review.py` 按需启动仅监听 `127.0.0.1` 的 Python HTTP 服务，验证 Host、Origin 及请求参数，按现有 D1 配置和 `SPAM_ADMIN_ID` 操作。支持 `--env-file` 和 `--no-browser`。
- `spamdet/d1.py` 增加 `list_review_samples` 和 `delete_review_samples`，使用参数绑定。标注沿用已有 `label_samples`，同步独立列和 JSON，并以既有管理员 ID 记录审核人。
- `README.md`、`docs/019-web-review.md`、`docs/CONTRACTS.md`、`docs/PROGRESS.md` 和本 Notes 记录操作方法、接口约定与验收结果。

## 设计决策

- 对 D1 Query REST 用虚构账号和数据库 ID 发送文件来源的 OPTIONS 预检，收到 HTTP 405。单页通过同源本机 Python 服务连接 D1，凭证留在服务进程，静态 HTML 双击时显示启动指引。
- 默认显示 pending，每页 50 条，选择范围为当前页。行 ID 以字符串传给浏览器，规避 JavaScript 大整数精度损失。总数、筛选条数与分页数据通过同一 SQL 查询返回，空页也返回计数。
- delete 在确认后删除 D1 样本。已不存在的 ID 满足删除目标，因此请求可安全重试。D1 响应未获确认时提示刷新核对。网页操作不会编辑 Telegram 审核通知或 Redis 聚合记录。

## 实际验证

- 用两条模拟样本在浏览器点击 `text`、`action`、`message_json` 表头和全选框，确认当前页全选和取消全选正常。也验证了点击样本正文和复选框切换单条选择，点击 JSON 预览不改变选择。

- 用 53 条内存 SQLite 模拟样本和 D1 REST 模拟响应，在浏览器验证表头、带 HTML 字符的 Unicode 原文、展开 JSON、下一页、全选、批量 Ham、单条改 Spam 及标签筛选。
- 通过同一模拟服务的实际 HTTP 接口验证单条和批量删除、删除重试、跨来源请求拒绝、无效 ID 拒绝、标签批次中含缺失 ID 时返回 409。
- 后续用 52 条模拟样本验证总数、筛选条数、空筛选及标注后刷新，浏览器验证键盘聚焦预览和复制成功提示。内置浏览器通过工具未能读回剪贴板内容。
- 121 项既有测试通过。Python 编译、Migration 本地校验、依赖检查、`review.command --help` 与 `git diff --check` 通过。

## 已知限制与后续交接

- 未对真实 D1 或 Telegram 进行验证，未在 Finder 实际双击，启动脚本已由终端验证。网页只在本机服务运行期间可用。
- 网页删除只影响 D1 样本。此前已生成的训练导出、模型文件和 Telegram 通知不会因数据库删除而更新。Redis 聚合会沿用已有 TTL。

## Git Commit / PR

尚未提交。
