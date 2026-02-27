# Encoding Memory (Must Follow)

## Always-On Rules
1. All project text files are `UTF-8` without BOM.
2. Before commit, run:
   - `cd frontend && npm run lint:mojibake`
3. Keep pre-commit hook enabled:
   - `git config core.hooksPath .githooks`
4. If Chinese output looks garbled in terminal, do not copy it into source.
5. Any file touched by large text edits must pass:
   - mojibake scan
   - compile/build validation

## Safe Edit Workflow (Windows)
1. Before session:
   - `chcp 65001`
2. Edit in IDE with explicit UTF-8 (no BOM).
3. After edits:
   - Run mojibake scan.
   - Run build/py_compile.
4. Before merge/commit:
   - Confirm zero mojibake issues.

## Required Checks
```bash
cd frontend && npm run lint:mojibake
cd ..
python scripts/check_mojibake.py --targets frontend/src backend/app/services/rag_engine.py backend/main.py CLAUDE.md
python -m py_compile backend/app/services/rag_engine.py backend/main.py scripts/check_mojibake.py
cd frontend && npm run build
```

## If Mojibake Appears Again
1. Stop feature edits and isolate encoding issue first.
2. Run:
   - `python scripts/check_mojibake.py --warn-only`
3. Fix source text with semantic restoration (not blind byte hacks).
4. Re-run full checks until zero issues.
5. Append new case into:
   - `docs/encoding_work_archive.md`

