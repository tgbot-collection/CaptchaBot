"""按需启动的本机 D1 样本审核工具。"""

from __future__ import annotations

import argparse
import asyncio
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import threading
import webbrowser

from dotenv import load_dotenv

from .config import ConfigError, SpamLearningConfig
from .d1 import D1Client, D1Error, D1NotFoundError

ROOT = Path(__file__).resolve().parent.parent
PAGE_SIZE = 50
MAX_ID = 2**63 - 1


class ReviewHandler(BaseHTTPRequestHandler):
    timeout = 4

    def __init__(self, *args, config: SpamLearningConfig, page: bytes):
        self.config = config
        self.page = page
        super().__init__(*args)

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self.server.server_port}"

    def log_message(self, *_args) -> None:
        # 请求内容和样本数据均不写入日志。
        pass

    def respond(self, status: int, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, status: int, payload: dict) -> None:
        self.respond(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if self.headers.get("Host") != self.host or self.path != "/":
            self.send_error(404)
            return
        self.respond(200, self.page, "text/html; charset=utf-8")

    def do_POST(self) -> None:
        if (
            self.headers.get("Host") != self.host
            or self.headers.get("Origin") != f"http://{self.host}"
            or self.headers.get_content_type() != "application/json"
        ):
            self.send_json(403, {"error": "请从本机审核页面发起请求。"})
            return
        if self.path not in ("/list", "/action"):
            self.send_json(404, {"error": "未知接口。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError
            result = asyncio.run(self.run_operation(payload))
        except ValueError:
            self.send_json(400, {"error": "请求参数无效。"})
        except D1NotFoundError:
            self.send_json(409, {"error": "有样本已不存在，整批未标注。请刷新列表。"})
        except D1Error:
            self.send_json(502, {"error": "D1 操作未获确认。请刷新列表核对结果。"})
        else:
            self.send_json(200, result)

    async def run_operation(self, payload: dict) -> dict:
        async with D1Client.from_config(self.config) as client:
            if self.path == "/list":
                rows, total, filtered_total = await client.list_review_samples(
                    payload.get("status"), payload.get("before"), PAGE_SIZE + 1
                )
                return {
                    "rows": rows[:PAGE_SIZE], "has_more": len(rows) > PAGE_SIZE,
                    "total": total, "filtered_total": filtered_total,
                }

            action = payload.get("action")
            ids = payload.get("ids")
            if action not in ("delete", "spam", "ham") or not isinstance(ids, list) or not 0 < len(ids) <= PAGE_SIZE:
                raise ValueError
            if any(not isinstance(value, str) or not value.isascii() or not value.isdecimal()
                   or len(value) > 19 or not 0 < int(value) <= MAX_ID for value in ids):
                raise ValueError
            sample_ids = list(dict.fromkeys(map(int, ids)))
            if action == "delete":
                await client.delete_review_samples(sample_ids)
            else:
                await client.label_samples(sample_ids, action, self.config.require_admin_id())
            return {"count": len(sample_ids)}


def main() -> None:
    parser = argparse.ArgumentParser(description="启动本机 D1 样本审核网页")
    parser.add_argument("--env-file", type=Path, help="指定 D1 配置文件")
    parser.add_argument("--no-browser", action="store_true", help="仅打印本机地址")
    args = parser.parse_args()
    if args.env_file is not None and not args.env_file.is_file():
        parser.error("指定的配置文件不存在")
    load_dotenv(args.env_file or ROOT / ".env")
    if args.env_file is None:
        load_dotenv(ROOT / "env/captcha.env")
    try:
        config = SpamLearningConfig.from_env()
        config.require_d1()
        config.require_admin_id()
    except ConfigError as exc:
        parser.error(str(exc))

    handler = partial(ReviewHandler, config=config, page=(ROOT / "review.html").read_bytes())
    with HTTPServer(("127.0.0.1", 0), handler) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"审核网页 {url}  按 Ctrl+C 退出。", flush=True)
        if not args.no_browser:
            threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
