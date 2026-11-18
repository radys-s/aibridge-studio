from pathlib import Path

from fastapi.testclient import TestClient

from conftest import FakeGateway
from studio.api import create_app
from studio.config import Settings

HEADERS = {"X-Studio": "1"}


def test_memory_api_manual_approval_and_csrf(tmp_path: Path, repo: Path) -> None:
    app = create_app(
        Settings(data_dir=tmp_path / "data", vault_path=None),
        gateway=FakeGateway(),
        start_runner=False,
    )
    client = TestClient(app)
    assert client.post("/api/memory/projects", json={"path": str(repo)}).status_code == 403
    p = client.post("/api/memory/projects", headers=HEADERS, json={"path": str(repo)}).json()
    assert p["id"]
    result = client.post(
        f"/api/memory/projects/{p['id']}/candidates",
        headers=HEADERS,
        json={
            "payload": {
                "title": "Fixture claim",
                "content": "Geometry works",
                "evidence": [{"type": "user"}],
            }
        },
    )
    assert result.status_code == 200, result.text
    candidate = result.json()
    assert client.get(f"/api/memory/projects/{p['id']}/items").json() == []
    approved = client.post(
        f"/api/memory/candidates/{candidate['id']}/approve", headers=HEADERS, json={}
    )
    assert approved.status_code == 200, approved.text
    items = client.get(f"/api/memory/projects/{p['id']}/items").json()
    assert items[0]["payload"]["content"] == "Geometry works"
    packet = client.post(
        f"/api/memory/projects/{p['id']}/context", headers=HEADERS, json={"objective": "Geometry"}
    )
    assert packet.status_code == 200
    assert "Geometry works" in packet.json()["text"]


def test_memory_api_rejects_arbitrary_workspace(tmp_path: Path, repo: Path) -> None:
    app = create_app(
        Settings(data_dir=tmp_path / "data", vault_path=None),
        gateway=FakeGateway(),
        start_runner=False,
    )
    client = TestClient(app)
    p = client.post("/api/memory/projects", headers=HEADERS, json={"path": str(repo)}).json()
    response = client.post(
        f"/api/memory/projects/{p['id']}/candidates",
        headers=HEADERS,
        json={"run_id": "notaccepted", "payload": {"title": "x", "content": "x"}},
    )
    assert response.status_code == 409
