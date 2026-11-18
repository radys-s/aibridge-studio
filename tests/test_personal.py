from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from studio.config import ModelSpec
from studio.gateway import ChatResult, ProviderError
from studio.personal import PersonalService
from studio.personal_vault import PersonalVault
from studio.protected_storage import PersonalStore, ProtectedStorageUnavailable, WindowsDPAPI
from studio.sandbox import Denied


class Protector:
    def __init__(self) -> None:
        self.cipher = AESGCM(AESGCM.generate_key(bit_length=256))

    def protect(self, data: bytes) -> bytes:
        return self.cipher.encrypt(b"x" * 12, data, None)

    def unprotect(self, data: bytes) -> bytes:
        return self.cipher.decrypt(b"x" * 12, data, None)


class Router:
    model = ModelSpec("groq/openai/gpt-oss-120b", frozenset({"lead"}), privacy="no_train")
    blocked = False

    def choose_personal(self, model_id: str | None = None) -> ModelSpec:
        return self.assert_personal_eligible(model_id or self.model.id)

    def assert_personal_eligible(self, model_id: str) -> ModelSpec:
        if self.blocked or model_id != self.model.id:
            raise Denied("Policy unavailable")
        return self.model


class Gateway:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []
        self.fail = False

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout_s: float | None = None,
    ) -> ChatResult:
        assert tools is None  # Personal mode never exposes worker commands/tools.
        self.calls.append(messages)
        if self.fail:
            raise ProviderError("Private echoed contents must not enter the user error")
        return ChatResult(
            {"content": "Next step: practice thoughtfully."}, "stop", model, "gpt-oss-120b", "groq"
        )

    def health(self) -> dict[str, Any]:
        return {}

    def models(self, fresh: bool = False) -> set[str] | None:
        return {Router.model.id}


@pytest.fixture
def service(tmp_path: Path) -> PersonalService:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Study.md").write_text(
        "Unique personal study objective: learn geometry", encoding="utf-8"
    )
    obj = PersonalService(
        PersonalStore(tmp_path / "protected", Protector()),
        PersonalVault(vault),
        Router(),
        Gateway(),
        gateway_privacy_ready=lambda: True,
    )
    obj.configure_exclusions([], enabled=True)
    return obj


def test_approval_single_use_and_encrypted_restart(service: PersonalService) -> None:
    session = service.create_session()["session_id"]
    preview = service.preview(session, "How to learn?", ["Study.md"])
    approval = preview["approval_id"]
    with pytest.raises(Denied, match="Explicit"):
        service.answer(approval, session)
    service.approve(approval, session)
    assert service.answer(approval, session)["answer"].startswith("Next step")
    with pytest.raises(Denied, match="once"):
        service.answer(approval, session)
    for file in service.store.root.iterdir():
        assert b"Unique personal" not in file.read_bytes()
        assert b"How to learn" not in file.read_bytes()
    assert service.store.get(approval)["state"] == "answered"  # type: ignore[index]
    assert not (service.vault.root / "personal.sqlite3").exists()


def test_full_session_refuses_before_any_cloud_call(service: PersonalService) -> None:
    session = service.create_session()["session_id"]
    service.store.put(
        session, {"kind": "session", "messages": [{"content": "x" * 1_900_000}], "claims": []}
    )
    approval = service.preview(session, "Geometry?", ["Study.md"])["approval_id"]
    service.approve(approval, session)
    with pytest.raises(Denied, match="history is full"):
        service.answer(approval, session)
    assert not service.gateway.calls  # type: ignore[attr-defined]
    assert service.store.get(approval)["state"] == "approved"  # type: ignore[index]


def test_same_session_answers_are_single_flight(service: PersonalService) -> None:
    session = service.create_session()["session_id"]
    first = service.preview(session, "First?", ["Study.md"])["approval_id"]
    second = service.preview(session, "Second?", ["Study.md"])["approval_id"]
    service.approve(first, session)
    service.approve(second, session)
    started, finish = threading.Event(), threading.Event()
    original = service.gateway.chat

    def chat(*args: Any, **kwargs: Any) -> ChatResult:
        started.set()
        assert finish.wait(5)
        return original(*args, **kwargs)

    service.gateway.chat = chat  # type: ignore[method-assign]
    thread = threading.Thread(target=lambda: service.answer(first, session))
    thread.start()
    assert started.wait(5)
    try:
        with pytest.raises(Denied, match="already running"):
            service.answer(second, session)
        assert service.store.get(second)["state"] == "approved"  # type: ignore[index]
    finally:
        finish.set()
        thread.join(5)
    assert len(service.gateway.calls) == 1  # type: ignore[attr-defined]


def test_stale_source_policy_session_and_expiry(service: PersonalService) -> None:
    session = service.create_session()["session_id"]
    preview = service.preview(session, "Geometry?", ["Study.md"])
    approval = preview["approval_id"]
    with pytest.raises(Denied, match="session"):
        service.approve(approval, str(uuid4()))
    (service.vault.root / "Study.md").write_text("Changed contents", encoding="utf-8")
    with pytest.raises(Denied, match="changed"):
        service.approve(approval, session)
    record = service.store.get(approval)
    assert record
    record["expires_at"] = 0
    service.store.put(approval, record)
    with pytest.raises(Denied, match="expired"):
        service.approve(approval, session)
    assert not service.gateway.calls  # type: ignore[attr-defined]


def test_policy_change_rejects_and_gateway_failure_never_falls_back(
    service: PersonalService,
) -> None:
    session = service.create_session()["session_id"]
    preview = service.preview(session, "Geometry?", ["Study.md"])
    service.approve(preview["approval_id"], session)
    service.router.blocked = True  # type: ignore[attr-defined]
    with pytest.raises(Denied):
        service.answer(preview["approval_id"], session)
    service.router.blocked = False  # type: ignore[attr-defined]
    service.gateway.fail = True  # type: ignore[attr-defined]
    with pytest.raises(Denied, match="No fallback") as exc:
        service.answer(preview["approval_id"], session)
    assert "Private echoed" not in str(exc.value)
    with pytest.raises(Denied, match="once"):
        service.answer(preview["approval_id"], session)
    assert len(service.gateway.calls) == 1  # type: ignore[attr-defined]


def test_vault_rejects_private_secrets_and_traversal(tmp_path: Path) -> None:
    vault = PersonalVault(tmp_path)
    with pytest.raises(Denied, match="exclusions"):
        vault.search("geometry")
    vault.configure((), enabled=True)
    for name, contents in [
        ("Study.md", "Geometry"),
        ("Private.md", "---\nprivate: true\n---\nGeometry"),
        ("Late.md", "Geometry" + "x" * 41000 + "\napi_key = 'gsk_" + "A" * 45 + "'"),
        ("Recorder.md", "Geometry"),
    ]:
        (tmp_path / name).write_text(contents, encoding="utf-8")
    assert [item["path"] for item in vault.search("Geometry")] == ["Study.md"]
    with pytest.raises(Denied):
        vault.read("../escape.md")
    with pytest.raises(Denied):
        vault.read("Private.md")


def test_ciphertext_tamper_and_wrong_key_fail_closed(tmp_path: Path) -> None:
    protector = Protector()
    store = PersonalStore(tmp_path, protector)
    record_id = str(uuid4())
    store.put(record_id, {"claim": "Personal objective"})
    assert PersonalStore(tmp_path, protector).get(record_id) == {"claim": "Personal objective"}
    with pytest.raises(ProtectedStorageUnavailable):
        PersonalStore(tmp_path, Protector())
    with sqlite3.connect(tmp_path / "personal.sqlite3") as conn:
        conn.execute("UPDATE personal_record SET ciphertext=?", (b"corrupt",))
    with pytest.raises(ProtectedStorageUnavailable, match="authentication"):
        store.get(record_id)


def test_encrypted_history_claim_reuse_and_gateway_guard(service: PersonalService) -> None:
    session = service.create_session()["session_id"]
    claim = service.propose_claim(session, "Practice geometry each morning")
    assert service.history(session)["claims"] == []
    service.approve_claim(claim["claim_id"], session)
    preview = service.preview(session, "Suggest next step", [])
    assert preview["snippets"][0]["text"] == claim["text"]
    service.approve(preview["approval_id"], session)
    service.answer(preview["approval_id"], session)
    assert len(service.history(session)["messages"]) == 2
    service.gateway_privacy_ready = lambda: False
    with pytest.raises(Denied, match="retention"):
        service.preview(session, "Suggest next step", [])
    for file in service.store.root.iterdir():
        assert b"Practice geometry" not in file.read_bytes()
    assert service.history(session)["claims"][0]["text"] == claim["text"]


def test_memory_index_refresh_and_disable_clears(service: PersonalService) -> None:
    assert service.search("geometry")
    (service.vault.root / "Study.md").write_text("Fresh algebra objective", encoding="utf-8")
    assert not service.search("geometry")
    assert service.search("algebra")
    service.configure_exclusions(["Study.md"], enabled=True)
    assert not service.search("algebra")
    service.configure_exclusions([], enabled=False)
    assert service.vault._index.execute("SELECT count(*) FROM notes").fetchone()[0] == 0


@pytest.mark.skipif(os.name != "nt", reason="Real DPAPI needs Windows")
def test_real_windows_dpapi_smoke(tmp_path: Path) -> None:
    protected = WindowsDPAPI()
    assert (
        protected.unprotect(protected.protect(b"synthetic fixture only"))
        == b"synthetic fixture only"
    )
    record_id = str(uuid4())
    PersonalStore(tmp_path).put(record_id, {"fixture": "synthetic personal smoke"})
    assert PersonalStore(tmp_path).get(record_id) == {"fixture": "synthetic personal smoke"}
