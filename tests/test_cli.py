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
