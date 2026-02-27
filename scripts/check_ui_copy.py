#!/usr/bin/env python3
"""Guardrail: prevent core Chinese UI copy from regressing to English."""

from __future__ import annotations

from pathlib import Path
import re
import sys


TARGETS = [
    Path("frontend/src/App.tsx"),
    Path("frontend/src/hooks/useVectorSearch.ts"),
]

# Keep this focused on user-visible copy that must stay Chinese.
FORBIDDEN_PHRASES = [
    "Upload Document",
    "Select Files",
    "Add File",
    "Close Current Tab",
    "Processing document",
    "No history records",
    "Loaded audit history.",
    "Upload failed",
    "Request failed",
    "No document is open",
    "Failed to fetch",
    "Failed to queue background OCR",
    "Attach PDF",
    "Upload Mode",
    "No, upload only",
    "Yes, OCR all pages",
    "This history record has no saved PDF.",
    "Starting document processing...",
    "Batch upload completed:",
    "Ignored ",
    "Unknown error",
    "Failed to open history",
    "Failed to attach PDF",
]

STRING_LITERAL_RE = re.compile(
    r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`"
)


def main() -> int:
    violations: list[str] = []

    for path in TARGETS:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for idx, line in enumerate(lines, start=1):
            literals = [m.group(0)[1:-1] for m in STRING_LITERAL_RE.finditer(line)]
            if not literals:
                continue
            for literal in literals:
                for phrase in FORBIDDEN_PHRASES:
                    if phrase in literal:
                        snippet = re.sub(r"\s+", " ", line.strip())
                        violations.append(f"{path}:{idx}: forbidden_ui_copy: {phrase} :: {snippet}")

    if violations:
        print("[ui-copy-check] found UI copy regressions:")
        for v in violations:
            print(v)
        return 1

    print("[ui-copy-check] passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
