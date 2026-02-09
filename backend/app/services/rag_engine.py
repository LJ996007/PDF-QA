"""
RAG引擎 - ChromaDB向量检索
"""
import chromadb
from chromadb.config import Settings
import hashlib
import os
import re
import json
from typing import List, Optional
import httpx


from app.models.schemas import TextChunk, PageContent, BoundingBox

try:
    from rank_bm25 import BM25Okapi
    import jieba
    HAS_BM25 = True
except ImportError:
    HAS_BM25 = False
    print("Warning: rank_bm25 or jieba not found. Hybrid search disabled.")


class RAGEngine:
    """RAG检索引擎，基于ChromaDB"""
    
    def __init__(self, persist_directory: str = "./chroma_db"):
        self.client = chromadb.PersistentClient(
            path=persist_directory,
            settings=Settings(anonymized_telemetry=False)
        )
        self.collection = self.client.get_or_create_collection(
            name="documents_v3",  # 使用v3集合以确保全新的维度(2048)
            metadata={"hnsw:space": "cosine"}
        )

        self.zhipu_api_key = os.getenv("ZHIPU_API_KEY", "")
        self.bm25_cache = {} # Cache for BM25 indices: {doc_id: {'model': bm25, 'ids': [], 'texts': []}}
    
    async def _get_embeddings(self, texts: List[str], api_key: Optional[str] = None) -> List[List[float]]:
        """
        获取文本向量（使用智谱API）
        """
        final_api_key = api_key or self.zhipu_api_key
        
        if not final_api_key:
            # 如果没有API Key，使用简单的哈希向量（仅用于测试）
            return [self._simple_hash_embedding(text) for text in texts]
        
        headers = {
            "Authorization": f"Bearer {final_api_key}",
            "Content-Type": "application/json"
        }
        
        embeddings = []
        
        # 批量处理，每次最多10个
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
        """简单哈希向量（用于测试，无API Key时）"""
        import hashlib
        hash_bytes = hashlib.sha256(text.encode()).digest()
        # 扩展到指定维度
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
        文本切分
        """
        if len(text) <= chunk_size:
            return [text] if text.strip() else []
        
        chunks = []
        start = 0
        
        while start < len(text):
            end = start + chunk_size
            
            # 尝试在句子结尾处切分
            if end < len(text):
                # 找最近的句号、换行等
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
        建立文档索引
        返回: 索引的chunk数量
        """
        all_chunks = []
        all_ids = []
        all_metadatas = []
        
        # 全局 chunk 计数器 (用于生成 b0001)
        global_chunk_count = 0

        for page in pages:
            if not page.text:
                continue
                
            # 检查是否有OCR坐标数据
            has_coords = page.coordinates and len(page.coordinates) > 0
            
            if has_coords:
                # 有精确坐标（OCR或原生文本），按行/块索引
                text_lines = page.text.split('\n')
                # Build (text, bbox) entries first (skip OCR noise), then merge consecutive
                # lines into slightly larger chunks for better retrieval quality.
                entries = []
                for idx, text in enumerate(text_lines):
                    text = text.strip()
                    if not text or self._is_low_value_text(text):
                        continue

                    # 获取对应的坐标
                    if idx < len(page.coordinates):
                        coord = page.coordinates[idx]
                        bbox_x = coord.x if hasattr(coord, 'x') else coord.get('x', 50)
                        bbox_y = coord.y if hasattr(coord, 'y') else coord.get('y', 50)
                        bbox_w = coord.w if hasattr(coord, 'w') else coord.get('w', 400)
                        bbox_h = coord.h if hasattr(coord, 'h') else coord.get('h', 30)
                    else:
                        # 没有坐标时使用估算
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
                # 无精确坐标：按段落切分
                text_chunks = self._chunk_text(page.text)

                for idx, chunk_text in enumerate(text_chunks):
                    if self._is_low_value_text(chunk_text):
                        continue
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
        
        # 获取向量
        embeddings = await self._get_embeddings(all_chunks, api_key)
        
        # 存入ChromaDB
        self.collection.add(
            ids=all_ids,
            embeddings=embeddings,
            documents=all_chunks,
            metadatas=all_metadatas
        )
        
        print(f"[RAG] Indexed {len(all_chunks)} chunks, first bbox: {all_metadatas[0] if all_metadatas else 'N/A'}")
        
        # 失效缓存
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
        索引OCR结果
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
        """使用jieba进行中文分词"""
        if not HAS_BM25:
            return text.split()
        return list(jieba.cut_for_search(text))

    def _ensure_bm25_index(self, doc_id: str):
        """确保文档的BM25索引已构建"""
        if not HAS_BM25:
            return

        if doc_id in self.bm25_cache:
            return

        print(f"[Hybrid] Building BM25 index for {doc_id}...")
        # 1. 从ChromaDB获取文档所有分块
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
            
            # 2. 分词
            tokenized_corpus = [self._tokenize(doc) for doc in texts]
            
            # 3. 构建索引
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
        """失效BM25缓存"""
        if doc_id in self.bm25_cache:
            del self.bm25_cache[doc_id]
            print(f"[Hybrid] Cache invalidated for {doc_id}")

    async def retrieve(
        self, 
        query: str, 
        doc_id: str, 
        top_k: int = 5,
        api_key: Optional[str] = None
    ) -> List[TextChunk]:
        """
        检索相关文本块
        """
        # 获取查询向量
        query_embedding = (await self._get_embeddings([query], api_key))[0]
        
        # 检索
        # 混合检索实现 (RRF Fusion)
        
        # 1. 向量检索 (Vector Search)
        # Get enough candidates so we can filter OCR noise and still return top_k results.
        k_vector = max(top_k * 10, 50)
        vector_results = self.collection.query(
            query_embeddings=[query_embedding],
            where={"doc_id": doc_id},
            n_results=k_vector
        )
        
        # 2. 关键词检索 (BM25 Search)
        # 确保索引存在
        self._ensure_bm25_index(doc_id)
        
        bm25_top_n = []
        if HAS_BM25 and doc_id in self.bm25_cache:
            cache = self.bm25_cache[doc_id]
            bm25 = cache["model"]
            doc_ids = cache["ids"]
            
            if bm25:
                tokenized_query = self._tokenize(query)
                # 获取所有分数
                doc_scores = bm25.get_scores(tokenized_query)
                
                # 获取Top N的索引
                # argsort是升序，所以取最后k个并反转
                import numpy as np
                top_indices = np.argsort(doc_scores)[-k_vector:][::-1]
                
                for idx in top_indices:
                    if doc_scores[idx] > 0: # 只保留有匹配项的结果
                        bm25_top_n.append(doc_ids[idx])
        
        # 3. RRF融合 (Reciprocal Rank Fusion)
        # Score = 1 / (k + rank)
        rrf_k = 60
        final_scores = {} # {chunk_id: score}
        
        # 处理向量结果
        if vector_results["ids"] and vector_results["ids"][0]:
            for rank, chunk_id in enumerate(vector_results["ids"][0]):
                final_scores[chunk_id] = final_scores.get(chunk_id, 0) + (1 / (rrf_k + rank + 1))
        
        # 处理BM25结果
        for rank, chunk_id in enumerate(bm25_top_n):
             final_scores[chunk_id] = final_scores.get(chunk_id, 0) + (1 / (rrf_k + rank + 1))
             
        # 4. 排序并获取Chunk详情
        # 按分数降序
        candidate_ids = sorted(final_scores.keys(), key=lambda x: final_scores[x], reverse=True)
        
        if not candidate_ids:
            return []
            
        # 批量获取Chunk详情
        # ChromaDB .get() 
        candidate_limit = max(top_k * 20, 200)
        candidate_ids = candidate_ids[:candidate_limit]
        final_chunks_data = self.collection.get(
            ids=candidate_ids,
            include=["documents", "metadatas"]
        )
        
        # 构建返回对象，需要按candidate_ids的顺序
        id_map = {id_: i for i, id_ in enumerate(final_chunks_data["ids"])}
        
        chunks = []
        for chunk_id in candidate_ids:
            if chunk_id not in id_map:
                continue
                
            idx = id_map[chunk_id]
            metadata = final_chunks_data["metadatas"][idx]
            content = final_chunks_data["documents"][idx]

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
            
            chunks.append(TextChunk(
                id=chunk_id,
                document_id=doc_id,
                page_number=metadata["page"],
                content=content,
                bbox=BoundingBox(
                    page=metadata["page"],
                    x=bbox_x,
                    y=bbox_y,
                    w=bbox_w,
                    h=bbox_h
                ),
                source_type=metadata["source"],
                distance=0, # Hybrid search score makes distance confusing, setting to 0 or could set to 1/score
                ref_id=f"ref-{len(chunks)+1}",
                block_id=metadata.get("block_id")
            ))

            if len(chunks) >= top_k:
                break
            
        return chunks
    
    def delete_document(self, doc_id: str):
        """删除文档的所有索引"""
        try:
            self.collection.delete(where={"doc_id": doc_id})
            self._invalidate_bm25_cache(doc_id)
        except Exception:
            pass


# 全局实例
rag_engine = RAGEngine()
