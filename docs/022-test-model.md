# Story 022：交互试判本地候选模型

## 验收条件

- `make test-model` 默认从 `models/candidates/` 选择 manifest 修改时间最新的候选，跳过隐藏构建目录。
- 启动时显示选中的候选和类型，每行输入一条正文，返回判定和可用分数。空行、EOF 或 Ctrl+C 退出。
- 候选缺失或加载失败时给出错误。试判只读取本地文件，无需 D1 或 Telegram 凭证。
