# Story 016：管理员转发消息直接标记 Spam

## 目标

允许唯一配置管理员把一条 Telegram 消息私聊转发给 Bot，并将该消息作为人工确认的
`manual/reviewed/spam` 样本直接写入 D1。样本使用转发来源而不是管理员本人作为作者。

## 范围

- 只接受 `SPAM_ADMIN_ID` 在 Bot 私聊中发送的真实转发消息。
- 保留转发消息的原始 text/caption、entities、媒体类型和可取得的原始时间。
- 展开 Kurigram 的结构化可见文字：Checklist、Poll、Contact、Venue/带地址的 Location、Game、Invoice、Giveaway、Story、WebPage，以及 Audio/Document/Video/Animation/Sticker 的可用文字元数据。
- 从 Kurigram `forward_origin` 提取已知用户、隐藏用户、群组或频道来源信息。
- 管理员只作为 `reviewed_by` 保存，不写入统一样本的 `user`。
- 写入成功后回复样本 ID；重复转发相同结构化样本时幂等返回原 ID。
- D1 失败时明确回复未保存，不伪装成功。

## 不在范围内

- 不删除管理员转发的消息，不封禁或限制管理员。
- 不把非管理员转发、普通私聊或群内转发自动标记为 Spam。
- 不查询原作者 Bio，不执行模型推理，不发送二次审核通知。
- 不新增 D1 字段、Migration 或环境变量。

## 验收条件

1. 已知用户转发会保存原作者 ID、username、姓名和 premium 状态，样本中不出现管理员资料。
2. 隐藏用户转发保存 Telegram 提供的显示名；频道/群来源保存可取得的 ID、username、标题和作者签名。
3. text/caption 和原始 Unicode 被保留；结构化内容按显示顺序展开，已有 entities 会重算 UTF-16 offset。
4. 只有配置管理员能触发，且功能关闭或 D1 不可用时不会写入。
5. 样本直接为 `source=manual/status=reviewed/label=spam`，`reviewed_by=SPAM_ADMIN_ID`。
6. 相同结构化转发重复提交不产生重复记录。
7. 既有关键词秒删、群消息采集、人工审核和模型建议路径保持不变。
8. 没有文字内容的 Photo、Voice、VideoNote、Dice 和 PaidMedia 不伪造占位文本，继续拒绝写入。
