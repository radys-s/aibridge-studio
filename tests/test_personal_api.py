from pathlib import Path

from fastapi.testclient import TestClient

from conftest import FakeGateway
from studio.api import create_app
from studio.config import Settings
from studio.personal import PersonalService
from studio.personal_vault import PersonalVault
from studio.protected_storage import PersonalStore
from test_personal import Gateway, Protector, Router

HEADERS = {"X-Studio": "1"}


def test_personal_api_approval_history_and_no_query_log(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Study.md").write_text("Geometry objective", encoding="utf-8")
    service = PersonalService(
        PersonalStore(tmp_path / "protected", Protector()),
        PersonalVault(vault),
        Router(),
        Gateway(),
        gateway_privacy_ready=lambda: True,
    )
    client = TestClient(
        create_app(
            Settings(data_dir=tmp_path / "data", vault_path=None),
            gateway=FakeGateway(),
            start_runner=False,
            personal_service=service,
        ),
        base_url="http://127.0.0.1",
    )
    assert client.post("/api/personal/sessions").status_code == 403
    assert (
        client.put(
            "/api/personal/policy", headers=HEADERS, json={"exclusions": [], "enabled": True}
        ).status_code
        == 200
    )
    session = client.post("/api/personal/sessions", headers=HEADERS).json()["session_id"]
    preview = client.post(
        f"/api/personal/sessions/{session}/preview",
        headers=HEADERS,
        json={"query": "How to learn?", "paths": ["Study.md"]},
    )
    assert preview.status_code == 200, preview.text
    approval = preview.json()["approval_id"]
    body = {"session_id": session}
    url = f"/api/personal/approvals/{approval}"
    assert client.post(url + "/answer", headers=HEADERS, json=body).status_code == 409
    assert client.post(url + "/approve", headers=HEADERS, json=body).status_code == 200
    assert client.post(url + "/answer", headers=HEADERS, json=body).status_code == 200
    assert (
        len(client.get(f"/api/personal/sessions/{session}", headers=HEADERS).json()["messages"])
        == 2
    )
    assert client.get(f"/api/personal/sessions/{session}").status_code == 403
    assert (
        client.get(
            "/api/personal/status", headers={**HEADERS, "Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.get("/api/personal/status", headers={**HEADERS, "Host": "evil.example"}).status_code
        == 403
    )
    assert client.get("/api/runs").json() == []
    assert client.post(url + "/answer", headers=HEADERS, json=body).status_code == 409


def test_personal_validation_never_echoes_sensitive_input(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            Settings(data_dir=tmp_path / "data", vault_path=None),
            gateway=FakeGateway(),
            start_runner=False,
        ),
        base_url="http://127.0.0.1",
    )
    secret = "PRIVATE_MARKER" + "x" * 4100
    response = client.post("/api/personal/search", headers=HEADERS, json={"query": secret})
    assert response.status_code == 422
    assert "PRIVATE_MARKER" not in response.text
    assert client.get("/api/personal/status", headers=HEADERS).json()["available"] is False
    assert client.get("/api/runs").status_code == 200
