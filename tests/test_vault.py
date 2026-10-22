"""Memory folder access policy: only non-personal notes leave the machine; writes need approval."""

from __future__ import annotations

from pathlib import Path

import pytest

from studio.sandbox import Denied
from studio.vault import Layout, Vault, redact


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "memory"
    files = {
        "README.md": "# Index\n[[projects/Calc]]",
        "private/Kdo jsem.md": "osobní věci",
        "chats/chat.md": "obsah chatu",
        "projects/Calc.md": (
            "# Calc\nRepo: C:\\nikde\\calc\nStav: chybí variance()\nKontakt jan@example.com, 777 123 456"
        ),
        "projects/Tajne.md": "---\nprivate: true\n---\nnic",
        "projects/Tagged.md": "---\ntags:\n  - projekt\n  - osobni\n---\nnic",
        "projects/Klic.md": "api_key = abcdefgh12345678",
        "projects/secret plans.md": "# Recorder\nnahrávky",
        "handoffs/2026-10-06 claude → codex - calc.md": (
            "---\ntype: handoff\nstatus: open\n---\n# calc\nDodělat variance"
        ),
        "handoffs/stare.md": "---\nstatus: done\n---\nhotovo",
        ".obsidian/workspace.md": "x",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Vault(root)


def test_only_shared_folders_are_listed(vault: Vault) -> None:
    notes = vault.notes()
    assert "projects/Calc.md" in notes and "README.md" in notes
    assert not any(n.startswith(("private", "chats", ".obsidian")) for n in notes)
    assert not any("secret" in n.lower() for n in notes)  # excluded words never leave


@pytest.mark.parametrize(
    "rel",
    [
        "private/Kdo jsem.md",
        "chats/chat.md",
        "projects/Tajne.md",
        "projects/Tagged.md",
        "projects/Klic.md",
        "projects/secret plans.md",
        "../outside.md",
        "projects/../private/Kdo jsem.md",
    ],
)
def test_personal_secret_and_escaping_notes_are_refused(vault: Vault, rel: str) -> None:
    with pytest.raises(Denied):
        vault.read(rel)
    assert vault.reads == []


def test_read_redacts_contacts_and_records_the_read(vault: Vault) -> None:
    note = vault.read("projects/Calc.md")
    assert "jan@example.com" not in note.text and "777 123 456" not in note.text
    assert "[e-mail]" in note.text and "[telefon]" in note.text
    assert vault.reads == ["projects/Calc.md"]
    assert redact("2026-10-06 commit 6aa093f") == "2026-10-06 commit 6aa093f"


def test_search_and_handoffs(vault: Vault) -> None:
    hits = vault.search("variance")
    assert {h["path"] for h in hits} == {
        "projects/Calc.md",
        "handoffs/2026-10-06 claude → codex - calc.md",
    }
    assert all(not h["path"].startswith("private") for h in hits)
    assert "Klic" not in " ".join(h["path"] for h in vault.search("api_key"))
    assert vault.open_handoffs() == ["handoffs/2026-10-06 claude → codex - calc.md"]
    assert vault.projects() == ["projects/Calc.md"]


def test_writes_only_append_or_new_in_allowed_folders(vault: Vault) -> None:
    vault.write("projects/Calc.md", "## 2026-10-07\nvariance hotová", "append")
    text = (vault.root / "projects/Calc.md").read_text(encoding="utf-8")
    assert text.startswith("# Calc") and text.rstrip().endswith("variance hotová")
    name = vault.handoff_name("calc: variance?", "codex")
    assert name.startswith("handoffs/") and ":" not in name.split("/", 1)[1]
    vault.write(name, "---\ntype: handoff\n---\nahoj", "new")
    with pytest.raises(Denied):
        vault.write(name, "znovu", "new")  # never overwrite
    with pytest.raises(Denied):
        vault.write("private/Kdo jsem.md", "x", "append")
    with pytest.raises(Denied):
        vault.write("tech/x.md", "x", "new")
    with pytest.raises(Denied):
        vault.write("projects/Calc.md", "password = hunter2hunter2", "append")


def test_folder_counts_mark_what_is_shared(vault: Vault) -> None:
    counts = {c["name"]: c for c in vault.folder_counts()}
    assert counts["projects"]["shared"] is True and counts["private"]["shared"] is False
    assert ".obsidian" not in counts


def test_repo_hint_accepts_both_slash_styles(vault: Vault, tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    (repo / ".git").mkdir(parents=True)
    for written in (str(repo), repo.as_posix()):
        note = vault.root / "projects/Hint.md"
        note.write_text(f'---\npath: "{written}"\n---\n# Hint', encoding="utf-8")
        assert vault.repo_hint(vault.read("projects/Hint.md")) is not None


def test_layout_is_configurable(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    for rel in ("02 Projekty/Calc.md", "08 Předávání/h.md", "01 Profil/me.md"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("---\nstatus: open\n---\nx", encoding="utf-8")
    layout = Layout(
        projects="02 Projekty",
        handoffs="08 Předávání",
        include=("02 Projekty", "08 Předávání"),
        exclude=("01 Profil",),
    )
    vault = Vault(root, layout)
    assert vault.projects() == ["02 Projekty/Calc.md"]
    assert vault.open_handoffs() == ["08 Předávání/h.md"]
    assert vault.handoff_name("t", "codex", "2026-01-01").startswith("08 Předávání/2026-01-01")
    with pytest.raises(Denied):
        vault.read("01 Profil/me.md")
