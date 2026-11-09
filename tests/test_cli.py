"""End-to-end CLI behaviour: exit codes, backups, batches, reports and --staged."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from doc_meta_strip.cli import EXIT_ERROR, EXIT_METADATA, EXIT_OK, main

from conftest import FAKE_AUTHOR, make_docx, make_jpeg, make_pdf


def test_clean_to_output_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_jpeg(tmp_path / "photo.jpg")
    target = tmp_path / "clean.jpg"

    code = main(["clean", str(source), "-o", str(target)])

    assert code == EXIT_OK
    assert target.exists()
    assert FAKE_AUTHOR.encode() not in target.read_bytes()
    assert source.read_bytes() != target.read_bytes()  # input untouched
    assert "item(s) removed" in capsys.readouterr().out


def test_clean_in_place_with_backup(tmp_path: Path) -> None:
    source = make_jpeg(tmp_path / "photo.jpg")
    original = source.read_bytes()

    code = main(["clean", str(source), "--in-place", "--backup"])

    assert code == EXIT_OK
    backup = tmp_path / "photo.jpg.bak"
    assert backup.read_bytes() == original
    assert FAKE_AUTHOR.encode() not in source.read_bytes()

    # A second run must not clobber the first backup.
    make_jpeg(tmp_path / "photo.jpg")
    assert main(["clean", str(source), "--in-place", "--backup", "--quiet"]) == EXIT_OK
    assert (tmp_path / "photo.jpg.bak.1").exists()


def test_clean_directory_batch(tmp_path: Path) -> None:
    folder = tmp_path / "belgeler"
    folder.mkdir()
    make_jpeg(folder / "one.jpg")
    make_pdf(folder / "two.pdf")
    make_docx(folder / "three.docx")
    (folder / "notes.txt").write_text("ignored", encoding="utf-8")

    code = main(["clean", str(folder), "--in-place", "--quiet"])

    assert code == EXIT_OK
    for name in ("one.jpg", "two.pdf", "three.docx"):
        assert FAKE_AUTHOR.encode() not in (folder / name).read_bytes()


def test_no_recursive_skips_subdirectories(tmp_path: Path) -> None:
    folder = tmp_path / "belgeler"
    nested = folder / "alt"
    nested.mkdir(parents=True)
    make_jpeg(nested / "deep.jpg")
    make_jpeg(folder / "top.jpg")

    code = main(["clean", str(folder), "--in-place", "--no-recursive", "--quiet"])

    assert code == EXIT_OK
    assert FAKE_AUTHOR.encode() in (nested / "deep.jpg").read_bytes()
    assert FAKE_AUTHOR.encode() not in (folder / "top.jpg").read_bytes()


def test_scan_strict_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_jpeg(tmp_path / "photo.jpg")

    assert main(["scan", str(source), "--strict"]) == EXIT_METADATA
    out = capsys.readouterr().out
    assert "sensitive metadata item(s)" in out
    assert "GPS location" in out


def test_scan_clean_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_jpeg(tmp_path / "photo.jpg")
    main(["clean", str(source), "--in-place", "--quiet"])
    capsys.readouterr()

    assert main(["scan", str(source), "--strict"]) == EXIT_OK
    assert "clean" in capsys.readouterr().out


def test_scan_without_paths_fails() -> None:
    assert main(["scan"]) == EXIT_ERROR


def test_clean_without_destination_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_jpeg(tmp_path / "photo.jpg")
    assert main(["clean", str(source)]) == EXIT_ERROR
    assert "--in-place" in capsys.readouterr().err


def test_output_with_multiple_inputs_fails(tmp_path: Path) -> None:
    first = make_jpeg(tmp_path / "a.jpg")
    second = make_jpeg(tmp_path / "b.jpg")
    assert main(["clean", str(first), str(second), "-o", str(tmp_path / "out.jpg")]) == EXIT_ERROR


def test_missing_path_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", str(tmp_path / "nope.pdf")]) == EXIT_ERROR
    assert "no such file" in capsys.readouterr().err


def test_unsupported_file_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("plain text", encoding="utf-8")

    assert main(["clean", str(notes), "--in-place"]) == EXIT_ERROR
    assert "unsupported file format" in capsys.readouterr().err


def test_corrupt_file_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7 but broken")
    assert main(["scan", str(broken)]) == EXIT_ERROR
    assert "not a readable PDF" in capsys.readouterr().err


def test_json_report_file(tmp_path: Path) -> None:
    source = make_jpeg(tmp_path / "photo.jpg")
    report_path = tmp_path / "audit.json"

    code = main(["clean", str(source), "--in-place", "--report", str(report_path), "--quiet"])

    assert code == EXIT_OK
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["tool"] == "doc-meta-strip"
    assert payload["mode"] == "clean"
    assert payload["summary"]["cleaned"] == 1
    assert payload["files"][0]["status"] == "cleaned"
    removed = payload["files"][0]["removed"]
    assert any(item["category"] == "gps" for item in removed)
    assert any(item["label"] == "Author / identity" for item in removed)


def test_scan_json_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_jpeg(tmp_path / "photo.jpg")
    assert main(["scan", str(source), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "scan"
    assert payload["summary"]["with_metadata"] == 1


def test_quiet_prints_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_jpeg(tmp_path / "photo.jpg")
    assert main(["clean", str(source), "--in-place", "--quiet"]) == EXIT_OK
    assert capsys.readouterr().out == ""


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "doc-meta-strip 0.1.0" in capsys.readouterr().out


def test_clean_already_clean_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_jpeg(tmp_path / "photo.jpg")
    main(["clean", str(source), "--in-place", "--quiet"])
    capsys.readouterr()

    assert main(["clean", str(source), "--in-place"]) == EXIT_OK
    assert "no metadata to remove" in capsys.readouterr().out


def test_output_copy_of_clean_file(tmp_path: Path) -> None:
    source = make_jpeg(tmp_path / "photo.jpg")
    main(["clean", str(source), "--in-place", "--quiet"])
    target = tmp_path / "copy.jpg"

    assert main(["clean", str(source), "-o", str(target), "--quiet"]) == EXIT_OK
    assert target.read_bytes() == source.read_bytes()


def test_keep_thumbnails_flag(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "rapor.pdf")
    assert main(["clean", str(source), "--in-place", "--keep-thumbnails", "--quiet"]) == EXIT_OK
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(source.read_bytes()))
    assert "/Thumb" in reader.pages[0]


def test_password_flow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from conftest import make_encrypted_pdf

    source = make_encrypted_pdf(tmp_path / "locked.pdf", password="fake-password")

    assert main(["clean", str(source), "--in-place"]) == EXIT_ERROR
    assert "--password" in capsys.readouterr().err

    assert main(["clean", str(source), "--in-place", "--password", "fake-password", "--quiet"]) == EXIT_OK
    assert FAKE_AUTHOR.encode() not in source.read_bytes()


def test_password_can_come_from_prompt_stdin_or_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every non-argv password source must unlock an encrypted PDF.
    import io
    import sys

    import doc_meta_strip.cli as cli

    from conftest import make_encrypted_pdf

    prompted = make_encrypted_pdf(tmp_path / "prompted.pdf", password="fake-password")
    seen: list[str] = []

    def fake_getpass(prompt: str) -> str:
        seen.append(prompt)
        return "fake-password"

    monkeypatch.setattr(cli.getpass, "getpass", fake_getpass)
    assert (
        main(["clean", str(prompted), "--in-place", "--ask-password", "--lang", "tr", "--quiet"]) == EXIT_OK
    )
    assert seen and "PDF parolası" in seen[0]  # prompt used, not an argv value
    assert FAKE_AUTHOR.encode() not in prompted.read_bytes()

    from_stdin = make_encrypted_pdf(tmp_path / "stdin.pdf", password="fake-password")
    monkeypatch.setattr(sys, "stdin", io.StringIO("fake-password" + chr(10)))
    assert main(["clean", str(from_stdin), "--in-place", "--password", "-", "--quiet"]) == EXIT_OK
    assert FAKE_AUTHOR.encode() not in from_stdin.read_bytes()

    from_env = make_encrypted_pdf(tmp_path / "env.pdf", password="fake-password")
    monkeypatch.delenv("DOC_META_STRIP_PASSWORD", raising=False)
    monkeypatch.setenv("DOC_META_STRIP_PASSWORD", "fake-password")
    assert main(["clean", str(from_env), "--in-place", "--quiet"]) == EXIT_OK
    assert FAKE_AUTHOR.encode() not in from_env.read_bytes()


def test_ask_password_abort_is_a_controlled_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import doc_meta_strip.cli as cli

    from conftest import make_encrypted_pdf

    source = make_encrypted_pdf(tmp_path / "locked.pdf", password="fake-password")

    def abort(_: str) -> str:
        raise EOFError

    monkeypatch.setattr(cli.getpass, "getpass", abort)
    assert main(["clean", str(source), "--in-place", "--ask-password"]) == EXIT_ERROR
    assert "password prompt aborted" in capsys.readouterr().err


def test_lang_option_and_environment_localize_human_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = make_jpeg(tmp_path / "photo.jpg")

    assert main(["scan", str(source), "--lang", "tr"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "hassas üstveri öğesi" in out
    assert "GPS konumu" in out
    assert "dosya tarandı" in out

    monkeypatch.setenv("DOC_META_STRIP_LANG", "tr")
    assert main(["scan", str(source)]) == EXIT_OK
    assert "hassas üstveri öğesi" in capsys.readouterr().out

    # An explicit flag wins over the environment.
    assert main(["scan", str(source), "--lang", "en"]) == EXIT_OK
    assert "sensitive metadata" in capsys.readouterr().out


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if shutil.which("git") is None:  # pragma: no cover - git is present on CI images
        pytest.skip("git is not installed")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], check=True)
    monkeypatch.chdir(repo)
    return repo


def test_staged_files_are_cleaned_and_restaged(git_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_jpeg(git_repo / "photo.jpg")
    subprocess.run(["git", "-C", str(git_repo), "add", "photo.jpg"], check=True)

    code = main(["clean", "--staged", "--report", "audit.json"])

    assert code == EXIT_OK
    assert FAKE_AUTHOR.encode() not in source.read_bytes()
    staged = subprocess.run(
        ["git", "-C", str(git_repo), "show", ":photo.jpg"], capture_output=True, check=True
    ).stdout
    assert FAKE_AUTHOR.encode() not in staged
    assert (git_repo / "audit.json").exists()
    out = capsys.readouterr().out
    assert "re-staged 1 file(s)" in out


def test_staged_without_changes(git_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (git_repo / "notes.txt").write_text("no metadata here", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", "notes.txt"], check=True)

    assert main(["clean", "--staged"]) == EXIT_OK
    assert "no supported files are staged" in capsys.readouterr().out


def test_staged_outside_git_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["clean", "--staged"]) == EXIT_ERROR
    assert "git diff failed" in capsys.readouterr().err


def test_unwritable_output_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_jpeg(tmp_path / "photo.jpg")
    assert main(["clean", str(source), "-o", str(tmp_path)]) == EXIT_ERROR
    assert "cannot write" in capsys.readouterr().err


def test_staged_file_with_broken_content_does_not_stop_the_hook(
    git_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = git_repo / "broken.jpg"
    broken.write_bytes(b"definitely not a jpeg")
    good = make_jpeg(git_repo / "photo.jpg")
    subprocess.run(["git", "-C", str(git_repo), "add", "broken.jpg", "photo.jpg"], check=True)

    assert main(["clean", "--staged", "--quiet"]) == EXIT_ERROR
    assert "unsupported file format" in capsys.readouterr().err
    assert FAKE_AUTHOR.encode() not in good.read_bytes()


def test_scan_report_cannot_overwrite_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_jpeg(tmp_path / "photo.jpg")
    original = source.read_bytes()

    assert main(["scan", str(source), "--report", str(source)]) == EXIT_ERROR
    assert "would overwrite" in capsys.readouterr().err
    assert source.read_bytes() == original


def test_clean_report_cannot_overwrite_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_jpeg(tmp_path / "photo.jpg")
    target = tmp_path / "clean.jpg"

    assert main(["clean", str(source), "-o", str(target), "--report", str(target)]) == EXIT_ERROR
    assert "would overwrite" in capsys.readouterr().err
    assert not target.exists()


def test_clean_report_cannot_overwrite_backup(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_jpeg(tmp_path / "photo.jpg")
    original = source.read_bytes()

    backup = tmp_path / "photo.jpg.bak"

    assert main(["clean", str(source), "--in-place", "--backup", "--report", str(backup)]) == EXIT_ERROR
    assert "would overwrite the backup" in capsys.readouterr().err
    assert source.read_bytes() == original


def test_report_dash_keeps_stdout_json_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_jpeg(tmp_path / "photo.jpg")

    assert main(["clean", str(source), "--in-place", "--report", "-"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "clean"
    assert payload["summary"]["cleaned"] == 1


def test_unwritable_report_path_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_jpeg(tmp_path / "photo.jpg")
    blocker = tmp_path / "not-a-file"
    blocker.mkdir()

    assert main(["clean", str(source), "--in-place", "--report", str(blocker), "--quiet"]) == EXIT_ERROR
    assert "cannot write report" in capsys.readouterr().err


def test_json_and_report_dash_emit_one_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``--json --report -`` used to print the same JSON report twice."""
    source = make_jpeg(tmp_path / "photo.jpg")

    assert main(["clean", str(source), "--in-place", "--json", "--report", "-"]) == EXIT_OK
    out = capsys.readouterr().out

    decoder = json.JSONDecoder()
    payload, end = decoder.raw_decode(out)
    assert payload["mode"] == "clean"
    assert out[end:].strip() == ""


def test_clean_strict_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``clean --strict`` cleans the file and exits 3 when metadata was removed."""
    source = make_jpeg(tmp_path / "photo.jpg")

    assert main(["clean", str(source), "--in-place", "--strict"]) == EXIT_METADATA
    assert FAKE_AUTHOR.encode() not in source.read_bytes()
    assert "item(s) removed" in capsys.readouterr().out

    # Once clean, a subsequent strict clean exits 0.
    assert main(["clean", str(source), "--in-place", "--strict", "--quiet"]) == EXIT_OK


def test_clean_strict_error_takes_precedence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7 but broken")

    assert main(["clean", str(broken), "--in-place", "--strict"]) == EXIT_ERROR
    assert "not a readable PDF" in capsys.readouterr().err


def test_staged_report_cannot_overwrite_staged_file(
    git_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = make_jpeg(git_repo / "photo.jpg")
    original = source.read_bytes()
    subprocess.run(["git", "-C", str(git_repo), "add", "photo.jpg"], check=True)

    assert main(["clean", "--staged", "--report", str(source)]) == EXIT_ERROR
    assert "would overwrite" in capsys.readouterr().err
    assert source.read_bytes() == original


def test_staged_from_subdirectory_cleans_repository_files(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """git reports index paths relative to the repo root, not to the cwd."""
    source = make_jpeg(git_repo / "photo.jpg")
    subprocess.run(["git", "-C", str(git_repo), "add", "photo.jpg"], check=True)
    (git_repo / "sub").mkdir()
    monkeypatch.chdir(git_repo / "sub")

    assert main(["clean", "--staged"]) == EXIT_OK

    assert FAKE_AUTHOR.encode() not in source.read_bytes()
    staged = subprocess.run(
        ["git", "-C", str(git_repo), "show", ":photo.jpg"], capture_output=True, check=True
    ).stdout
    assert FAKE_AUTHOR.encode() not in staged
    assert "re-staged 1 file(s)" in capsys.readouterr().out


def test_staged_cleans_index_blob_and_preserves_unstaged_worktree_changes(
    git_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When index and worktree differ, ``--staged`` cleans the index blob and keeps unstaged edits."""
    doc = make_docx(git_repo / "sunum.docx")
    subprocess.run(["git", "-C", str(git_repo), "add", "sunum.docx"], check=True)

    # Modify the working tree copy with unstaged edits after staging.
    unstaged_marker = b"UNSTAGED_WORKTREE_DRAFT_TEXT"
    import zipfile

    with zipfile.ZipFile(doc, "a") as archive:
        archive.writestr("word/unstaged_note.xml", unstaged_marker)
    worktree_before = doc.read_bytes()

    code = main(["clean", "--staged"])

    assert code == EXIT_OK
    # Working tree copy must remain completely untouched.
    assert doc.read_bytes() == worktree_before
    # Staged index blob must be sanitized AND must not contain unstaged worktree edits.
    staged = subprocess.run(
        ["git", "-C", str(git_repo), "show", ":sunum.docx"], capture_output=True, check=True
    ).stdout
    assert FAKE_AUTHOR.encode() not in staged
    assert unstaged_marker not in staged
    out = capsys.readouterr().out
    assert "working tree has unstaged changes; cleaned staged index blob only" in out


def test_staged_clean_index_does_not_stage_dirty_worktree(
    git_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An already-clean staged blob must not cause dirty unstaged worktree edits to be staged."""
    source = make_jpeg(git_repo / "photo.jpg")
    assert main(["clean", str(source), "--in-place", "--quiet"]) == EXIT_OK
    clean_bytes = source.read_bytes()
    subprocess.run(["git", "-C", str(git_repo), "add", "photo.jpg"], check=True)

    # Re-create dirty JPEG with metadata in the working tree without staging it.
    make_jpeg(source)
    dirty_worktree = source.read_bytes()
    assert FAKE_AUTHOR.encode() in dirty_worktree
    capsys.readouterr()

    assert main(["clean", "--staged"]) == EXIT_OK

    staged = subprocess.run(
        ["git", "-C", str(git_repo), "show", ":photo.jpg"], capture_output=True, check=True
    ).stdout
    assert staged == clean_bytes
    assert source.read_bytes() == dirty_worktree


def test_staged_file_deleted_in_worktree_still_cleans_index_blob(
    git_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Deleting a file from disk after ``git add`` must still clean the staged index blob."""
    source = make_jpeg(git_repo / "photo.jpg")
    subprocess.run(["git", "-C", str(git_repo), "add", "photo.jpg"], check=True)
    source.unlink()

    assert main(["clean", "--staged"]) == EXIT_OK

    assert not source.exists()
    staged = subprocess.run(
        ["git", "-C", str(git_repo), "show", ":photo.jpg"], capture_output=True, check=True
    ).stdout
    assert FAKE_AUTHOR.encode() not in staged
    assert "re-staged 1 file(s)" in capsys.readouterr().out


def test_clean_staged_direct_call_and_unstaged_target_error(git_repo: Path) -> None:
    from doc_meta_strip.hook import clean_staged

    source = make_jpeg(git_repo / "photo.jpg")
    # Calling clean_staged with an explicit target that was never git-added reports an error outcome.
    outcomes, changed = clean_staged(root=git_repo, targets=[source])
    assert len(outcomes) == 1
    assert not outcomes[0].ok
    assert "not staged in the git index" in (outcomes[0].error or "")
    assert changed == []

    # When staged and targets is omitted, clean_staged discovers and cleans it.
    subprocess.run(["git", "-C", str(git_repo), "add", "photo.jpg"], check=True)
    outcomes, changed = clean_staged(root=git_repo)
    assert len(outcomes) == 1 and outcomes[0].status == "cleaned"
    assert changed == [source]


def test_clean_staged_unmerged_or_symlink_index_entry_reports_error(git_repo: Path) -> None:
    from doc_meta_strip.hook import clean_staged

    source = make_jpeg(git_repo / "photo.jpg")
    oid = subprocess.run(
        ["git", "-C", str(git_repo), "hash-object", "-w", str(source)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    # Stage as a symlink (mode 120000) -> unsupported git index mode.
    subprocess.run(
        ["git", "-C", str(git_repo), "update-index", "--add", "--cacheinfo", f"120000,{oid},photo.jpg"],
        check=True,
    )
    outcomes, _ = clean_staged(root=git_repo, targets=[source])
    assert len(outcomes) == 1 and not outcomes[0].ok
    assert "unsupported git index mode" in (outcomes[0].error or "")

    # Stage as an unmerged conflict entry (stages 1 and 2) -> unmerged git index entries.
    subprocess.run(
        ["git", "-C", str(git_repo), "update-index", "--index-info"],
        input=f"0 {'0' * 40}\tphoto.jpg\n100644 {oid} 1\tphoto.jpg\n100644 {oid} 2\tphoto.jpg\n".encode(),
        check=True,
    )
    outcomes, _ = clean_staged(root=git_repo, targets=[source])
    assert len(outcomes) == 1 and not outcomes[0].ok
    assert "unmerged git index entries" in (outcomes[0].error or "")


def test_cli_subprocess_handles_legacy_windows_codepage(tmp_path: Path) -> None:
    """Running the CLI with a legacy 8-bit stdio encoding (e.g. cp1254/cp1252) must not crash."""
    import os
    import sys

    source = make_jpeg(tmp_path / "photo.jpg")
    env = {**os.environ, "PYTHONIOENCODING": "cp1254"}
    root = Path(__file__).resolve().parents[1]

    scan_proc = subprocess.run(
        [sys.executable, "-m", "doc_meta_strip", "scan", str(source)],
        cwd=root,
        env=env,
        capture_output=True,
        check=False,
    )
    assert scan_proc.returncode == EXIT_OK

    clean_proc = subprocess.run(
        [sys.executable, "-m", "doc_meta_strip", "clean", str(source), "--in-place"],
        cwd=root,
        env=env,
        capture_output=True,
        check=False,
    )
    assert clean_proc.returncode == EXIT_OK
    assert FAKE_AUTHOR.encode() not in source.read_bytes()


def test_unsupported_embedded_media_is_visible_in_human_scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import zipfile

    source = tmp_path / "scan.docx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="urn:w"/>')
        archive.writestr("word/media/unknown.tif", b"II*\x00" + bytes(32))
    code = main(["scan", str(source), "--strict"])
    output = capsys.readouterr().out
    assert code == EXIT_METADATA
    assert "media-uninspected" in output
    assert "unknown.tif" in output


def test_staged_cleans_index_blob_not_worktree(git_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The commit must contain the cleaned *staged* version, not the worktree.

    Reproduces the leak where ``clean --staged`` read the file from disk,
    cleaned that, and ``git add``-ed it -- sweeping every unstaged edit (here a
    secret appended after staging) into the next commit.
    """
    source = make_jpeg(git_repo / "belge.jpg")
    staged_v1 = source.read_bytes()
    subprocess.run(["git", "-C", str(git_repo), "add", "belge.jpg"], check=True)

    # v2 exists only in the worktree and carries content that must not be staged.
    worktree_v2 = staged_v1 + b"WORKTREE-ONLY-SECRET"
    source.write_bytes(worktree_v2)

    assert main(["clean", "--staged"]) == EXIT_OK

    index_blob = subprocess.run(
        ["git", "-C", str(git_repo), "show", ":belge.jpg"], capture_output=True, check=True
    ).stdout
    assert b"WORKTREE-ONLY-SECRET" not in index_blob
    assert FAKE_AUTHOR.encode() not in index_blob
    assert index_blob != staged_v1
    # The unrelated worktree change is preserved untouched.
    assert source.read_bytes() == worktree_v2
    assert "unstaged changes; cleaned staged index blob only" in capsys.readouterr().out


def test_strict_scan_is_green_after_cleaning_a_docx_with_vector_pictures(tmp_path: Path) -> None:
    """GIF/SVG pictures are cleaned, so ``clean`` then ``scan --strict`` exits 0."""
    import zipfile

    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1">'
        b'<metadata>Fake Embedded Author</metadata><rect width="1" height="1"/></svg>'
    )
    gif = (
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
        b"\x21\xfe\x04Fake\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b"
    )
    source = tmp_path / "vector.docx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="urn:w"/>')
        archive.writestr("word/media/a.svg", svg)
        archive.writestr("word/media/b.gif", gif)
    assert main(["scan", str(source), "--strict"]) == EXIT_METADATA
    assert main(["clean", str(source), "--in-place"]) == EXIT_OK
    assert main(["scan", str(source), "--strict"]) == EXIT_OK
