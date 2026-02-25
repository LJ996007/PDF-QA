"""
Simple local persistence for document metadata and OCR results.

Storage layout (relative to backend working dir):
  doc_store/
    documents.json
    ocr/
      {doc_id}.json
    chat/
      {doc_id}.json
    compliance/
      {doc_id}.json
    compliance_v2/
      {doc_id}.json
    evidence/
      {doc_id}.json
    review/
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
        self.chat_dir = self.base_dir / "chat"
        self.compliance_dir = self.base_dir / "compliance"
        self.compliance_v2_dir = self.base_dir / "compliance_v2"
        self.evidence_dir = self.base_dir / "evidence"
        self.review_dir = self.base_dir / "review"
        self._lock = threading.RLock()

    def _ensure_dirs(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.ocr_dir.mkdir(parents=True, exist_ok=True)
        self.chat_dir.mkdir(parents=True, exist_ok=True)
        self.compliance_dir.mkdir(parents=True, exist_ok=True)
        self.compliance_v2_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.review_dir.mkdir(parents=True, exist_ok=True)

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
            # Capture existing record for best-effort cleanup.
            existing = next((d for d in docs if d.get("doc_id") == doc_id), None)
            new_docs = [d for d in docs if d.get("doc_id") != doc_id]
            changed = len(new_docs) != len(docs)
            if changed:
                idx["documents"] = new_docs
                self._save_index_unlocked(idx)

            # Best-effort delete persisted PDF (when keep_pdf=1).
            try:
                pdf_path = (existing or {}).get("pdf_path")
                if pdf_path and os.path.exists(pdf_path):
                    os.remove(pdf_path)
            except Exception:
                pass

            # Best-effort delete OCR payload.
            try:
                ocr_path = self.ocr_dir / f"{doc_id}.json"
                if ocr_path.exists():
                    ocr_path.unlink()
            except Exception:
                pass

            # Best-effort delete v2 compliance payloads.
            try:
                v2_path = self.compliance_v2_dir / f"{doc_id}.json"
                if v2_path.exists():
                    v2_path.unlink()
            except Exception:
                pass
            try:
                evidence_path = self.evidence_dir / f"{doc_id}.json"
                if evidence_path.exists():
                    evidence_path.unlink()
            except Exception:
                pass
            try:
                review_path = self.review_dir / f"{doc_id}.json"
                if review_path.exists():
                    review_path.unlink()
            except Exception:
                pass
            return changed

    def _load_chat_unlocked(self, doc_id: str) -> Dict[str, Any]:
        self._ensure_dirs()
        path = self.chat_dir / f"{doc_id}.json"
        if not path.exists():
            data = {"version": 1, "doc_id": doc_id, "messages": []}
            _atomic_write_json(path, data)
            return data
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("invalid chat data")
            if "messages" not in data or not isinstance(data.get("messages"), list):
                data["messages"] = []
            data.setdefault("version", 1)
            data.setdefault("doc_id", doc_id)
            return data
        except Exception:
            data = {"version": 1, "doc_id": doc_id, "messages": []}
            _atomic_write_json(path, data)
            return data

    def load_chat(self, doc_id: str) -> Dict[str, Any]:
        with self._lock:
            return self._load_chat_unlocked(doc_id)

    def append_chat(self, doc_id: str, user_message: Dict[str, Any], assistant_message: Dict[str, Any]) -> None:
        with self._lock:
            self._ensure_dirs()
            path = self.chat_dir / f"{doc_id}.json"
            data = self._load_chat_unlocked(doc_id)
            msgs = list(data.get("messages") or [])
            msgs.append(user_message)
            msgs.append(assistant_message)
            data["messages"] = msgs
            _atomic_write_json(path, data)

    def delete_chat(self, doc_id: str) -> None:
        with self._lock:
            self._ensure_dirs()
            try:
                path = self.chat_dir / f"{doc_id}.json"
                if path.exists():
                    path.unlink()
            except Exception:
                pass

    def save_compliance(self, doc_id: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            self._ensure_dirs()
            path = self.compliance_dir / f"{doc_id}.json"
            _atomic_write_json(path, payload)

    def load_compliance(self, doc_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_dirs()
        path = self.compliance_dir / f"{doc_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            return data
        except Exception:
            return None

    def delete_compliance(self, doc_id: str) -> None:
        with self._lock:
            self._ensure_dirs()
            try:
                path = self.compliance_dir / f"{doc_id}.json"
                if path.exists():
                    path.unlink()
            except Exception:
                pass

    def save_compliance_v2(self, doc_id: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            self._ensure_dirs()
            path = self.compliance_v2_dir / f"{doc_id}.json"
            _atomic_write_json(path, payload)

    def load_compliance_v2(self, doc_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_dirs()
        path = self.compliance_v2_dir / f"{doc_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def delete_compliance_v2(self, doc_id: str) -> None:
        with self._lock:
            self._ensure_dirs()
            try:
                path = self.compliance_v2_dir / f"{doc_id}.json"
                if path.exists():
                    path.unlink()
            except Exception:
                pass

    def save_evidence(self, doc_id: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            self._ensure_dirs()
            path = self.evidence_dir / f"{doc_id}.json"
            _atomic_write_json(path, payload)

    def load_evidence(self, doc_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_dirs()
        path = self.evidence_dir / f"{doc_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def delete_evidence(self, doc_id: str) -> None:
        with self._lock:
            self._ensure_dirs()
            try:
                path = self.evidence_dir / f"{doc_id}.json"
                if path.exists():
                    path.unlink()
            except Exception:
                pass

    def save_review(self, doc_id: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            self._ensure_dirs()
            path = self.review_dir / f"{doc_id}.json"
            _atomic_write_json(path, payload)

    def load_review(self, doc_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_dirs()
        path = self.review_dir / f"{doc_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def delete_review(self, doc_id: str) -> None:
        with self._lock:
            self._ensure_dirs()
            try:
                path = self.review_dir / f"{doc_id}.json"
                if path.exists():
                    path.unlink()
            except Exception:
                pass

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
