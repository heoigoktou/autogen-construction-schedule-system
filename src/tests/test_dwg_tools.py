import os
from pathlib import Path

from tools import dwg_tools
from tools.document_tools import read_source_document
from tools.dwg_tools import find_oda_converter, read_dxf_text


def test_find_oda_converter_from_env(monkeypatch, tmp_path: Path) -> None:
    fake_exe = tmp_path / "ODAFileConverter.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.setenv("ODA_CONVERTER_PATH", str(fake_exe))

    assert find_oda_converter() == fake_exe


def test_find_oda_converter_from_path(monkeypatch, tmp_path: Path) -> None:
    fake_exe = tmp_path / "ODAFileConverter.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.delenv("ODA_CONVERTER_PATH", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))

    assert find_oda_converter() == fake_exe


def test_dxf_reader_extracts_text_layers_and_blocks(tmp_path: Path) -> None:
    dxf = tmp_path / "sample.dxf"
    dxf.write_text(
        "\n".join(
            [
                "0",
                "SECTION",
                "2",
                "ENTITIES",
                "0",
                "TEXT",
                "8",
                "A-WALL",
                "1",
                "总建筑面积 68000 平方米",
                "0",
                "INSERT",
                "8",
                "A-DOOR",
                "2",
                "DOOR-BLOCK",
                "0",
                "ENDSEC",
            ]
        ),
        encoding="utf-8",
    )

    text = read_dxf_text(dxf)

    assert "A-WALL" in text
    assert "DOOR-BLOCK" in text
    assert "总建筑面积 68000 平方米" in text


def test_dwg_without_oda_returns_warning(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(dwg_tools, "find_oda_converter", lambda: None)
    dwg = tmp_path / "plan.dwg"
    dwg.write_bytes(b"not a real dwg")

    document = read_source_document(dwg, install_oda_if_missing=False)

    assert document.text == ""
    assert "未检测到 ODA File Converter" in document.warning


def test_install_oda_not_attempted_without_flag(monkeypatch) -> None:
    called = False

    def fake_install(timeout_seconds: int = 600):
        nonlocal called
        called = True

    monkeypatch.setattr(dwg_tools, "find_oda_converter", lambda: None)
    monkeypatch.setattr(dwg_tools, "install_oda_converter_with_winget", fake_install)

    converter, warning = dwg_tools.ensure_oda_converter(install_if_missing=False)

    assert converter is None
    assert "winget install" in warning
    assert called is False
