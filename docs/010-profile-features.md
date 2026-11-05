# Story 010：多字段 Spam 特征

## 目标
在不改变现有规则删除权限和人工标签边界的前提下，将模型输入从仅有 `message.text/caption` 扩展为消息正文、Bio 和显示名称三个相互隔离的特征通道，提高对短文本、混淆文本和个人资料广告的识别能力。

本项目尚未正式积累生产数据。D1、离线数据集和模型产物均按最终格式重新创建，不实现旧数据库升级、旧记录回填或旧模型兼容层。

## 范围与验收

- 统一样本升级为 `schema_version=2`。`user` 增加 `bio_state`，取值仅限 `present`、`empty`、`unavailable`、`not_requested`；原始 Unicode 继续保留。
- 模型输入只包含：`message.text`（为空时使用 caption）、`user.bio`、`first_name + last_name`。`username` 只用于人工审核展示，不进入模型；`user.id` 只用于离线分组，绝不进入模型向量。
- `matched_rule`、`source`、历史预测、人工标签和 `bio_state` 不得作为模型特征，避免规则、来源和缺失状态泄漏。
- 普通群消息复用 Handler 已经取得的 `chat.bio`，不增加 Telegram 请求；规则提前命中仍先同步快照并立即删除，Bio 只在既有有界后台 worker 中短超时查询。
- 新增训练和推理共用的唯一特征提取接口。三个通道分别生成带稳定前缀的字符 n-gram，并使用 manifest 中声明的通道权重，不能裸字符串拼接。
- Bio 中 Telegram 邀请链接、普通 URL 和 mention 分别标记化；不记忆具体邀请链接或联系方式。
- 离线数据集升级为 format v2，保留三个原始特征通道、归一化通道、人工标签、来源引用、用户分组摘要和模板分组摘要。只接受 `reviewed/spam|ham`；pending、规则命中和历史预测不得成为训练标签。
- 相同用户、相同消息模板或相同 Bio 模板不得跨训练集与测试集。用户 ID 只在导出时生成摘要，不写入模型 artifact。
- 构建报告必须记录两类样本的 Bio 覆盖率、显示名称覆盖率、独立用户数和模板数。若一类 Bio 全部缺失而另一类存在 Bio，启用 Bio 的二分类候选必须拒绝构建。
- 相似度模型和二分类模型均使用相同多通道特征接口。少量纯正例仍只能生成 `spam_like` 或 `unknown`；二分类结果仍只是建议。
- 训练报告在数据条件允许时比较 message-only、message+bio、message+bio+display_name；没有两类齐全的独立分组测试集时明确报告无法比较，不生成虚假结论。
- D1 初始 Migration、测试 fixture、Docker 内置模型和文档直接更新为最终格式；不新增升级 Migration。
- V1/V2 模型均不得触发删除、封禁、禁言或其他管理动作。现有关键词命中后的立即删除路径不得等待 Bio、D1 或模型推理。

## 特征契约

模型特征通道：

```text
message       默认权重 1.00
bio           默认权重 0.75
display_name  默认权重 0.25
```

每个通道独立归一化并生成字符 2–5 gram，词项分别使用 `m:`、`b:`、`n:` 前缀。通道为空时不生成特征，不使用“缺失 Bio”作为分类信号。权重、归一化配置和 n-gram 范围必须进入 manifest 和模型版本 hash。

`predict_text(text)` 继续作为只提供 message 通道的便捷接口；在线 `predict(sample)` 必须使用完整多通道特征。

## 数据与隐私边界

- 不保存完整 Pyrogram `Message` JSON、头像 file ID、群权限或其他与训练无关的客户端字段。
- D1 可保存审核所需的原始 Bio、显示名称和 username；模型不得使用 username。
- 用户分组摘要只用于防止离线评估泄漏，不作为特征，也不进入发布模型。
- 日志不得输出消息、Bio、姓名、username、Telegram ID、邀请链接或最近训练样本。
- 仍只有人工确认的 `reviewed` 标签可以训练。`matched_rule=invite_link_bio` 也不能代替人工确认。

## 重建与发布

开发验收使用全新的 D1 Schema 和重新生成的模型包。部署时先停止采集，再由维护者确认目标后重建 D1、执行最终 `001_create_samples.sql`、重新构建并批准候选模型，最后重建镜像和启动 Bot。本 Story 不自动删除远程数据库，也不自动部署模型。

