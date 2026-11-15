# Story 022：交互试判本地候选模型

## 实际实现

- `spamdet/try_model.py` 按候选 manifest 修改时间选择最新目录，时间相同则按目录名确定顺序。通过既有 `LocalPredictor` 加载相似度或二分类候选。
- `Makefile` 增加 `test-model`，复用 `PYTHON` 和 `SPAM_CANDIDATES`。Python CLI 为 `python -m spamdet.try_model --candidates <目录>`。
- `README.md`、Story 和进度表添加入口与说明。

## 验证及交接

- 用现有本地二分类候选实测 `make test-model PYTHON=venv/bin/python`，确认可加载、试判并以空行退出。
- 用临时目录验证日期排序、忽略隐藏构建目录、无候选和加载失败提示。122 项既有测试及 `git diff --check` 通过。
- 此入口按行提交正文，Bio 和显示名称为空。判定仅用于本机试用，样本输入不写入文件。

## Git Commit / PR

尚未提交。
