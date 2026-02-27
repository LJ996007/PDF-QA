# Encoding Work Archive (Mojibake)

## Purpose
This archive records why mojibake repeatedly appears after feature changes, and how we fixed it in this repository.  
Scope: `frontend/src`, `backend/main.py`, `backend/app/routers`, `backend/app/services/rag_engine.py`, `CLAUDE.md`.

## Incident Summary

### Incident A (UI text garbled)
- Symptom:
  - Main page title, upload area text, and button labels became garbled Chinese.
- Example pattern: common double-encoding artifacts in status labels and button text.
- Impact:
  - User-visible regression, directly affects usability.
- Root cause:
  - UTF-8 Chinese text was written/edited through non-UTF-safe paths, then saved again.
  - Some edits were performed through terminal/output channels that did not preserve Unicode reliably.
  - Existing mojibake strings were copied forward during feature additions.

### Incident B (Backend comments/docstrings garbled)
- Symptom:
  - `backend/app/services/rag_engine.py` comments/docstrings became unreadable mojibake.
- Impact:
  - Runtime logic unaffected, but maintenance quality dropped; future edits became risky.
- Root cause:
  - Historical garbled content stayed in file and was re-propagated during later modifications.
  - Encoding discipline was not enforced for comment/docstring edits.

### Incident C (BOM + hidden bad chars leak)
- Symptom:
  - Files passed visual checks but still had encoding issues.
  - UTF-8 BOM and private-use characters appeared in tracked files.
- Root cause:
  - Different tools used different default save encoding.
  - Prior checker covered high-confidence mojibake patterns, but not BOM/private-use/cjk-`?` literal cases.

## Confirmed Technical Causes
1. Mixed encoding paths on Windows:
   - Editing/saving with tools using non-UTF defaults can corrupt Chinese content.
2. Terminal display encoding != file encoding:
   - Garbled console output may be copied back into source by mistake.
3. Non-atomic manual replacement:
   - Large-scale text replacement without final scanner/build validation leaves residual corruption.
4. Missing guardrails in older workflow:
   - Before checker enhancement, BOM/private-use/cjk-`?` literal were not blocked.
5. Legacy contamination:
   - Existing corrupted text inside repository is easy to carry into new features.

## Fixes Applied in This Repo
1. Restored user-facing Chinese text in `frontend/src/App.tsx`.
2. Restored readable Chinese comments/docstrings in `backend/app/services/rag_engine.py` without logic changes.
3. Enhanced checker `scripts/check_mojibake.py`:
   - `utf8_bom`
   - `private_use_char` (`[\uE000-\uF8FF]`)
   - `cjk_question_mark_in_literal` (only inside string literals)
4. Expanded enforced scan targets in:
   - `frontend/package.json` (`lint:mojibake`, `check:mojibake:warn`)
   - `.githooks/pre-commit`
5. Normalized key files to UTF-8 without BOM.

## Prevention Strategy (Repository-level)
1. Encode once, edit safely:
   - All source/docs must be UTF-8 (no BOM).
2. Never trust terminal rendering for copy-back:
   - Treat garbled terminal text as diagnostic output only.
3. Block before commit:
   - `check_mojibake.py` is mandatory in local pre-commit.
4. Validate after major edits:
   - Run mojibake check + build + py_compile before finishing.
5. Keep this archive updated:
   - Every new encoding regression must append: symptom, root cause, prevention patch.

## Standard Verification Commands
```bash
cd frontend && npm run lint:mojibake
cd ..
python scripts/check_mojibake.py --targets frontend/src backend/app/services/rag_engine.py backend/main.py CLAUDE.md
python -m py_compile backend/app/services/rag_engine.py backend/main.py scripts/check_mojibake.py
cd frontend && npm run build
```

## Do Not
1. Do not paste garbled terminal text back into source files.
2. Do not use write paths with unknown encoding defaults for source code.
3. Do not skip mojibake checks after large refactors or copy-heavy changes.
4. Do not bypass pre-commit encoding checks for convenience.
