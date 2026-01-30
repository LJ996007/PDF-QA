"""
检查 ChromaDB 中存储的文档数据
"""
import chromadb
from chromadb.config import Settings

# 连接到同一个数据库
client = chromadb.PersistentClient(
    path="./chroma_db",
    settings=Settings(anonymized_telemetry=False)
)

# 获取 collection
try:
    collection = client.get_collection("documents_v3")
    
    # 获取所有文档
    results = collection.get(
        limit=10,
        include=["documents", "metadatas"]
    )
    
    print(f"Total documents in collection: {collection.count()}")
    print(f"\nFirst 5 documents:")
    
    for i, (doc_id, doc, metadata) in enumerate(zip(
        results["ids"][:5], 
        results["documents"][:5], 
        results["metadatas"][:5]
    )):
        print(f"\n--- Document {i+1} ---")
        print(f"ID: {doc_id}")
        print(f"Content preview: {doc[:50]}...")
        print(f"Metadata: {metadata}")
        print(f"  bbox_x: {metadata.get('bbox_x')}")
        print(f"  bbox_y: {metadata.get('bbox_y')}")
        print(f"  bbox_w: {metadata.get('bbox_w')}")
        print(f"  bbox_h: {metadata.get('bbox_h')}")
        print(f"  source: {metadata.get('source')}")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
