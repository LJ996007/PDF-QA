"""Word to PDF conversion helpers and optional text fallback extraction."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


class WordConversionError(RuntimeError):
    """Raised when all Word conversion backends fail."""


@dataclass
class ConversionResult:
    output_pdf_path: str
    engine: str
    elapsed_ms: int


def _run_command(cmd: Sequence[str], timeout_sec: int = 180) -> Tuple[bool, str]:
    try:
        completed = subprocess.run(
            list(cmd),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout_sec,
        )
    except FileNotFoundError as exc:
        return False, str(exc)
    except subprocess.TimeoutExpired as exc:
        return False, f"timeout after {timeout_sec}s: {' '.join(cmd)}\n{exc}"
    except Exception as exc:  # pragma: no cover - defensive.
        return False, f"unexpected command failure: {exc}"

    if completed.returncode == 0:
        return True, ""

    details = (completed.stderr or completed.stdout or "").strip()
    if not details:
        details = f"exit code {completed.returncode}"
    return False, details


def _convert_with_mammoth_fitz(input_path: str, output_pdf_path: str) -> Tuple[bool, str]:
    """Pure-Python: mammoth (docx→HTML) + PyMuPDF Story (HTML→PDF)."""
    try:
        import mammoth  # type: ignore
        import fitz  # type: ignore
    except ImportError as exc:
        return False, f"missing dependency: {exc}"
    try:
        with open(input_path, "rb") as fh:
            result = mammoth.convert_to_html(fh)
        html = result.value or ""
        if not html.strip():
            return False, "mammoth returned empty HTML"
        full_html = f"<html><body>{html}</body></html>"
        story = fitz.Story(html=full_html)
        writer = fitz.DocumentWriter(output_pdf_path)
        mediabox = fitz.paper_rect("a4")
        margin = 50
        where = mediabox + (margin, margin, -margin, -margin)
        more = True
        while more:
            dev = writer.begin_page(mediabox)
            more, _ = story.place(where)
            story.draw(dev)
            writer.end_page()
        writer.close()
        if Path(output_pdf_path).exists():
            return True, ""
        return False, "mammoth+fitz finished but output file not found"
    except Exception as exc:
        return False, f"mammoth+fitz failed: {exc}"


def _convert_with_docx2pdf(input_path: str, output_pdf_path: str) -> Tuple[bool, str]:
    """Use docx2pdf (Word COM on Windows, LibreOffice on macOS/Linux) to convert."""
    try:
        from docx2pdf import convert  # type: ignore
    except ImportError:
        return False, "docx2pdf not installed"
    try:
        convert(input_path, output_pdf_path)
        if Path(output_pdf_path).exists():
            return True, ""
        return False, "docx2pdf finished but output file not found"
    except Exception as exc:
        return False, f"docx2pdf failed: {exc}"


def _convert_with_unoserver(input_path: str, output_pdf_path: str) -> Tuple[bool, str]:
    unoconvert = shutil.which("unoconvert")
    if not unoconvert:
        return False, "unoconvert executable not found"
    return _run_command([unoconvert, input_path, output_pdf_path])


def _convert_with_soffice(input_path: str, output_pdf_path: str) -> Tuple[bool, str]:
    soffice = shutil.which("soffice")
    if not soffice:
        return False, "soffice executable not found"

    outdir = str(Path(output_pdf_path).parent.resolve())
    ok, err = _run_command(
        [
            soffice,
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            "--convert-to",
            "pdf",
            "--outdir",
            outdir,
            str(Path(input_path).resolve()),
        ]
    )
    if not ok:
        return False, err

    expected = str((Path(outdir) / (Path(input_path).stem + ".pdf")).resolve())
    target = str(Path(output_pdf_path).resolve())
    if os.path.exists(target):
        return True, ""
    if os.path.exists(expected):
        try:
            if expected != target:
                os.replace(expected, target)
            return True, ""
        except Exception as exc:  # pragma: no cover - defensive.
            return False, f"failed to move converted pdf: {exc}"

    return False, "soffice finished but output pdf not found"


def convert_to_pdf(input_path: str, output_pdf_path: str) -> ConversionResult:
    src = Path(input_path).resolve()
    out = Path(output_pdf_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    start = time.perf_counter()
    errors: List[str] = []

    for engine, fn in (
        ("mammoth+fitz", _convert_with_mammoth_fitz),
        ("docx2pdf", _convert_with_docx2pdf),
        ("unoserver", _convert_with_unoserver),
        ("soffice", _convert_with_soffice),
    ):
        ok, err = fn(str(src), str(out))
        if ok and out.exists():
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return ConversionResult(output_pdf_path=str(out), engine=engine, elapsed_ms=elapsed_ms)
        errors.append(f"{engine}: {err}")

    joined = " | ".join(errors)
    raise WordConversionError(f"failed to convert '{src.name}' to PDF ({joined})")


def extract_markdown_with_markitdown(path: str) -> Tuple[str, Optional[str]]:
    """Best-effort markdown extraction for .docx fallback indexing."""
    try:
        from markitdown import MarkItDown  # type: ignore
    except Exception as exc:
        return "", f"markitdown import failed: {exc}"

    try:
        converter = MarkItDown()
        result = converter.convert(path)
        text = getattr(result, "text_content", None) or getattr(result, "markdown", None) or str(result)
        text = (text or "").strip()
        if not text:
            return "", "markitdown returned empty content"
        return text, None
    except Exception as exc:
        return "", f"markitdown convert failed: {exc}"
