"""One-time, local-only credential transfer. Never imported by Studio runtime.

Source format: Prizma backend/app/crypto.py and models/provider_key.py.
Destination contract: installed OmniRoute createProviderSchema and requireManagementAuth.
Use `python -m studio.admin_import --source-db PATH --master-key-file PATH`.
Management token comes only from OMNIROUTE_MANAGEMENT_TOKEN, never inference credentials.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Other Prizma providers have paid, unknown, or training-enabled tiers. No blanket import.
ELIGIBLE = frozenset({"groq"})


class ImportFailure(RuntimeError):
    """Fixed safe diagnostic, never upstream error bodies or credential values."""


def master_key(path: Path) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError:
        raise ImportFailure("master key unavailable; source unchanged") from None
    if len(raw) == 32:
        return raw
    decoders: tuple[Callable[[bytes], bytes], ...] = (
        lambda v: base64.b64decode(v, validate=True),
        lambda v: bytes.fromhex(v.decode()),
    )
    for decode in decoders:
        try:
            decoded = decode(raw.strip())
        except (ValueError, UnicodeError):
            continue
        if len(decoded) == 32:
            return decoded
    raise ImportFailure("invalid master key; source unchanged")


class CredentialImporter:
    def __init__(self, source_db: Path, key_file: Path, client: httpx.Client) -> None:
        host = urlsplit(str(client.base_url))
        if host.hostname not in {"127.0.0.1", "localhost", "::1"} or host.scheme != "http":
            raise ImportFailure("destination must be loopback HTTP")
        if not client.headers.get("Authorization", "").startswith("Bearer "):
            raise ImportFailure("dedicated management credential required")
        self.source_db, self.key_file, self.client = source_db, key_file, client

    def _request(self, method: str, body: dict[str, str] | None = None) -> httpx.Response:
        try:
            response = self.client.request(method, "/api/providers", json=body)
        except httpx.HTTPError:
            raise ImportFailure("management gateway unavailable") from None
        if not response.is_success:
            raise ImportFailure("management authentication or request failed")
        return response

    def _destinations(self) -> set[str]:
        try:
            data = self._request("GET").json()
            rows = data.get("providers") if isinstance(data, dict) else None
            if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                raise ValueError
            return {str(r.get("provider", "")) for r in rows}
        except (ValueError, TypeError):
            raise ImportFailure("incompatible management response") from None

    def run(self, execute: bool = False) -> list[dict[str, object]]:
        destinations = self._destinations()
        try:
            with sqlite3.connect(self.source_db.resolve().as_uri() + "?mode=ro", uri=True) as db:
                rows = db.execute("SELECT credential_id FROM providerkey").fetchall()
        except sqlite3.Error:
            raise ImportFailure("source database unavailable or incompatible") from None
        report: list[dict[str, object]] = []
        for (provider,) in rows:
            # Only provider identifiers are reportable. Ignore malformed/unrecognized rows.
            if not isinstance(provider, str) or not re.fullmatch(r"[a-z0-9_-]{1,64}", provider):
                continue
            eligible = provider in ELIGIBLE
            status = (
                "ineligible"
                if not eligible
                else "duplicate"
                if provider in destinations
                else "ready"
            )
            if execute and status == "ready":
                try:
                    override = os.environ.get("PRIZMA_KEY_" + provider.upper().replace("-", "_"))
                    if override:
                        secret = override.strip()
                    else:
                        with sqlite3.connect(
                            self.source_db.resolve().as_uri() + "?mode=ro", uri=True
                        ) as db:
                            nonce, ciphertext = db.execute(
                                "SELECT nonce,ciphertext FROM providerkey WHERE credential_id=?",
                                (provider,),
                            ).fetchone()
                        secret = (
                            AESGCM(master_key(self.key_file))
                            .decrypt(bytes(nonce), bytes(ciphertext), None)
                            .decode()
                        )
                except ImportFailure:
                    raise
                except Exception:
                    raise ImportFailure(
                        "source integrity/decryption failed; source unchanged"
                    ) from None
                if not secret.strip() or len(secret) > 100_000:
                    raise ImportFailure("invalid source credential")
                # Recheck immediately before write. Never modify an existing connection.
                if provider in self._destinations():
                    status = "duplicate"
                else:
                    self._request(
                        "POST", {"provider": provider, "name": "Prizma import", "apiKey": secret}
                    )
                    status = "imported_unverified"
                    destinations.add(provider)
                del secret
            report.append(
                {
                    "provider": provider,
                    "source_available": True,
                    "source_shadowed": bool(
                        os.environ.get("PRIZMA_KEY_" + provider.upper().replace("-", "_"))
                    ),
                    "eligible": eligible,
                    "status": status,
                }
            )
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview or import eligible Prizma credentials")
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--master-key-file", type=Path, required=True)
    parser.add_argument("--gateway", default="http://127.0.0.1:20128")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--management-token-file", type=Path)
    args = parser.parse_args()
    token = os.environ.get("OMNIROUTE_MANAGEMENT_TOKEN")
    if not token and args.management_token_file:
        from studio.admin_bootstrap import management_token

        token = management_token(args.management_token_file)
    if not token:
        parser.exit(1, "dedicated OMNIROUTE_MANAGEMENT_TOKEN required\n")
    try:
        with httpx.Client(
            base_url=args.gateway,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            print(
                json.dumps(
                    CredentialImporter(args.source_db, args.master_key_file, client).run(
                        args.execute
                    )
                )
            )
    except ImportFailure as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
