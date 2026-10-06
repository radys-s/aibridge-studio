"""Entry point: `uv run studio serve`."""

from __future__ import annotations

import argparse

import uvicorn

from studio.api import create_app
from studio.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="studio")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="start API + UI on 127.0.0.1")
    args = parser.parse_args()
    if args.cmd == "serve":
        settings = Settings.from_env()
        print(
            f"AI Bridge Studio: http://{settings.host}:{settings.port}  data: {settings.data_dir}"
        )
        uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
