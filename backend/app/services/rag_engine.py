"""
RAG 寮曟搸锛氬熀浜?ChromaDB 鐨勬绱笌绱㈠紩鏈嶅姟銆?
"""
import chromadb
from chromadb.config import Settings
import hashlib
import logging
import os
import re
import json
from typing import List, Optional
import httpx


from app.models.schemas import TextChunk, PageContent, BoundingBox

logger = logging.getLogger(__name__)

try:
    from rank_bm25 import BM25Okapi
    import jieba
    HAS_BM25 = True
except ImportError:
    HAS_BM25 = False
    print("Warning: rank_bm25 or jieba not found. Hybrid search disabled.")


class RAGEngine:
    """RAG 妫€绱㈠紩鎿庯紝璐熻矗鍚戦噺绱㈠紩銆佹贩鍚堟绱笌寮曠敤瀹氫綅銆?"""
    
    def __init__(self, persist_directory: str = "./chroma_db"):
        self.client = chromadb.PersistentClient(
            path=persist_directory,
            settings=Settings(anonymized_telemetry=False)
        )
        self.collection = self.client.get_or_create_collection(
            name="documents_v3",  # 浣跨敤 v3 闆嗗悎锛岀‘淇濆悜閲忕淮搴︿负 2048銆?
            metadata={"hnsw:space": "cosine"}
        )

        self.zhipu_api_key = os.getenv("ZHIPU_API_KEY", "")
        self.bm25_cache = {} # Cache for BM25 indices: {doc_id: {'model': bm25, 'ids': [], 'texts': []}}
    
    async def _get_embeddings(self, texts: List[str], api_key: Optional[str] = None) -> List[List[float]]:
        """
        鑾峰彇鏂囨湰鍚戦噺锛堜紭鍏堜娇鐢ㄦ櫤璋?API锛夈€?
        """
        final_api_key = api_key or self.zhipu_api_key
        
        if not final_api_key:
            # 鏃?API Key 鏃朵娇鐢ㄥ搱甯屽悜閲忥紙浠呯敤浜庢祴璇曞拰鍏滃簳锛夈€?
            return [self._simple_hash_embedding(text) for text in texts]
        
        headers = {
            "Authorization": f"Bearer {final_api_key}",
            "Content-Type": "application/json"
        }
        
        embeddings = []
        
        # 鎵归噺澶勭悊锛屾瘡娆℃渶澶?10 鏉°€?
        for i in range(0, len(texts), 10):
            batch = texts[i:i+10]
            
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        "https://open.bigmodel.cn/api/paas/v4/embeddings",
                        headers=headers,
                        json={
                            "model": "embedding-3",
                            "input": batch
                        }
                    )
                    response.raise_for_status()
                    result = response.json()
                    
                    for item in result["data"]:
                        embeddings.append(item["embedding"])
            except Exception as e:
                print(f"Embedding generation failed: {e}")
                import traceback
                traceback.print_exc()
                # Fallback to hash embedding on error
                for text in batch:
                    embeddings.append(self._simple_hash_embedding(text))
        
        return embeddings
    
    def _simple_hash_embedding(self, text: str, dim: int = 2048) -> List[float]:
        """鐢熸垚绠€鏄撳搱甯屽悜閲忥紙鏃?API Key 鏃跺厹搴曪級銆?"""
        import hashlib
        hash_bytes = hashlib.sha256(text.encode()).digest()
        # 鎵╁睍鍒版寚瀹氱淮搴︺€?
        embedding = []
        for i in range(dim):
            byte_idx = i % len(hash_bytes)
            embedding.append((hash_bytes[byte_idx] - 128) / 128.0)
        return embedding

    def _is_low_value_text(self, text: str) -> bool:
        """
        Heuristic filter to drop OCR noise (single letters, pure punctuation, etc.).

        This improves retrieval quality on scanned PDFs where OCR can emit many tiny fragments.
        """
        t = (text or "").strip()
        if not t:
            return True

        compact = re.sub(r"\s+", "", t)
        if len(compact) <= 2:
            return True

        if re.fullmatch(r"[\W_]+", compact, flags=re.UNICODE):
            return True

        has_cjk = any("\u4e00" <= c <= "\u9fff" for c in compact)
        if has_cjk and len(compact) <= 3:
            return True

        if compact.isascii():
            # Drop short ASCII-only words/numbers (common OCR artifacts).
            if re.fullmatch(r"[A-Za-z]{1,4}", compact):
                return True
            if re.fullmatch(r"\d{1,2}", compact):
                return True
            if len(compact) <= 4 and not re.fullmatch(r"\d{3,4}", compact):
                return True

        return False

    def _normalize_for_dedup(self, text: str) -> str:
        t = (text or "").strip().lower()
        if not t:
            return ""
        t = re.sub(r"\s+", " ", t)
        t = re.sub(r"[^\w\u4e00-\u9fff ]+", "", t)
        return t.strip()

    def _collect_header_footer_repeat_keys(self, pages: List[PageContent]) -> set:
        if len(pages) < 3:
            return set()

        page_freq = {}
        for page in pages:
            lines = [ln.strip() for ln in (page.text or "").split("\n") if ln.strip()]
            if not lines:
                continue

            sample = lines[:3]
            if len(lines) > 3:
                sample.extend(lines[-3:])

            seen = set()
            for line in sample:
                if self._is_low_value_text(line):
                    continue
                key = self._normalize_for_dedup(line)
                if len(key) < 6:
                    continue
                seen.add(key)

            for key in seen:
                page_freq[key] = page_freq.get(key, 0) + 1

        threshold = max(3, int(len(pages) * 0.35))
        return {k for k, v in page_freq.items() if v >= threshold}

    def _select_best_line_index(self, query: str, lines: List[str]) -> int:
        """
        Pick the most relevant line inside a multi-line chunk for tighter highlight bbox.

        We keep chunk-level retrieval for recall, but return a line-level bbox for precision.
        """
        if not lines:
            return 0
        q = (query or "").strip()
        if not q:
            return 0

        # Extract meaningful tokens (works even when jieba is unavailable).
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{2,}|\d{2,}", q)
        if not tokens:
            tokens = [q]

        best_i = 0
        best_score = -1
        for i, line in enumerate(lines):
            t = (line or "").strip()
            if not t:
                continue
            score = 0
            for tok in tokens:
                if tok and tok in t:
                    score += 2
            # Small bonus for longer informative lines.
            score += min(len(t), 120) / 120.0
            if score > best_score:
                best_score = score
                best_i = i
        return best_i
    
    def _chunk_text(self, text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
        """
        鎸夐暱搴︿笌璇箟鏂偣鍒囧垎鏂囨湰銆?
        """
        if len(text) <= chunk_size:
            return [text] if text.strip() else []
        
        chunks = []
        start = 0
        
        while start < len(text):
            end = start + chunk_size
            
            # 浼樺厛鍦ㄥ彞瀛愯竟鐣屽鍒囧垎锛屽噺灏戣涔夋柇瑁傘€?
            if end < len(text):
                # 鏌ユ壘鏈€杩戠殑鏍囩偣鎴栨崲琛屻€?
                for sep in ["\n\n", "。", ".", "\n", "；", ";", "，", ","]:
                    last_sep = text[start:end].rfind(sep)
                    if last_sep > chunk_size // 2:
                        end = start + last_sep + len(sep)
                        break
            
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            
            start = end - overlap
        
        return chunks
    
    async def index_document(self, doc_id: str, pages: List[PageContent], api_key: Optional[str] = None) -> int:
        """
        寤虹珛鏂囨。绱㈠紩銆?
        杩斿洖: 绱㈠紩鍚庣殑 chunk 鏁伴噺銆?
        """
        all_chunks = []
        all_ids = []
        all_metadatas = []
        header_footer_repeat_keys = self._collect_header_footer_repeat_keys(pages)
        seen_cross_page_chunks = set()
        
        # 鍏ㄥ眬 chunk 璁℃暟鍣紙鐢ㄤ簬鐢熸垚 b0001 杩欑被 block_id锛夈€?
        global_chunk_count = 0

        for page in pages:
            if not page.text:
                continue
                
            # 妫€鏌ユ槸鍚﹀瓨鍦ㄥ潗鏍囨暟鎹紙OCR 鎴栧師鐢熸枃鏈級銆?
            has_coords = page.coordinates and len(page.coordinates) > 0
            
            if has_coords:
                # 鏈夌簿纭潗鏍囨椂锛屾寜琛屾瀯寤哄潡銆?
                text_lines = page.text.split('\n')
                # Build (text, bbox) entries first (skip OCR noise), then merge consecutive
                # lines into slightly larger chunks for better retrieval quality.
                entries = []
                for idx, text in enumerate(text_lines):
                    text = text.strip()
                    if not text or self._is_low_value_text(text):
                        continue
                    line_key = self._normalize_for_dedup(text)
                    if line_key and line_key in header_footer_repeat_keys:
                        continue

                    # 璇诲彇褰撳墠琛屽潗鏍囥€?
                    if idx < len(page.coordinates):
                        coord = page.coordinates[idx]
                        bbox_x = coord.x if hasattr(coord, 'x') else coord.get('x', 50)
                        bbox_y = coord.y if hasattr(coord, 'y') else coord.get('y', 50)
                        bbox_w = coord.w if hasattr(coord, 'w') else coord.get('w', 400)
                        bbox_h = coord.h if hasattr(coord, 'h') else coord.get('h', 30)
                    else:
                        # 鍧愭爣缂哄け鏃朵娇鐢ㄤ及绠楀€笺€?
                        bbox_x = 50
                        bbox_y = (1.0 - idx / max(len(text_lines), 1)) * 700
                        bbox_w = 500
                        bbox_h = 30

                    entries.append((text, float(bbox_x), float(bbox_y), float(bbox_w), float(bbox_h)))

                coord_chunk_chars = 320
                coord_chunk_max_lines = 8

                current_texts = []
                current_bbox = None  # (x0, y0, x1, y1)
                current_line_bboxes = []  # list of per-line bbox dicts in the same order as current_texts

                def flush_current():
                    nonlocal global_chunk_count, current_texts, current_bbox, current_line_bboxes
                    if not current_texts or not current_bbox:
                        current_texts = []
                        current_bbox = None
                        current_line_bboxes = []
                        return

                    chunk_text = "\n".join(current_texts).strip()
                    if not chunk_text or self._is_low_value_text(chunk_text):
                        current_texts = []
                        current_bbox = None
                        current_line_bboxes = []
                        return

                    dedup_key = self._normalize_for_dedup(chunk_text)
                    if dedup_key and len(dedup_key) >= 80:
                        if dedup_key in seen_cross_page_chunks:
                            current_texts = []
                            current_bbox = None
                            current_line_bboxes = []
                            return
                        seen_cross_page_chunks.add(dedup_key)

                    global_chunk_count += 1
                    block_id = f"b{global_chunk_count:04d}"  # b0001, b0002...
                    chunk_id = f"{doc_id}_{block_id}"  # Unique ID for Chroma

                    x0, y0, x1, y1 = current_bbox
                    all_chunks.append(chunk_text)
                    all_ids.append(chunk_id)
                    all_metadatas.append({
                        "doc_id": doc_id,
                        "page": page.page_number,
                        "source": page.type,
                        "block_id": block_id,
                        "bbox_x": float(x0),
                        "bbox_y": float(y0),
                        "bbox_w": float(x1 - x0),
                        "bbox_h": float(y1 - y0),
                        # Store per-line boxes so we can return a tighter highlight bbox later.
                        "bbox_lines": json.dumps(current_line_bboxes, ensure_ascii=False),
                    })

                    current_texts = []
                    current_bbox = None
                    current_line_bboxes = []

                for text, bbox_x, bbox_y, bbox_w, bbox_h in entries:
                    x0 = bbox_x
                    y0 = bbox_y
                    x1 = bbox_x + bbox_w
                    y1 = bbox_y + bbox_h

                    if not current_texts:
                        current_texts = [text]
                        current_bbox = (x0, y0, x1, y1)
                        current_line_bboxes = [{"x": x0, "y": y0, "w": bbox_w, "h": bbox_h}]
                        continue

                    # Approx length with newlines.
                    current_len = sum(len(t) for t in current_texts) + max(len(current_texts) - 1, 0)
                    next_len = current_len + 1 + len(text)

                    if next_len > coord_chunk_chars or len(current_texts) >= coord_chunk_max_lines:
                        flush_current()
                        current_texts = [text]
                        current_bbox = (x0, y0, x1, y1)
                        current_line_bboxes = [{"x": x0, "y": y0, "w": bbox_w, "h": bbox_h}]
                        continue

                    current_texts.append(text)
                    current_line_bboxes.append({"x": x0, "y": y0, "w": bbox_w, "h": bbox_h})
                    cx0, cy0, cx1, cy1 = current_bbox
                    current_bbox = (min(cx0, x0), min(cy0, y0), max(cx1, x1), max(cy1, y1))

                flush_current()
            else:
                # 鏃犵簿纭潗鏍囨椂锛屾寜娈佃惤鍒囧潡銆?
                text_chunks = self._chunk_text(page.text)

                for idx, chunk_text in enumerate(text_chunks):
                    if self._is_low_value_text(chunk_text):
                        continue
                    dedup_key = self._normalize_for_dedup(chunk_text)
                    if dedup_key and len(dedup_key) >= 80:
                        if dedup_key in seen_cross_page_chunks:
                            continue
                        seen_cross_page_chunks.add(dedup_key)
                    global_chunk_count += 1
                    block_id = f"b{global_chunk_count:04d}"
                    chunk_id = f"{doc_id}_{block_id}"
                    
                    y_ratio = 1.0 - (idx / max(len(text_chunks), 1))

                    all_chunks.append(chunk_text)
                    all_ids.append(chunk_id)
                    all_metadatas.append({
                        "doc_id": doc_id,
                        "page": page.page_number,
                        "source": page.type,
                        "block_id": block_id,
                        "bbox_x": 50,
                        "bbox_y": y_ratio * 700,
                        "bbox_w": 500,
                        "bbox_h": 50
                    })
        
        if not all_chunks:
            return 0
        
        # 鐢熸垚鍚戦噺銆?
        embeddings = await self._get_embeddings(all_chunks, api_key)
        
        # 鍐欏叆 ChromaDB銆?
        self.collection.add(
            ids=all_ids,
            embeddings=embeddings,
            documents=all_chunks,
            metadatas=all_metadatas
        )
        
        print(f"[RAG] Indexed {len(all_chunks)} chunks, first bbox: {all_metadatas[0] if all_metadatas else 'N/A'}")
        
        # 绱㈠紩鏇存柊鍚庝娇 BM25 缂撳瓨澶辨晥銆?
        self._invalidate_bm25_cache(doc_id)
        
        return len(all_chunks)
    
    async def index_ocr_result(
        self, 
        doc_id: str, 
        page_number: int, 
        chunks: List[dict],
        api_key: Optional[str] = None
    ) -> int:
        """
        绱㈠紩 OCR 缁撴灉銆?
        """
        all_chunks = []
        all_ids = []
        all_metadatas = []
        
        for idx, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}_p{page_number}_ocr{idx}"
            text = chunk.get("text", "")
            bbox = chunk.get("bbox", {})
            
            if not text.strip():
                continue
            
            all_chunks.append(text)
            all_ids.append(chunk_id)
            all_metadatas.append({
                "doc_id": doc_id,
                "page": page_number,
                "source": "ocr",
                "bbox_x": bbox.get("x", 0),
                "bbox_y": bbox.get("y", 0),
                "bbox_w": bbox.get("w", 100),
                "bbox_h": bbox.get("h", 20)
            })
        
        if not all_chunks:
            return 0
        
        embeddings = await self._get_embeddings(all_chunks, api_key)
        
        self.collection.add(
            ids=all_ids,
            embeddings=embeddings,
            documents=all_chunks,
            metadatas=all_metadatas
        )
        
        return len(all_chunks)
    


    def _tokenize(self, text: str) -> List[str]:
        """浣跨敤 jieba 杩涜涓枃鍒嗚瘝銆?"""
        if not HAS_BM25:
            return text.split()
        return list(jieba.cut_for_search(text))

    def _ensure_bm25_index(self, doc_id: str):
        """纭繚鎸囧畾鏂囨。鐨?BM25 绱㈠紩宸叉瀯寤恒€?"""
        if not HAS_BM25:
            return

        if doc_id in self.bm25_cache:
            return

        print(f"[Hybrid] Building BM25 index for {doc_id}...")
        # 1. 浠?ChromaDB 璇诲彇鏂囨。鐨勫叏閮ㄥ垎鍧椼€?
        try:
            results = self.collection.get(
                where={"doc_id": doc_id},
                include=["documents"]
            )
            
            if not results or not results["documents"]:
                print(f"[Hybrid] No documents found for {doc_id}")
                self.bm25_cache[doc_id] = {
                    "model": None,
                    "ids": [],
                    "texts": []
                }
                return

            ids = results["ids"]
            texts = results["documents"]
            
            # 2. 鍒嗚瘝銆?
            tokenized_corpus = [self._tokenize(doc) for doc in texts]
            
            # 3. 鏋勫缓 BM25 绱㈠紩銆?
            bm25 = BM25Okapi(tokenized_corpus)
            
            self.bm25_cache[doc_id] = {
                "model": bm25,
                "ids": ids,
                "texts": texts
            }
            print(f"[Hybrid] BM25 index built for {doc_id}, chunks: {len(ids)}")
            
        except Exception as e:
            print(f"[Hybrid] Index build failed: {e}")
            import traceback
            traceback.print_exc()

    def _invalidate_bm25_cache(self, doc_id: str):
        """浣挎寚瀹氭枃妗ｇ殑 BM25 缂撳瓨澶辨晥銆?"""
        if doc_id in self.bm25_cache:
            del self.bm25_cache[doc_id]
            print(f"[Hybrid] Cache invalidated for {doc_id}")

    async def retrieve(
        self,
        query: str,
        doc_id: str,
        top_k: int = 5,
        api_key: Optional[str] = None,
        allowed_pages: Optional[List[int]] = None,
        ensure_page_coverage: bool = False,
    ) -> List[TextChunk]:
        """
        妫€绱㈢浉鍏虫枃鏈潡骞惰繑鍥炲甫鍧愭爣鐨勫紩鐢ㄣ€?
        """
        allowed_page_set = set(allowed_pages or [])
        allowed_page_list = sorted(allowed_page_set)
        coverage_enabled = ensure_page_coverage and bool(allowed_page_set) and len(allowed_page_set) <= 20
        if allowed_pages is not None and not allowed_page_set:
            return []
        if coverage_enabled and len(allowed_page_list) > top_k:
            message = f"page_coverage_limited doc_id={doc_id} allowed_pages={len(allowed_page_list)} top_k={top_k}"
            print(message, flush=True)
            logger.info(message)

        # 鐢熸垚鏌ヨ鍚戦噺銆?
        query_embedding = (await self._get_embeddings([query], api_key))[0]

        # 鎵ц娣峰悎妫€绱紙RRF 铻嶅悎锛夈€?


        # 1. 鍚戦噺妫€绱紙Vector Search锛夈€?
        # Get enough candidates so we can filter OCR noise and still return top_k results.
        k_vector = max(top_k * 10, 50)
        vector_results = self.collection.query(
            query_embeddings=[query_embedding],
            where={"doc_id": doc_id},
            n_results=k_vector
        )

        # 2. 鍏抽敭璇嶆绱紙BM25 Search锛夈€?
        # 鍏堢‘淇?BM25 绱㈠紩瀛樺湪銆?
        self._ensure_bm25_index(doc_id)

        bm25_top_n = []
        if HAS_BM25 and doc_id in self.bm25_cache:
            cache = self.bm25_cache[doc_id]
            bm25 = cache["model"]
            doc_ids = cache["ids"]

            if bm25:
                tokenized_query = self._tokenize(query)
                # 鑾峰彇鍏ㄩ儴寰楀垎銆?
                doc_scores = bm25.get_scores(tokenized_query)

                # 鑾峰彇 Top N 绱㈠紩锛坅rgsort 涓哄崌搴忥紝闇€鍙嶈浆锛夈€?

                import numpy as np
                top_indices = np.argsort(doc_scores)[-k_vector:][::-1]

                for idx in top_indices:
                    if doc_scores[idx] > 0:  # 浠呬繚鐣欐湁鍖归厤鍒嗘暟鐨勭粨鏋溿€?
                        bm25_top_n.append(doc_ids[idx])

        # 3. RRF 铻嶅悎锛圧eciprocal Rank Fusion锛夈€?
        # Score = 1 / (k + rank)
        rrf_k = 60
        final_scores = {}  # {chunk_id: score}

        # 鍚堝苟鍚戦噺妫€绱㈢粨鏋溿€?
        if vector_results["ids"] and vector_results["ids"][0]:
            for rank, chunk_id in enumerate(vector_results["ids"][0]):
                final_scores[chunk_id] = final_scores.get(chunk_id, 0) + (1 / (rrf_k + rank + 1))

        # 鍚堝苟 BM25 缁撴灉銆?
        for rank, chunk_id in enumerate(bm25_top_n):
            final_scores[chunk_id] = final_scores.get(chunk_id, 0) + (1 / (rrf_k + rank + 1))

        # 4. 鎺掑簭骞惰幏鍙?chunk 璇︽儏銆?

        candidate_ids = sorted(final_scores.keys(), key=lambda x: final_scores[x], reverse=True)

        if not candidate_ids:
            return []

        # 鎵归噺鑾峰彇 chunk 璇︽儏銆?
        # ChromaDB .get()
        candidate_limit = max(top_k * 20, 200)
        candidate_ids = candidate_ids[:candidate_limit]
        final_chunks_data = self.collection.get(
            ids=candidate_ids,
            include=["documents", "metadatas"]
        )

        # 鎸?candidate_ids 椤哄簭鏋勫缓杩斿洖瀵硅薄銆?
        id_map = {id_: i for i, id_ in enumerate(final_chunks_data["ids"])}

        candidate_chunks: List[TextChunk] = []
        for chunk_id in candidate_ids:
            if chunk_id not in id_map:
                continue

            idx = id_map[chunk_id]
            metadata = final_chunks_data["metadatas"][idx]
            content = final_chunks_data["documents"][idx]
            page_number = metadata["page"]

            if allowed_page_set and page_number not in allowed_page_set:
                continue

            if self._is_low_value_text(content):
                continue

            # Tighten highlight bbox when we have per-line bboxes stored in metadata.
            bbox_x = metadata["bbox_x"]
            bbox_y = metadata["bbox_y"]
            bbox_w = metadata["bbox_w"]
            bbox_h = metadata["bbox_h"]
            bbox_lines_raw = metadata.get("bbox_lines")
            if bbox_lines_raw:
                try:
                    bbox_lines = json.loads(bbox_lines_raw) if isinstance(bbox_lines_raw, str) else None
                except Exception:
                    bbox_lines = None

                if isinstance(bbox_lines, list) and bbox_lines:
                    lines = content.split("\n")
                    li = self._select_best_line_index(query, lines[: len(bbox_lines)])
                    li = max(0, min(li, len(bbox_lines) - 1))
                    b = bbox_lines[li] or {}
                    if all(k in b for k in ("x", "y", "w", "h")):
                        bbox_x = float(b["x"])
                        bbox_y = float(b["y"])
                        bbox_w = float(b["w"])
                        bbox_h = float(b["h"])

            candidate_chunks.append(TextChunk(
                id=chunk_id,
                document_id=doc_id,
                page_number=page_number,
                content=content,
                bbox=BoundingBox(
                    page=page_number,
                    x=bbox_x,
                    y=bbox_y,
                    w=bbox_w,
                    h=bbox_h
                ),
                source_type=metadata["source"],
                distance=0,  # Hybrid search score makes distance confusing, setting to 0 or could set to 1/score
                ref_id=None,
                block_id=metadata.get("block_id")
            ))

            if len(candidate_chunks) >= candidate_limit:
                break

        if not candidate_chunks:
            return []

        if coverage_enabled:
            best_by_page = {}
            for chunk in candidate_chunks:
                if chunk.page_number not in best_by_page:
                    best_by_page[chunk.page_number] = chunk

            selected_chunks: List[TextChunk] = []
            selected_ids = set()

            for page_num in allowed_page_list:
                page_chunk = best_by_page.get(page_num)
                if not page_chunk:
                    continue
                selected_chunks.append(page_chunk)
                selected_ids.add(page_chunk.id)
                if len(selected_chunks) >= top_k:
                    break

            if len(selected_chunks) < top_k:
                for chunk in candidate_chunks:
                    if chunk.id in selected_ids:
                        continue
                    selected_chunks.append(chunk)
                    selected_ids.add(chunk.id)
                    if len(selected_chunks) >= top_k:
                        break

            chunks = selected_chunks[:top_k]
        else:
            chunks = candidate_chunks[:top_k]

        for idx, chunk in enumerate(chunks):
            chunk.ref_id = f"ref-{idx + 1}"

        return chunks

    def delete_document(self, doc_id: str):
        """鍒犻櫎鎸囧畾鏂囨。鐨勫叏閮ㄧ储寮曟暟鎹€?"""
        try:
            self.collection.delete(where={"doc_id": doc_id})
            self._invalidate_bm25_cache(doc_id)
        except Exception:
            pass


# 鍏ㄥ眬瀹炰緥
rag_engine = RAGEngine()





