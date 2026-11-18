"""Obsidian access policy: only non-personal notes leave the machine; writes need approval."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from studio.sandbox import Denied
from studio.vault import Vault, handoff_name, redact, repo_path_candidates


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "AI paměť"
    files = {
        "00 Rozcestník.md": "# Rozcestník\n[[02 Projekty/Calc]]",
        "01 Profil/Kdo jsem.md": "Tomáš, osobní věci",
        "10 Chaty/chat.md": "obsah chatu",
        "02 Projekty/Calc.md": (
            "# Calc\nRepo: C:\\nikde\\calc\nStav: chybí variance()\nKontakt jan@example.com, 777 123 456"
        ),
        "02 Projekty/Tajne.md": "---\nprivate: true\n---\nnic",
        "02 Projekty/Tagged.md": "---\ntags:\n  - projekt\n  - osobni\n---\nnic",
        "02 Projekty/Klic.md": "api_key = abcdefgh12345678",
        "02 Projekty/Iron Dust a Recorder.md": "# Recorder\nnahrávky",
        "08 Předávání/2026-10-06 claude → codex - calc.md": (
            "---\ntype: handoff\nstatus: open\n---\n# calc\nDodělat variance"
        ),
        "08 Předávání/stare.md": "---\nstatus: done\n---\nhotovo",
        ".obsidian/workspace.md": "x",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Vault(root)


def test_only_shared_folders_are_listed(vault: Vault) -> None:
    notes = vault.notes()
    assert "02 Projekty/Calc.md" in notes and "00 Rozcestník.md" in notes
    assert not any(n.startswith(("01 Profil", "10 Chaty", ".obsidian")) for n in notes)
    assert not any("recorder" in n.lower() for n in notes)  # Recorder data never leaves


@pytest.mark.parametrize(
    "rel",
    [
        "01 Profil/Kdo jsem.md",
        "10 Chaty/chat.md",
        "02 Projekty/Tajne.md",
        "02 Projekty/Tagged.md",
        "02 Projekty/Klic.md",
        "02 Projekty/Iron Dust a Recorder.md",
        "../outside.md",
        "02 Projekty/../01 Profil/Kdo jsem.md",
    ],
)
def test_personal_secret_and_escaping_notes_are_refused(vault: Vault, rel: str) -> None:
    with pytest.raises(Denied):
        vault.read(rel)
    assert vault.reads == []


def test_read_redacts_contacts_and_records_the_read(vault: Vault) -> None:
    note = vault.read("02 Projekty/Calc.md")
    assert "jan@example.com" not in note.text and "777 123 456" not in note.text
    assert "[e-mail]" in note.text and "[telefon]" in note.text
    assert vault.reads == ["02 Projekty/Calc.md"]
    assert redact("2026-10-06 commit 6aa093f") == "2026-10-06 commit 6aa093f"


def test_search_and_handoffs(vault: Vault) -> None:
    hits = vault.search("variance")
    assert {h["path"] for h in hits} == {
        "02 Projekty/Calc.md",
        "08 Předávání/2026-10-06 claude → codex - calc.md",
    }
    assert all(not h["path"].startswith("01 Profil") for h in hits)
    assert "Klic" not in " ".join(h["path"] for h in vault.search("api_key"))
    assert vault.open_handoffs() == ["08 Předávání/2026-10-06 claude → codex - calc.md"]
    assert vault.projects() == ["02 Projekty/Calc.md"]


def test_writes_only_append_or_new_in_allowed_folders(vault: Vault) -> None:
    vault.write("02 Projekty/Calc.md", "## 2026-10-07\nvariance hotová", "append")
    text = (vault.root / "02 Projekty/Calc.md").read_text(encoding="utf-8")
    assert text.startswith("# Calc") and text.rstrip().endswith("variance hotová")
    name = handoff_name("calc: variance?", "codex")
    assert name.startswith("08 Předávání/") and ":" not in name.split("/", 1)[1]
    vault.write(name, "---\ntype: handoff\n---\nahoj", "new")
    with pytest.raises(Denied):
        vault.write(name, "znovu", "new")  # never overwrite
    with pytest.raises(Denied):
        vault.write("01 Profil/Kdo jsem.md", "x", "append")
    with pytest.raises(Denied):
        vault.write("11 Technologie/x.md", "x", "new")
    with pytest.raises(Denied):
        vault.write("02 Projekty/Calc.md", "password = hunter2hunter2", "append")


def test_folder_counts_mark_what_is_shared(vault: Vault) -> None:
    counts = {c["name"]: c for c in vault.folder_counts()}
    assert counts["02 Projekty"]["shared"] is True and counts["01 Profil"]["shared"] is False
    assert ".obsidian" not in counts


@pytest.mark.skipif(sys.platform != "win32", reason="Windows backslash paths")
def test_repo_hint_accepts_both_slash_styles(vault: Vault, tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    (repo / ".git").mkdir(parents=True)
    for written in (str(repo), repo.as_posix()):
        note = vault.root / "02 Projekty/Hint.md"
        note.write_text(f'---\npath: "{written}"\n---\n# Hint', encoding="utf-8")
        assert vault.repo_hint(vault.read("02 Projekty/Hint.md")) is not None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX absolute paths")
def test_repo_hint_finds_posix_paths(vault: Vault, tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    (repo / ".git").mkdir(parents=True)
    note = vault.root / "02 Projekty/Hint.md"
    for line in (f"Repozitář: {repo}", f"Repo: `{repo}`.", f'path: "{repo}"', f"({repo})"):
        note.write_text(f"# Hint\n{line}\n", encoding="utf-8")
        assert vault.repo_hint(vault.read("02 Projekty/Hint.md")) == str(repo), line


def test_repo_hint_skips_paths_that_are_not_repositories(vault: Vault, tmp_path: Path) -> None:
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    note = vault.root / "02 Projekty/Hint.md"
    note.write_text(f"# Hint\nRepo: {plain}\nURL https://example.com/a/b\n", encoding="utf-8")
    assert vault.repo_hint(vault.read("02 Projekty/Hint.md")) is None


def test_repo_path_candidates_runs_on_every_platform() -> None:
    text = (
        "Repozitář: /home/t/calc.\n"
        "Win: C:\\src\\calc a C:/src/other\n"
        "Odkaz [kód](/srv/code) a [[/home/t/wiki]] a **/opt/x**\n"
        "Ne: https://example.com/a/b, file:///etc/x, ~/x, //nas/share, \\\\nas\\share, a/b, /\n"
    )
    assert repo_path_candidates(text) == [
        "/home/t/calc",
        "C:\\src\\calc",
        "C:/src/other",
        "/srv/code",
        "/home/t/wiki",
        "/opt/x",
    ]


def test_repo_hint_never_offers_the_vault(tmp_path: Path) -> None:
    # An Obsidian vault synced with git, at a path without spaces (as on the hubs).
    root = tmp_path / "vault"
    (root / ".git").mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    note = root / "02 Projekty" / "Hint.md"
    note.parent.mkdir()
    vault = Vault(root)
    for target in (root, tmp_path):  # the vault itself, or a repo that contains it
        note.write_text(f"# Hint\nRepo: {target}\n", encoding="utf-8")
        assert vault.repo_hint(vault.read("02 Projekty/Hint.md")) is None, target
