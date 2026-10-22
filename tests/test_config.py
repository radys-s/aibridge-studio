"""config.toml in the data dir: values below environment variables, keys never from files."""

from __future__ import annotations

from pathlib import Path

import pytest

from studio.config import Settings


def test_defaults_have_no_memory_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("STUDIO_VAULT", raising=False)
    settings = Settings.from_env()
    assert settings.vault_path is None and settings.vault() is None


def test_file_config_and_env_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    notes = tmp_path / "notes"
    (notes / "Projects").mkdir(parents=True)
    (tmp_path / "config.toml").write_text(
        'port = 9001\nomniroute_url = "http://10.0.0.2:20128"\n'
        f'[memory]\npath = "{notes.as_posix()}"\nprojects = "Projects"\ninclude = ["Projects"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("STUDIO_DATA_DIR", str(tmp_path))
    for name in ("STUDIO_VAULT", "STUDIO_PORT", "STUDIO_OMNIROUTE_URL"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_env()
    assert settings.port == 9001
    assert settings.omniroute_url == "http://10.0.0.2:20128"
    vault = settings.vault()
    assert vault is not None and vault.layout.projects == "Projects"
    monkeypatch.setenv("STUDIO_PORT", "9100")
    assert Settings.from_env().port == 9100


def test_broken_config_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "config.toml").write_text("port = = 1", encoding="utf-8")
    monkeypatch.setenv("STUDIO_DATA_DIR", str(tmp_path))
    with pytest.raises(SystemExit):
        Settings.from_env()


def test_memory_folder_chosen_in_settings(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from studio.api import create_app
    from studio.testing import ScriptedGateway

    notes = tmp_path / "notes"
    (notes / "projects").mkdir(parents=True)
    (notes / "projects" / "Calc.md").write_text("# Calc", encoding="utf-8")
    app = create_app(
        Settings(data_dir=tmp_path / "d"), gateway=ScriptedGateway(), start_runner=False
    )
    with TestClient(app, headers={"X-Studio": "1"}) as c:
        assert c.get("/api/vault").json()["available"] is False
        assert c.put("/api/vault/path", json={"path": "relative/x"}).status_code == 422
        body = c.put("/api/vault/path", json={"path": str(notes)}).json()
        assert body["available"] is True and body["projects"] == ["projects/Calc.md"]
        assert c.get("/api/settings").json()["memory_path"] == str(notes.resolve())
        # Not the home folder or a drive root: their README and notes/ would be shared.
        for broad in (Path.home(), Path(notes.anchor)):
            assert c.put("/api/vault/path", json={"path": str(broad)}).status_code == 422
        assert c.put("/api/vault/path", json={"path": None}).json()["available"] is False
