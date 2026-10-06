"""Entry point: `uv run studio serve`."""

from __future__ import annotations

import argparse
import socket

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
        # On Windows uvicorn's SO_REUSEADDR lets a second server bind the same port and
        # silently steal requests from the first one. Refuse to start instead.
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex((settings.host, settings.port)) == 0:
                raise SystemExit(f"port {settings.port} is already in use; is Studio running?")
        print(
            f"AI Bridge Studio: http://{settings.host}:{settings.port}  data: {settings.data_dir}"
        )
        uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
