"""DWG/DXF helpers with optional ODA File Converter integration."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

ODA_WINGET_ID = "ODA.ODAFileConverter"
SUPPORTED_DWG_VERSION = "ACAD2018"
SUPPORTED_DXF_FORMAT = "DXF"


@dataclass(frozen=True)
class OdaInstallResult:
    """Result of an ODA installation attempt."""

    installed_path: Path | None
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    warning: str = ""


def find_oda_converter() -> Path | None:
    """Find ODAFileConverter.exe from env, PATH, or common install folders."""

    env_path = os.environ.get("ODA_CONVERTER_PATH", "").strip().strip('"')
    if env_path:
        path = Path(env_path)
        if path.exists() and path.is_file():
            return path

    path_from_path = shutil.which("ODAFileConverter.exe") or shutil.which("ODAFileConverter")
    if path_from_path:
        return Path(path_from_path)

    program_files = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    candidates: list[Path] = []
    for root in program_files:
        if not root:
            continue
        candidates.extend(
            [
                Path(root) / "ODA" / "ODAFileConverter" / "ODAFileConverter.exe",
                Path(root) / "ODA" / "ODA File Converter" / "ODAFileConverter.exe",
            ]
        )

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def install_oda_converter_with_winget(timeout_seconds: int = 600) -> OdaInstallResult:
    """Install ODA File Converter with winget and return the detected path."""

    winget = shutil.which("winget")
    command = [
        winget or "winget",
        "install",
        "--id",
        ODA_WINGET_ID,
        "--exact",
        "--accept-package-agreements",
        "--accept-source-agreements",
    ]
    if not winget:
        return OdaInstallResult(
            installed_path=None,
            command=command,
            returncode=None,
            stdout="",
            stderr="",
            warning="未找到 winget，无法自动安装 ODA File Converter，请手动安装。",
        )

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )
    installed_path = find_oda_converter()
    warning = ""
    if completed.returncode != 0 and installed_path is None:
        warning = f"winget 安装 ODA File Converter 失败，退出码 {completed.returncode}"
    elif installed_path is None:
        warning = "winget 执行完成，但仍未检测到 ODAFileConverter.exe"
    return OdaInstallResult(
        installed_path=installed_path,
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        warning=warning,
    )


def ensure_oda_converter(
    *,
    install_if_missing: bool,
    timeout_seconds: int = 600,
) -> tuple[Path | None, str]:
    """Find ODA or optionally install it."""

    converter = find_oda_converter()
    if converter:
        return converter, ""

    if not install_if_missing:
        return (
            None,
            "未检测到 ODA File Converter。可运行 "
            "`winget install --id ODA.ODAFileConverter --exact "
            "--accept-package-agreements --accept-source-agreements`，"
            "或使用 `--install-oda-if-missing` 自动安装。",
        )

    result = install_oda_converter_with_winget(timeout_seconds=timeout_seconds)
    if result.installed_path:
        return result.installed_path, ""
    return None, result.warning or "ODA File Converter 自动安装失败"


def convert_dwg_to_dxf(
    dwg_path: str | Path,
    *,
    output_dir: str | Path,
    converter_path: str | Path,
    timeout_seconds: int = 120,
) -> Path:
    """Convert one DWG file to DXF with ODA File Converter."""

    source = Path(dwg_path)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="oda_in_") as temp_input_dir:
        temp_input = Path(temp_input_dir)
        temp_source = temp_input / source.name
        shutil.copy2(source, temp_source)
        command = [
            str(converter_path),
            str(temp_input),
            str(target_dir),
            SUPPORTED_DWG_VERSION,
            SUPPORTED_DXF_FORMAT,
            "0",
            "1",
            "*.dwg",
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    candidates = sorted(target_dir.glob(f"{source.stem}*.dxf"))
    if completed.returncode != 0 or not candidates:
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        raise RuntimeError(
            f"DWG 转 DXF 失败：{source.name}，退出码 {completed.returncode}，输出：{output[:800]}"
        )
    return candidates[0]


def read_dxf_text(path: str | Path) -> str:
    """Extract readable text, layer names, and block names from a DXF file."""

    dxf_path = Path(path)
    lines = _read_dxf_lines(dxf_path)
    pairs = _pairs(lines)
    texts: list[str] = []
    layers: set[str] = set()
    blocks: set[str] = set()
    entity_types: set[str] = set()

    current_entity = ""
    for code, value in pairs:
        if code == "0":
            current_entity = value
            entity_types.add(value)
            continue
        if code == "8" and value:
            layers.add(value)
        elif code == "2" and value and current_entity in {"BLOCK", "INSERT"}:
            blocks.add(value)
        elif code in {"1", "3"} and value:
            cleaned = _clean_dxf_text(value)
            if cleaned and len(cleaned) > 1:
                texts.append(cleaned)

    sections = [f"DXF 文件：{dxf_path.name}"]
    if layers:
        sections.append("图层：" + "、".join(sorted(layers)[:200]))
    if blocks:
        sections.append("块名：" + "、".join(sorted(blocks)[:200]))
    if entity_types:
        sections.append("实体类型：" + "、".join(sorted(entity_types)[:80]))
    if texts:
        sections.append("文字内容：\n" + "\n".join(dict.fromkeys(texts)))
    return "\n\n".join(sections)


def _read_dxf_lines(path: Path) -> list[str]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return path.read_text(encoding=encoding).splitlines()
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="ignore").splitlines()


def _pairs(lines: list[str]) -> list[tuple[str, str]]:
    result = []
    index = 0
    while index + 1 < len(lines):
        result.append((lines[index].strip(), lines[index + 1].strip()))
        index += 2
    return result


def _clean_dxf_text(value: str) -> str:
    cleaned = value.replace("\\P", "\n").replace("\\~", " ")
    cleaned = re.sub(r"\\[A-Za-z][^;]*;", "", cleaned)
    cleaned = re.sub(r"[{}]", "", cleaned)
    return cleaned.strip()
