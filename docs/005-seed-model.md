# Story 005：用不足 100 条 Spam 训练初始模型

## 依赖
Story 001；按顺序开发时安排在 Story 004 后。

## 开发任务
- `spam.txt` 为 UTF-8，每行一条已确认 Spam；保留原始 Unicode；跳过空行和完全重复行，元数据中保留原文。
- 实现确定性预处理：原文与归一化文本分离；NFKC、可配置的零宽控制符处理、空白处理、URL/mention 标记化；不要激进替换中文字符。
- 训练字符 n-gram TF-IDF（例如 2–5 gram）与最近 Spam 的余弦相似度；根据样本量调整；测试短文本和 Unicode 混淆。
- 保存向量器、索引和带版本的 manifest，提供可复现的训练 CLI；只加载本地可信模型文件，说明序列化安全。预测接口遵循 `CONTRACTS.md`。
- **不能输出二分类 `ham`，不能把相似度称为概率**。训练语料为空或不可用时返回 `unavailable`。
- 测试结果可复现性、归一化、Unicode、零宽字符、空/短消息、保存和加载。

## 验收标准
- 在 macOS M1 Pro 或 Linux CPU 离线执行 `python ... --input spam.txt --output ...` 可生成模型。
- CLI 演示显示最近样本、相似度及 `spam_like` / `unknown`。
- 无自动删除行为，也不依赖 D1。
- 填写 `docs/notes/005-notes.md` 并更新进度。
