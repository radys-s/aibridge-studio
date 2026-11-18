"""Explicit local administrative setup using OmniRoute's legitimate CLI authentication.

Never imported by the runtime. Machine-token output is captured only in process memory.
No inference credential reuse, auth database changes, or password bypass.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from studio.admin_import import ImportFailure
from studio.protected_storage import WindowsDPAPI


def management_token(path: Path) -> str:
    try:
        return WindowsDPAPI().unprotect(path.read_bytes()).decode("utf-8")
    except Exception:
        raise ImportFailure("protected management credential unavailable") from None


def bootstrap_management(
    package_dir: Path,
    node: Path,
    protected_path: Path,
    url: str = "http://127.0.0.1:20128",
) -> dict[str, object]:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
    ):
        raise ImportFailure("management bootstrap requires loopback HTTP")
    if protected_path.exists():
        token = management_token(protected_path)
        with httpx.Client(
            base_url=url,
            headers={"Authorization": f"Bearer {token}"},
            trust_env=False,
            follow_redirects=False,
            timeout=15,
        ) as client:
            if client.get("/api/providers").is_success:
                return {"ready": True, "created": False}
        raise ImportFailure("existing management credential rejected; no replacement created")
    helper = package_dir / "bin/cli/utils/cliToken.mjs"
    if not helper.is_file() or not node.is_file() or not node.is_absolute():
        raise ImportFailure("installed local CLI helper unavailable")
    script = (
        "import {getCliToken} from "
        + json.dumps(helper.resolve().as_uri())
        + "; process.stdout.write(await getCliToken());"
    )
    try:
        result = subprocess.run(
            [str(node), "--input-type=module", "-e", script],
            capture_output=True,
            timeout=15,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        cli_token = result.stdout.decode().strip()
    except Exception:
        raise ImportFailure("local CLI authentication unavailable") from None
    if not cli_token:
        raise ImportFailure("local CLI authentication unavailable")
    # Confirm DPAPI works before creating any persistent gateway credential.
    protector = WindowsDPAPI()
    protector.protect(b"bootstrap-preflight")
    with httpx.Client(
        base_url=url,
        headers={"x-omniroute-cli-token": cli_token},
        trust_env=False,
        follow_redirects=False,
        timeout=15,
    ) as client:
        try:
            if not client.get("/api/providers").is_success:
                raise ImportFailure("local administrative authentication rejected")
            response = client.post(
                "/api/keys",
                json={"name": "AI Bridge Studio management", "scopes": ["manage"], "noLog": True},
            )
            if not response.is_success:
                raise ImportFailure("management credential creation rejected")
            payload = response.json()
            record = payload.get("key") if isinstance(payload.get("key"), dict) else payload
            key = record.get("key")
            if not isinstance(key, str) or not key or record.get("scopes") != ["manage"]:
                raise ImportFailure("management credential response incompatible")
            protected_path.parent.mkdir(parents=True, exist_ok=True)
            with protected_path.open("xb") as stream:
                stream.write(protector.protect(key.encode("utf-8")))
            return {"ready": True, "created": True, "scope": "manage", "storage": "Windows DPAPI"}
        except (httpx.HTTPError, ValueError, OSError):
            raise ImportFailure(
                "local management setup failed; credentials not displayed"
            ) from None
