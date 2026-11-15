"""交互试判最近构建的本地候选模型。"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .model_loader import LocalPredictor


def latest_candidate(root: Path) -> Path:
    manifests = (
        directory / "manifest.json"
        for directory in root.iterdir()
        if not directory.name.startswith(".") and directory.is_dir()
    )
    latest = max(
        (manifest for manifest in manifests if manifest.is_file()),
        key=lambda manifest: (manifest.stat().st_mtime_ns, manifest.parent.name),
        default=None,
    )
    if latest is None:
        raise FileNotFoundError(f"目录中没有候选模型：{root}")
    return latest.parent


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="交互试判最近构建的本地候选模型")
    parser.add_argument("--candidates", type=Path, default=Path("models/candidates"))
    args = parser.parse_args(argv)

    try:
        candidate = latest_candidate(args.candidates)
    except OSError as exc:
        parser.exit(1, f"无法选择候选模型：{exc}\n")

    model = LocalPredictor.from_path(candidate)
    if not model.available:
        parser.exit(1, f"候选模型加载失败：{candidate}\n")

    print(f"候选：{candidate}（{model.kind}）")
    print("每行输入一条消息，空行退出。")
    while True:
        try:
            message = input("文本 > ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not message.strip():
            return 0
        result = model.predict_text(message)
        score = "" if result.score is None else f"  分数：{result.score:.4f}"
        print(f"{result.decision}{score}")


if __name__ == "__main__":
    raise SystemExit(main())
