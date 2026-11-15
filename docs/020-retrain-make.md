# Story 020：合并人工重训 Make 入口

日常重训使用 `make retrain-spam-model`，顺序导出 D1 已审核样本并构建候选。移除拆分的 Make 入口与初始种子训练 Make 入口。Docker 直接调用既有初始模型 CLI，继续为无 D1 凭证的镜像构建打包默认模型。

## 验收条件

- 新 Make 目标先导出数据，再用同一路径构建候选。导出失败时停止。
- 默认构建相似度候选，指定 `CLASSIFIER=1` 时请求二分类候选。保留独立的人工批准入口和 Python CLI。
- Docker 初始模型使用相同的种子、CLI 参数与镜像路径，无需 D1 凭证。
- README 与运维手册使用新的 Make 入口。
