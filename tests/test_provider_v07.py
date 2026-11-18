import os
import sqlite3
import time
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from studio.admin_import import CredentialImporter, ImportFailure, master_key
from studio.config import ModelSpec
from studio.db import Database
from studio.routing import ModelRouter
from studio.sandbox import secret_findings


def test_billing_unknown_and_starter_are_blocked(tmp_path: Path) -> None:
    router = ModelRouter(Database(tmp_path / "d.db"), ())
    assert (
        router.blocked(ModelSpec("groq/not-in-catalog", frozenset(), privacy="no_train"))
        == "billing_unknown"
    )
    assert (
        router.blocked(
            ModelSpec("ollamacloud/x", frozenset(), privacy="no_train", billing="starter_credits")
        )
        == "starter_eligibility_unverified"
    )
    assert (
        router.blocked(
            ModelSpec("ovh/x", frozenset(), privacy="no_train", billing="anonymous_free")
        )
        == "anonymous_route_unverified"
    )


def test_anonymous_limit_is_short_but_quota_is_long(tmp_path: Path) -> None:
    router = ModelRouter(Database(tmp_path / "d.db"), ())
    router.report_failure("ovh/x", "provider_error", 403, "rate limit", 30)
    assert 20 < router.db.cooldowns()["ovh/x"] - time.time() < 40
    router.report_failure("groq/x", "provider_error", 403, "credits exhausted", 1)
    assert router.db.cooldowns()["groq/x"] - time.time() > 86000


def test_literal_passwords_but_not_normal_code() -> None:
    for text in (
        'password="correct horse battery staple"',
        "password=mysecretpass",
        '{"password":"a long password"}',
    ):
        assert secret_findings(text)
    for text in (
        "password = hash_password(raw)",
        "api_key=settings.api_key",
        "password=raw_password",
    ):
        assert not secret_findings(text)


def source(tmp_path: Path) -> tuple[Path, Path]:
    key = os.urandom(32)
    nonce = os.urandom(12)
    keyfile = tmp_path / "master.key"
    keyfile.write_bytes(key)
    database = tmp_path / "source.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE providerkey(credential_id TEXT, nonce BLOB, ciphertext BLOB)")
        for provider in ("groq", "nvidia"):
            db.execute(
                "INSERT INTO providerkey VALUES(?,?,?)",
                (provider, nonce, AESGCM(key).encrypt(nonce, b"fixture-token", None)),
            )
    return database, keyfile


def test_import_preview_execute_and_no_overwrite(tmp_path: Path) -> None:
    database, keyfile = source(tmp_path)
    before = database.read_bytes()
    posted: list[str] = []
    providers: list[dict[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(200, json={"providers": providers})
        posted.append(req.content.decode())
        providers.append({"provider": "groq"})
        return httpx.Response(201, json={"provider": "groq"})

    with httpx.Client(
        base_url="http://127.0.0.1:20128",
        headers={"Authorization": "Bearer management-fixture"},
        transport=httpx.MockTransport(handler),
    ) as client:
        importer = CredentialImporter(database, keyfile, client)
        assert importer.run()[0]["status"] == "ready"
        assert not posted
        assert importer.run(True)[0]["status"] == "imported_unverified"
        assert importer.run(True)[0]["status"] == "duplicate"
        assert len(posted) == 1
        assert "fixture-token" not in str(importer.run())
    assert database.read_bytes() == before


def test_missing_wrong_master_and_auth_errors_never_echo(tmp_path: Path) -> None:
    with pytest.raises(ImportFailure, match="unavailable"):
        master_key(tmp_path / "missing")
    assert not (tmp_path / "missing").exists()
    database, keyfile = source(tmp_path)
    keyfile.write_bytes(os.urandom(32))
    with (
        httpx.Client(
            base_url="http://localhost:20128",
            headers={"Authorization": "Bearer fixture"},
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"providers": []})),
        ) as client,
        pytest.raises(ImportFailure, match="integrity/decryption"),
    ):
        CredentialImporter(database, keyfile, client).run(True)
    with httpx.Client(
        base_url="http://localhost:20128",
        headers={"Authorization": "Bearer fixture"},
        transport=httpx.MockTransport(lambda _: httpx.Response(401, text="secret-leak")),
    ) as client:
        with pytest.raises(ImportFailure) as error:
            CredentialImporter(database, keyfile, client).run()
        assert "secret-leak" not in str(error.value)
