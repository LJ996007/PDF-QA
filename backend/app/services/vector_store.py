"""向量存储服务 - 基于ChromaDB的向量化和检索"""
import chromadb
from chromadb.config import Settings
from typing import List, Dict, Optional
from app.models.schemas import Paragraph
from app.config import CHROMA_DIR


class VectorStore:
    """向量存储管理器"""

    def __init__(self, collection_name: str = "pdf_paragraphs"):
        """
        初始化向量存储

        Args:
            collection_name: ChromaDB集合名称
        """
        # 初始化ChromaDB客户端（持久化存储）
        self.client = chromadb.PersistentClient(path=CHROMA_DIR)

        # 获取或创建集合
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}  # 使用余弦相似度
        )

    def add_document(self, document_id: str, paragraphs: List[Paragraph]):
        """
        添加文档的所有段落到向量存储

        Args:
            document_id: 文档ID
            paragraphs: 段落列表
        """
        if not paragraphs:
            return

        # 准备批量数据
        ids = []
        documents = []
        metadatas = []

        for para in paragraphs:
            # 唯一ID
            ids.append(para.id)

            # 文档内容（用于向量化）
            documents.append(para.text)

            # 元数据（用于过滤和检索）
            metadatas.append({
                "document_id": document_id,
                "page_number": str(para.page_number),
                "x0": str(para.bbox.get("x0", 0)),
                "y0": str(para.bbox.get("y0", 0)),
                "x1": str(para.bbox.get("x1", 0)),
                "y1": str(para.bbox.get("y1", 0)),
                "text": para.text  # 存储原文用于返回
            })

        # 批量添加到ChromaDB
        self.collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas
        )

    def search(
        self,
        query: str,
        document_id: Optional[str] = None,
        top_k: int = 5
    ) -> List[Dict]:
        """
        检索与查询最相关的段落

        Args:
            query: 查询文本
            document_id: 限定在特定文档中检索（可选）
            top_k: 返回结果数量

        Returns:
            相关段落列表，包含段落信息和位置坐标
        """
        # 构建过滤条件
        where = None
        if document_id:
            where = {"document_id": document_id}

        # 执行检索
        results = self.collection.query(
            query_texts=[query],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"]
        )

        # 格式化结果
        paragraphs = []
        if results["ids"] and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                metadata = results["metadatas"][0][i]
                paragraphs.append({
                    "id": results["ids"][0][i],
                    "text": metadata.get("text", ""),
                    "page": int(metadata.get("page_number", 1)),
                    "bbox": {
                        "x0": float(metadata.get("x0", 0)),
                        "y0": float(metadata.get("y0", 0)),
                        "x1": float(metadata.get("x1", 0)),
                        "y1": float(metadata.get("y1", 0))
                    },
                    "score": 1 - results["distances"][0][i]  # 转换为相似度分数
                })

        return paragraphs

    def get_paragraph_by_id(self, paragraph_id: str) -> Optional[Dict]:
        """
        根据段落ID获取详细信息

        Args:
            paragraph_id: 段落ID

        Returns:
            段落信息，如果不存在返回None
        """
        results = self.collection.get(
            ids=[paragraph_id],
            include=["metadatas"]
        )

        if results["ids"] and results["metadatas"]:
            metadata = results["metadatas"][0]
            return {
                "id": paragraph_id,
                "text": metadata.get("text", ""),
                "page": int(metadata.get("page_number", 1)),
                "bbox": {
                    "x0": float(metadata.get("x0", 0)),
                    "y0": float(metadata.get("y0", 0)),
                    "x1": float(metadata.get("x1", 0)),
                    "y1": float(metadata.get("y1", 0))
                }
            }
        return None

    def delete_document(self, document_id: str):
        """
        删除文档的所有段落

        Args:
            document_id: 文档ID
        """
        # 获取该文档的所有段落ID
        results = self.collection.get(
            where={"document_id": document_id}
        )

        if results["ids"]:
            self.collection.delete(ids=results["ids"])

    def get_collection_stats(self) -> Dict:
        """
        获取集合统计信息

        Returns:
            统计信息字典
        """
        count = self.collection.count()
        return {
            "total_paragraphs": count,
            "collection_name": self.collection.name
        }
