# Story 024：普通群消息采集排除列表 — 实施记录

状态：已完成。任务说明：[Story 024](../024-capture-exclude-groups.md)。

## 实际实现、文件与接口

- `spamdet/config.py` 新增 `SPAM_CAPTURE_EXCLUDE_GROUPS` 解析与 `record_exclude_groups` 字段，默认空集合。配置仅接受负数群 ID 逗号列表，允许周围空白，重复值合并，拒绝 `all`、正数、零及空列表项。
- `SpamLearningConfig.records_group(chat_id)` 优先检查排除集合，再执行既有普通采集选择；不改变学习总开关和默认关闭行为。
- `main.py` 已通过该接口控制普通群采集，因此无需修改 Handler、事件循环或有界队列。规则删除及规则样本仍走原路径。
- `tests/test_main_capture.py` 增加配置和实际 Handler 回归；`README.md` 增加用户提供群 ID 的配置示例和部署说明；Story、契约、进度及本 Notes 同步更新。
- 未变更依赖、Docker 配置、统一样本结构、D1 Schema 或模型格式。Compose 已使用 `env/captcha.env` 注入配置。

## 设计决策

排除只针对普通 `source=group` 采样，符合减少无关样本的需求。不能在 Handler 顶部按群直接返回，否则会绕过现有规则。无需引入语言检测依赖，也不根据单条 URL 猜测消息语言。

## 实际验证

- `venv/bin/python -m unittest tests.test_main_capture -q`：18 项通过。
- `venv/bin/python -m unittest discover -q`：125 项通过。
- 新增回归覆盖排除优先于 all/指定列表、空值兼容、空白与重复 ID、非法配置、总开关关闭和未开启普通采集；实际 Handler 验证排除群普通消息不入队，关键词消息仍删除并采集原始 rule 快照，删除前无 Bio 请求。
- `git diff --check`：通过。
- 未连接真实 Telegram 或远程 D1，未构建或部署 Docker 镜像；测试出现既有 zhconv 文件未关闭 ResourceWarning，不影响通过。

## 已知限制与交接

- 更新运行环境变量并重启后生效；Docker 使用包含新代码的镜像，并重建容器以重新载入 env_file。
- 不自动清理已保存的 D1 样本，不移除已导出数据或模型内容。群 ID 排除不等于语言过滤，该群中的中文普通消息同样会被排除。
- 既有群 Handler 为 Bio 规则执行的资料查询仍然存在；本功能只减少普通消息采集，不绕过群规则检查。
- 本会话仅完成 Story 024，后续需求另立 Story。

## Git Commit / PR

尚未提交；未创建 PR。
