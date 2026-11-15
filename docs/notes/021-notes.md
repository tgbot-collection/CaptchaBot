# Story 021：重训支持仅有个人资料的样本

## 实现

- `spamdet/retrain.py` 允许 D1 已审核样本的正文及 caption 为空，并从既有独立通道读取 Bio 和显示名称。沿用 format v2 和原始 Unicode。缺少 `message` 对象仍为结构错误。
- 归一化后三个通道都为空时，沿用既有的跳过逻辑。导出会报告跳过条数。D1 写入、字段及配置均保持现有契约。
- 切分时忽略空正文模板键，避免仅含个人资料的样本被错误串成同一组。同用户和相同 Bio 模板仍整组切分。
- `tests/test_retrain.py` 增加回归测试。Story、进度与共享契约已更新。

## 验证与交接

- 新测试覆盖空正文和 null caption、Bio 和显示名称保留、仅 Bio、仅显示名称、全空跳过、数据集读写及构建两种候选。122 项测试全部通过，编译和 `git diff --check` 通过。
- 模拟 D1 导出验证了保留可用资料及跳过条数提示。真实 D1 与真实重训未运行，二分类仍受两类 Bio 覆盖和独立分组检查约束。
- 后续从已配置 D1 的终端重新执行 `make retrain-spam-model CLASSIFIER=1`。

## Git Commit / PR

尚未提交。
