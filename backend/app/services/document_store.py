"""
Simple local persistence for document metadata and OCR results.

Storage layout (relative to backend working dir):
  doc_store/
    documents.json
    ocr/
      {doc_id}.json
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(str(tmp_path), str(path))


class DocumentStore:
    def __init__(self, base_dir: str = "doc_store"):
        self.base_dir = Path(base_dir)
        self.index_path = self.base_dir / "documents.json"
        self.ocr_dir = self.base_dir / "ocr"
        self._lock = threading.Lock()

    def _ensure_dirs(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.ocr_dir.mkdir(parents=True, exist_ok=True)

    def _load_index_unlocked(self) -> Dict[str, Any]:
        self._ensure_dirs()
        if not self.index_path.exists():
            idx = {"version": 1, "documents": []}
            _atomic_write_json(self.index_path, idx)
            return idx
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            # Corrupt index: keep a backup and start fresh.
            backup = self.index_path.with_suffix(".corrupt.json")
            try:
                os.replace(str(self.index_path), str(backup))
            except Exception:
                pass
            idx = {"version": 1, "documents": []}
            _atomic_write_json(self.index_path, idx)
            return idx

    def _save_index_unlocked(self, idx: Dict[str, Any]) -> None:
        self._ensure_dirs()
        _atomic_write_json(self.index_path, idx)

    def load_index(self) -> Dict[str, Any]:
        with self._lock:
            return self._load_index_unlocked()

    def save_index(self, idx: Dict[str, Any]) -> None:
        with self._lock:
            self._save_index_unlocked(idx)

    def list_docs(self) -> List[Dict[str, Any]]:
        idx = self.load_index()
        docs = list(idx.get("documents") or [])

        def sort_key(d: Dict[str, Any]) -> str:
            return str(d.get("created_at") or "")

        docs.sort(key=sort_key, reverse=True)
        return docs

    def get_by_sha256(self, sha256: str) -> Optional[Dict[str, Any]]:
        sha256 = (sha256 or "").strip().lower()
        if not sha256:
            return None
        for d in self.list_docs():
            if str(d.get("sha256") or "").lower() == sha256:
                return d
        return None

    def get_by_doc_id(self, doc_id: str) -> Optional[Dict[str, Any]]:
        doc_id = (doc_id or "").strip()
        if not doc_id:
            return None
        for d in self.list_docs():
            if d.get("doc_id") == doc_id:
                return d
        return None

    def upsert_doc(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """
        Upsert by sha256 when present; otherwise by doc_id.
        Returns the stored record.
        """
        with self._lock:
            self._ensure_dirs()
            idx = self._load_index_unlocked()
            docs = idx.get("documents") or []
            sha = str(meta.get("sha256") or "").strip().lower()
            doc_id = str(meta.get("doc_id") or "").strip()

            match_i = None
            if sha:
                for i, d in enumerate(docs):
                    if str(d.get("sha256") or "").lower() == sha:
                        match_i = i
                        break
            if match_i is None and doc_id:
                for i, d in enumerate(docs):
                    if d.get("doc_id") == doc_id:
                        match_i = i
                        break

            if match_i is None:
                docs.append(meta)
                idx["documents"] = docs
                self._save_index_unlocked(idx)
                return meta

            existing = docs[match_i]
            existing.update(meta)
            docs[match_i] = existing
            idx["documents"] = docs
            self._save_index_unlocked(idx)
            return existing

    def delete_doc(self, doc_id: str) -> bool:
        with self._lock:
            self._ensure_dirs()
            idx = self._load_index_unlocked()
            docs = idx.get("documents") or []
            new_docs = [d for d in docs if d.get("doc_id") != doc_id]
            changed = len(new_docs) != len(docs)
            if changed:
                idx["documents"] = new_docs
                self._save_index_unlocked(idx)

            # Best-effort delete OCR payload.
            try:
                ocr_path = self.ocr_dir / f"{doc_id}.json"
                if ocr_path.exists():
                    ocr_path.unlink()
            except Exception:
                pass
            return changed

    def save_ocr_result(self, doc_id: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            self._ensure_dirs()
            path = self.ocr_dir / f"{doc_id}.json"
            _atomic_write_json(path, payload)

    def load_ocr_result(self, doc_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_dirs()
        path = self.ocr_dir / f"{doc_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None


document_store = DocumentStore()
