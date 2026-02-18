#!/usr/bin/env python3
"""Scan source files for common Chinese mojibake patterns."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence


DEFAULT_EXTENSIONS = {".ts", ".tsx", ".py", ".md", ".css", ".json"}
DEFAULT_TARGETS = ("frontend/src", "backend/app", "CLAUDE.md")
SKIP_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
}

# High-confidence mojibake fragments seen in UTF-8 <-> GBK double-encoding accidents.
HIGH_CONFIDENCE_PATTERNS: Sequence[tuple[str, str]] = (
    ("姝ｅ湪", "status_phrase"),
    ("涓婁紶", "upload_phrase"),
    ("澶辫触", "failed_phrase"),
    ("鏂囨。", "document_phrase"),
    ("鍔犺浇", "loading_phrase"),
    ("鑾峰彇", "fetch_phrase"),
    ("娌℃湁", "missing_phrase"),
    ("璇锋眰", "request_phrase"),
    ("鍙戠敓", "error_phrase"),
    ("閿欒", "error_token"),
)

SUSPECT_CHAR_SET = set("澶鍙鏂娌璇锛銆鈥锟")


@dataclass(frozen=True)
class Issue:
    path: Path
    line: int
    reason: str
    snippet: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check files for likely Chinese mojibake.")
    parser.add_argument(
        "--targets",
        nargs="*",
        default=list(DEFAULT_TARGETS),
        help=f"Files or directories to scan (default: {', '.join(DEFAULT_TARGETS)}).",
    )
    parser.add_argument(
        "--ext",
        nargs="*",
        default=sorted(DEFAULT_EXTENSIONS),
        help="File extensions to scan, e.g. --ext .ts .tsx .py",
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="Always exit 0 even when mojibake is detected.",
    )
    return parser.parse_args()


def should_skip_dir(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def iter_files(targets: Sequence[str], extensions: set[str]) -> Iterator[Path]:
    for raw_target in targets:
        target = Path(raw_target)
        if not target.exists():
            continue
        if target.is_file():
            if target.suffix.lower() in extensions:
                yield target
            continue
        for file_path in target.rglob("*"):
            if not file_path.is_file():
                continue
            if should_skip_dir(file_path):
                continue
            if file_path.suffix.lower() in extensions:
                yield file_path


def line_has_mojibake(line: str) -> str | None:
    for pattern, reason in HIGH_CONFIDENCE_PATTERNS:
        if pattern in line:
            return reason

    suspect_char_hits = sum(1 for ch in line if ch in SUSPECT_CHAR_SET)
    if suspect_char_hits >= 3:
        return "suspicious_char_cluster"

    return None


def scan_file(path: Path) -> Iterable[Issue]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [Issue(path=path, line=1, reason="decode_error", snippet="cannot decode as UTF-8")]

    issues: list[Issue] = []
    for line_num, line in enumerate(text.splitlines(), start=1):
        reason = line_has_mojibake(line)
        if not reason:
            continue

        snippet = re.sub(r"\s+", " ", line.strip())
        if len(snippet) > 160:
            snippet = f"{snippet[:157]}..."
        issues.append(Issue(path=path, line=line_num, reason=reason, snippet=snippet))
    return issues


def main() -> int:
    args = parse_args()
    extensions = {ext if ext.startswith(".") else f".{ext}" for ext in args.ext}
    files = sorted(set(iter_files(args.targets, extensions)))

    all_issues: list[Issue] = []
    for file_path in files:
        all_issues.extend(scan_file(file_path))

    if all_issues:
        print(f"[mojibake-check] found {len(all_issues)} issue(s):")
        for issue in all_issues:
            escaped_snippet = issue.snippet.encode("unicode_escape").decode("ascii")
            print(f"{issue.path}:{issue.line}: {issue.reason}: {escaped_snippet}")
        if args.warn_only:
            print("[mojibake-check] warn-only mode enabled; exiting with code 0.")
            return 0
        return 1

    print("[mojibake-check] no suspicious mojibake patterns found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
