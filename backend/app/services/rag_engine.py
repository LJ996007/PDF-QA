"""
RAG引擎 - ChromaDB向量检索
"""
import chromadb
from chromadb.config import Settings
import hashlib
import os
from typing import List, Optional
import httpx
import json

from app.models.schemas import TextChunk, PageContent, BoundingBox


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
        
        for page in pages:
            if not page.text:
                continue
                
            # 检查是否有OCR坐标数据
            has_coords = page.coordinates and len(page.coordinates) > 0
            
            print(f"[RAG Index] Page {page.page_number}: type={page.type}, text_len={len(page.text)}, has_coords={has_coords}, coords_count={len(page.coordinates) if page.coordinates else 0}")
            
            if has_coords:
                # 有精确坐标（OCR或原生文本），按行/块索引
                text_lines = page.text.split('\n')

                for idx, text in enumerate(text_lines):
                    if not text.strip():
                        continue

                    chunk_id = f"{doc_id}_p{page.page_number}_c{idx}"

                    # 获取对应的坐标
                    if idx < len(page.coordinates):
                        coord = page.coordinates[idx]
                        bbox_x = coord.x if hasattr(coord, 'x') else coord.get('x', 50)
                        bbox_y = coord.y if hasattr(coord, 'y') else coord.get('y', 50)
                        bbox_w = coord.w if hasattr(coord, 'w') else coord.get('w', 400)
                        bbox_h = coord.h if hasattr(coord, 'h') else coord.get('h', 30)
                        
                        # 调试日志：打印前3个坐标
                        if idx < 3:
                            print(f"[RAG Index] Chunk {idx}: text='{text[:20]}...', bbox=({bbox_x:.1f},{bbox_y:.1f},{bbox_w:.1f},{bbox_h:.1f})")
                    else:
                        # 没有坐标时使用估算
                        bbox_x = 50
                        bbox_y = (1.0 - idx / max(len(text_lines), 1)) * 700
                        bbox_w = 500
                        bbox_h = 30

                    all_chunks.append(text)
                    all_ids.append(chunk_id)
                    all_metadatas.append({
                        "doc_id": doc_id,
                        "page": page.page_number,
                        "source": page.type,
                        "bbox_x": float(bbox_x),
                        "bbox_y": float(bbox_y),
                        "bbox_w": float(bbox_w),
                        "bbox_h": float(bbox_h)
                    })
            else:
                # 无精确坐标：按段落切分，使用估算坐标
                text_chunks = self._chunk_text(page.text)

                for idx, chunk_text in enumerate(text_chunks):
                    chunk_id = f"{doc_id}_p{page.page_number}_c{idx}"
                    y_ratio = 1.0 - (idx / max(len(text_chunks), 1))

                    all_chunks.append(chunk_text)
                    all_ids.append(chunk_id)
                    all_metadatas.append({
                        "doc_id": doc_id,
                        "page": page.page_number,
                        "source": page.type,
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
        results = self.collection.query(
            query_embeddings=[query_embedding],
            where={"doc_id": doc_id},
            n_results=top_k
        )
        
        if not results["ids"] or not results["ids"][0]:
            return []
        
        chunks = []
        for i, chunk_id in enumerate(results["ids"][0]):
            metadata = results["metadatas"][0][i]
            
            chunks.append(TextChunk(
                id=chunk_id,
                document_id=doc_id,
                page_number=metadata["page"],
                content=results["documents"][0][i],
                bbox=BoundingBox(
                    page=metadata["page"],
                    x=metadata["bbox_x"],
                    y=metadata["bbox_y"],
                    w=metadata["bbox_w"],
                    h=metadata["bbox_h"]
                ),
                source_type=metadata["source"],
                distance=results["distances"][0][i] if results.get("distances") else None,
                ref_id=f"ref-{i+1}"
            ))
        
        return chunks
    
    def delete_document(self, doc_id: str):
        """删除文档的所有索引"""
        try:
            self.collection.delete(where={"doc_id": doc_id})
        except Exception:
            pass


# 全局实例
rag_engine = RAGEngine()
