**PDF智能问答系统 V6.0 - 架构重构版**

## 项目概述

**核心定位**：基于 **"渐进式视觉RAG"** 的本地化文档助手，支持精确到字符坐标的双向引用跳转。

**架构升级**：
- **PDF引擎**：从"截图OCR"升级为 **PDF.js三层渲染体系**（Canvas视觉层 + TextLayer文本层 + Annotation交互层）
- **RAG增强**：引入 **ChromaDB本地向量库** + 智能OCR路由（原生文本优先，视觉OCR兜底）
- **Key管理**：简化为 **单Key模式**（仅保留智谱AI Key，平台托管DeepSeek Key池）
- **坐标系统**：完整保留PDF原生坐标系，实现"Answer → Source → PDF高亮"双向链路

---

## 一、系统架构（三层分离）

```
┌─────────────────────────────────────────────────────────────────┐
│                        前端 (React)                              │
│  ┌──────────────────┐ ┌──────────────────┐ ┌─────────────────┐  │
│  │   Canvas Layer   │ │   Text Layer     │ │ Highlight Layer │  │
│  │   (视觉渲染)      │ │   (可选中复制)    │ │   (引用高亮)     │  │
│  │  pdfjs.Canvas    │ │ 绝对定位透明层    │ │  SVG/CSS覆盖    │  │
│  └──────────────────┘ └──────────────────┘ └─────────────────┘  │
│                          ↕️ Coordinate Mapping                    │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTP/WS
┌──────────────────────────▼──────────────────────────────────────┐
│                      后端 (FastAPI)                              │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Router Layer                                            │  │
│  │  ├── /upload   -> PDF预处理 + 智能分流(Native/OCR)       │  │
│  │  ├── /ocr/{id} -> 按需OCR (SSE流式返回)                  │  │
│  │  └── /chat     -> RAG检索 + DeepSeek推理                 │  │
│  └──────────────────┬──────────────────┬─────────────────────┘  │
│                     ▼                  ▼                        │
│  ┌──────────────────────┐  ┌──────────────────────┐            │
│  │    Parser Service    │  │    Vector Store      │            │
│  │  ┌──────────────────┐│  │  ChromaDB (SQLite)   │            │
│  │  │ PyMuPDF (fitz)   ││  │  ├─ Document chunks  │            │
│  │  │ ├─ 提取TextLayer ││  │  ├─ Embeddings       │            │
│  │  │ ├─ 坐标保留      ││  │  └─ Metadata(坐标)   │            │
│  │  │ └─ 图片区域检测  ││  └──────────────────────┘            │
│  │  └──────────────────┘│                                      │
│  │  ┌──────────────────┐│  ┌──────────────────────┐            │
│  │  │ GLM-4V Service   ││  │   LLM Router         │            │
│  │  │ (仅处理图片页)   ││  │  ├─ DeepSeek Pool    │            │
│  │  └──────────────────┘│  │  └─ GLM-4 Fallback   │            │
│  └──────────────────────┘  └──────────────────────┘            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 二、核心技术创新点

### 2.1 智能PDF分流引擎（Smart Router）

不再盲目OCR所有页面，采用**"提取优先，OCR兜底"**策略：

```python
# backend/services/document_router.py
async def process_page(page: fitz.Page) -> PageContent:
    """
    决策逻辑：
    1. 先提取原生文本（保留坐标）
    2. 检查文本密度和完整性
    3. 若文本<100字符或包含大量乱码 -> 标记为Image-heavy -> OCR
    """
    text_dict = page.get_text("dict")
    chars = sum(len(span["text"]) for block in text_dict["blocks"] for span in block.get("lines", []))
    
    if chars > 200 and not has_garbled_text(text_dict):
        # 原生文本足够，直接结构化
        return PageContent(
            type="native",
            text=extract_structured_text(text_dict),
            coordinates=extract_coordinates(text_dict),
            confidence=1.0
        )
    else:
        # 扫描件或图片PDF，走OCR
        return PageContent(
            type="ocr",
            image=render_page_to_image(page),
            coordinates=None  # OCR后回填
        )
```

### 2.2 坐标映射系统（Coordinate Bridge）

实现RAG结果到PDF可视区域的双向映射：

```typescript
// 数据结构：保留PDF原生坐标（与缩放无关）
interface TextChunk {
  id: string;
  content: string;
  embedding: number[];
  bbox: {       // PDF坐标系 (0-1 或 points)
    page: number;
    x: number;  // 左下角x (PDF标准坐标系)
    y: number;  // 左下角y
    w: number;
    h: number;
  };
  source: "native" | "ocr";
}

// 前端渲染时坐标转换（PDF坐标 -> CSS像素）
function pdfToCss(bbox: PDFBBox, viewport: Viewport): CSSRect {
  const scale = viewport.width / viewport.pdfPageWidth;
  return {
    left: bbox.x * scale,
    top: (viewport.pdfPageHeight - bbox.y - bbox.h) * scale, // PDF坐标原点在左下，CSS在左上
    width: bbox.w * scale,
    height: bbox.h * scale
  };
}
```

### 2.3 渐进式加载策略（Progressive Loading）

解决大PDF（500页+）性能问题：

```typescript
// frontend/hooks/usePdfLoader.ts
interface LoadingStrategy {
  thumbnail: "immediate";     // 1. 立即加载128px缩略图（PyMuPDF生成）
  textLayer: "viewport";      // 2. 视口内加载TextLayer（前后各缓冲2页）
  ocr: "onDemand";            // 3. OCR仅当用户滚动到该页且停留>500ms
  vectorIndex: "background";  // 4. 后台线程构建向量索引
}
```

---

## 三、技术栈选型（最终版）

| 层级 | 技术选型 | 理由 |
|-----|---------|------|
| **前端框架** | React 18 + TypeScript | 生态成熟 |
| **PDF引擎** | `pdfjs-dist` (v4.0+) + `react-pdf` | 官方支持TextLayer，支持坐标提取 |
| **状态管理** | Zustand + Immer | 简化状态流，持久化配置 |
| **向量数据库** | ChromaDB (嵌入式) | 零配置本地SQLite存储，支持元数据过滤 |
| **Embeddings** | `BAAI/bge-m3` (本地) 或 智谱API | 中英文优化，支持稀疏向量 |
| **后端框架** | FastAPI + Python 3.11 | 异步支持，PyMuPDF生态 |
| **OCR模型** | GLM-4V-Flash (智谱) | 专精中文文档，成本极低（0.002元/次）|
| **LLM推理** | DeepSeek-V3 (平台托管) | 用户无需配置，后端Key池负载均衡 |

---

## 四、核心模块详细设计

### 模块1：PDF渲染引擎（Frontend）

**架构分层**：

```tsx
// components/PDFViewer/LayeredRenderer.tsx
export function LayeredPDFViewer({ docId }: { docId: string }) {
  const [scale, setScale] = useState(1.0);
  const [highlights, setHighlights] = useState<TextChunk[]>([]);
  
  return (
    <div className="pdf-container">
      <TransformWrapper> {/* 手势缩放支持 */}
        <TransformComponent>
          <VirtualizedList 
            itemCount={numPages}
            renderItem={({ index }) => (
              <PageLayer 
                pageNumber={index + 1}
                scale={scale}
                docId={docId}
                highlights={filterHighlightsByPage(highlights, index + 1)}
              />
            )}
          />
        </TransformComponent>
      </TransformWrapper>
    </div>
  );
}

// 单页三层渲染
function PageLayer({ pageNumber, scale, docId, highlights }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);
  
  useEffect(() => {
    // Layer 1: Canvas渲染（视觉）
    const renderCanvas = async () => {
      const page = await pdfDoc.getPage(pageNumber);
      const viewport = page.getViewport({ scale });
      const canvas = canvasRef.current;
      const ctx = canvas.getContext('2d');
      await page.render({ canvasContext: ctx, viewport }).promise;
    };
    
    // Layer 2: 文本层渲染（可选中）
    const renderTextLayer = async () => {
      const page = await pdfDoc.getPage(pageNumber);
      const textContent = await page.getTextContent();
      await pdfjs.renderTextLayer({
        textContent,
        container: textLayerRef.current,
        viewport: page.getViewport({ scale }),
        textDivs: [] // 可缓存复用
      });
    };
    
    renderCanvas();
    renderTextLayer();
  }, [pageNumber, scale]);
  
  return (
    <div className="page-wrapper" style={{ position: 'relative' }}>
      <canvas ref={canvasRef} className="canvas-layer" />
      
      {/* 透明文本层：支持原生选择、复制、搜索 */}
      <div 
        ref={textLayerRef} 
        className="text-layer" 
        style={{ 
          position: 'absolute', 
          top: 0, 
          left: 0,
          pointerEvents: 'auto',
          opacity: 0.2 // 调试时可见，生产环境为0
        }} 
      />
      
      {/* Layer 3: 引用高亮层（与RAG联动） */}
      <svg className="highlight-layer" style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none' }}>
        {highlights.map(chunk => (
          <rect
            key={chunk.id}
            x={chunk.bbox.x * scale}
            y={(pageHeight - chunk.bbox.y - chunk.bbox.h) * scale}
            width={chunk.bbox.w * scale}
            height={chunk.bbox.h * scale}
            fill="rgba(255, 255, 0, 0.3)"
            stroke="orange"
            strokeWidth={1}
          >
            <title>{chunk.content.substring(0, 50)}...</title>
          </rect>
        ))}
      </svg>
    </div>
  );
}
```

**关键优化点**：
1. **虚拟滚动**：使用 `react-window` 或 `react-virtuoso`，只渲染视口内3页内容
2. **缩放策略**：
   - 快速缩放：使用CSS `transform: scale()`（瞬时响应）
   - 清晰渲染：停止操作300ms后，用新scale重绘Canvas（清晰）
3. **文本层复用**：PDF.js的TextLayer只解析一次，缩放时通过CSS transform跟随变换

---

### 模块2：RAG引擎（Backend）

**向量检索流程**：

```python
# backend/services/rag_engine.py
class RAGEngine:
    def __init__(self):
        self.client = chromadb.PersistentClient(path="./chroma_db")
        self.collection = self.client.get_or_create_collection(
            name="documents",
            metadata={"hnsw:space": "cosine"}
        )
    
    async def index_document(self, doc_id: str, pages: List[PageContent]):
        """建立文档索引"""
        chunks = []
        
        for page in pages:
            if page.type == "native":
                # 原生文本：按段落切分，保留精确坐标
                for para in page.paragraphs:
                    chunks.append({
                        "id": f"{doc_id}_p{page.number}_b{para.index}",
                        "text": para.text,
                        "metadata": {
                            "page": page.number,
                            "bbox": para.bbox,
                            "source": "native"
                        }
                    })
            else:
                # OCR结果：先按OCR坐标切分，再入库
                ocr_blocks = await ocr_service.parse(page.image)
                for block in ocr_blocks:
                    chunks.append({
                        "id": f"{doc_id}_p{page.number}_ocr{block.index}",
                        "text": block.text,
                        "metadata": {
                            "page": page.number,
                            "bbox": block.bbox,  # OCR返回的坐标
                            "source": "ocr"
                        }
                    })
        
        # 生成向量（批量）
        embeddings = await embedding_model.encode([c["text"] for c in chunks])
        
        self.collection.add(
            ids=[c["id"] for c in chunks],
            embeddings=embeddings,
            documents=[c["text"] for c in chunks],
            metadatas=[c["metadata"] for c in chunks]
        )
    
    async def retrieve(self, query: str, doc_id: str, top_k: int = 5) -> List[TextChunk]:
        """混合检索：向量相似度 + 重排序"""
        # 1. 向量检索
        query_embed = await embedding_model.encode(query)
        results = self.collection.query(
            query_embeddings=[query_embed],
            where={"doc_id": doc_id},  # 仅搜索当前文档
            n_results=top_k * 2  # 多召回用于重排
        )
        
        # 2. 重排序（可选，使用轻量级模型）
        reranked = await reranker.rerank(query, results["documents"][0])
        
        return [
            TextChunk(
                id=results["ids"][0][i],
                content=results["documents"][0][i],
                bbox=results["metadatas"][0][i]["bbox"],
                page=results["metadatas"][0][i]["page"],
                distance=results["distances"][0][i]
            )
            for i in reranked[:top_k]
        ]
```

---

### 模块3：问答与引用链路

**实现精确引用跳转**：

```python
# backend/routers/chat.py
@router.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    # 1. 检索相关片段（含坐标）
    chunks = await rag_engine.retrieve(
        query=request.question,
        doc_id=request.docId
    )
    
    # 2. 构建带引用的Prompt
    context_blocks = []
    for i, chunk in enumerate(chunks):
        ref_id = f"[ref-{i+1}]"
        context_blocks.append(
            f"{ref_id} (第{chunk.page}页)\n{chunk.content}"
        )
    
    prompt = f"""
基于以下文档片段回答问题。请使用 [ref-N] 格式标注信息来源。

文档片段：
{'---'.join(context_blocks)}

问题：{request.question}

注意：回答中每句事实性陈述后必须跟随引用标记，如"根据配置 [ref-1]，系统支持..."
"""
    
    # 3. 调用DeepSeek生成（流式）
    response = await deepseek_client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        stream=True
    )
    
    # 4. 后端解析引用，前端高亮
    async for chunk in response:
        content = chunk.choices[0].delta.content
        # 简单正则提取 [ref-X] 用于前端高亮
        refs = extract_refs(content)
        yield json.dumps({
            "content": content,
            "active_refs": refs,  // 实时告诉前端当前在引用哪个
            "done": False
        })
```

**前端高亮联动**：

```typescript
// 当收到流式响应中的 active_refs
useEffect(() => {
  const activeChunks = chunks.filter(c => 
    message.active_refs.includes(c.refId)
  );
  
  // 高亮对应PDF区域
  setHighlights(activeChunks);
  
  // 可选：自动滚动到第一个引用位置
  if (activeChunks.length > 0) {
    pdfViewer.scrollToPage(activeChunks[0].page);
  }
}, [message.active_refs]);
```

---

## 五、API设计（REST + SSE）

### 文档上传与处理

```http
POST /api/documents/upload
Content-Type: multipart/form-data
X-API-Key: {zhipu-api-key}

file: <PDF文件>

# 响应
{
  "document_id": "doc_xxx",
  "status": "processing",
  "total_pages": 45,
  "ocr_required_pages": [3, 15, 16],  // 仅这3页需要OCR
  "progress_url": "/api/documents/doc_xxx/progress"
}

# 查询进度（SSE流式）
GET /api/documents/doc_xxx/progress
# 返回：
data: {"stage": "extracting", "current": 10, "total": 45}
data: {"stage": "embedding", "current": 40, "total": 45}
data: {"stage": "completed", "document_id": "doc_xxx"}
```

### 按需OCR（视口触发）

```http
POST /api/documents/{doc_id}/pages/{page_num}/ocr
Headers: X-API-Key: {zhipu-key}

# 响应（缓存）
{
  "page": 15,
  "chunks": [
    {
      "text": "表格内容...",
      "bbox": {"x": 100, "y": 200, "w": 300, "h": 50}
    }
  ]
}
```

### 对话接口（WebSocket推荐，兼容SSE）

```http
POST /api/chat
Content-Type: application/json

{
  "document_id": "doc_xxx",
  "question": "总结第三页的重点",
  "history": [...]
}

# SSE流式返回
data: {"type": "thinking", "content": "正在检索相关页面..."}
data: {"type": "content", "text": "根据", "refs": []}
data: {"type": "content", "text": "文档 [ref-1] ", "refs": ["ref-1"]}
data: {"type": "done", "final_refs": ["ref-1", "ref-2"]}
```

---

## 六、数据模型

```typescript
// 数据库 Schema (SQLite/JSON存储)
interface Document {
  id: string;
  name: string;
  total_pages: number;
  upload_time: string;
  processing_status: 'extracting' | 'embedding' | 'completed';
  thumbnail_urls: string[];  // 每页缩略图base64或路径
}

interface Chunk {
  id: string;
  document_id: string;
  page_number: number;
  content: string;
  embedding: number[];      // 1536维（bge-m3）
  bbox: BoundingBox;        // PDF坐标
  source_type: 'native' | 'ocr';
  ocr_confidence?: number;  // OCR专享
}

interface ChatMessage {
  id: string;
  document_id: string;
  role: 'user' | 'assistant';
  content: string;
  references: string[];     // 引用的chunk IDs
  timestamp: string;
}
```

---

## 七、部署与配置

### 单Key配置（用户侧极简）

```bash
# .env (用户只需填写一个Key)
ZHIPU_API_KEY=sk-xxxxxxxx  # 用于OCR+Embedding（可选）
# DeepSeek Key由平台托管，或使用用户自有Key：
# DEEPSEEK_API_KEY=sk-yyyyyyyy  # 可选，不填则走平台代理
```

### 目录结构

```
pdf-qa-system-v6/
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── PDFViewer/          # 三层渲染核心
│   │   │   │   ├── CanvasLayer.tsx
│   │   │   │   ├── TextLayer.tsx
│   │   │   │   └── HighlightLayer.tsx
│   │   │   ├── Chat/               # 对话组件
│   │   │   └── Settings/           # 单Key配置面板
│   │   ├── hooks/
│   │   │   ├── usePdfLoader.ts     # 渐进加载
│   │   │   └── useVectorSearch.ts  # 本地检索
│   │   └── stores/
│   │       └── documentStore.ts    # Zustand状态
├── backend/
│   ├── app/
│   │   ├── routers/
│   │   │   ├── documents.py        # 上传+进度
│   │   │   ├── ocr.py              # 按需OCR接口
│   │   │   └── chat.py             # RAG对话
│   │   ├── services/
│   │   │   ├── parser.py           # PyMuPDF解析
│   │   │   ├── ocr_gateway.py      # GLM-4V封装
│   │   │   ├── rag_engine.py       # ChromaDB操作
│   │   │   └── llm_router.py       # DeepSeek负载均衡
│   │   └── models/
│   │       └── schemas.py          # Pydantic模型
│   └── chroma_db/                  # 向量数据库目录
└── docker-compose.yml              # 一键部署（含Chroma）
```

### 性能优化配置

```yaml
# docker-compose.yml
services:
  backend:
    environment:
      - OCR_CONCURRENCY=3          # GLM-4V并发限制（防限流）
      - MAX_PDF_SIZE=50MB
      - CHUNK_SIZE=500             # 文本切分长度
      - CHUNK_OVERLAP=50           # 切分重叠（保持上下文）
    volumes:
      - ./chroma_db:/app/chroma_db # 持久化向量数据
      - ./uploads:/app/uploads     # PDF文件缓存
```

---

## 八、关键交互流程图

### 1. 上传与处理流程

```
用户上传PDF
    ↓
后端：PyMuPDF提取文本层 + 检测图片页
    ↓
├─ 文本页 ────┬─> 直接切分chunk ─┐
└─ 图片页标记 ─┘                 ↓
                              存入ChromaDB
                              生成缩略图
    ↓
返回doc_id，后台继续：图片页异步OCR → 更新向量库
```

### 2. 问答与高亮流程

```
用户提问
    ↓
RAG检索：从ChromaDB找出Top5文本块（含坐标）
    ↓
构建Prompt（带引用标记）-> DeepSeek生成答案
    ↓
流式返回：解析[ref-X]标签 -> 前端实时高亮PDF对应区域
    ↓
用户点击引用 -> 自动滚动到PDF对应页/位置
```

---

## 九、风险控制与降级策略

| 场景 | 策略 |
|-----|------|
| **OCR服务限流** | 智能队列：图片页OCR进入后台队列，先返回文本页结果让用户可检索 |
| **大PDF（>100MB）** | 分片上传 + 分卷处理，优先处理前20页（摘要通常在前几页） |
| **坐标漂移** | OCR结果使用`cross-matching`算法与PDF原生坐标对齐（基于页眉页脚定位） |
| **Key泄露** | 后端实现Rate Limiting（每分钟20次），超限提示用户 |
| **浏览器兼容性** | PDF.js自动降级：WebWorker不可用则转服务端渲染静态图 |

---

## 十、开发路线图

### Phase 1: 基础渲染（Week 1-2）
- [ ] 实现PDF.js三层渲染（Canvas+TextLayer+Highlight）
- [ ] 虚拟滚动支持（1000页流畅滚动）
- [ ] 缩放手势优化（双指/滚轮平滑缩放）

### Phase 2: 智能RAG（Week 3-4）
- [ ] PyMuPDF智能分流（Native vs OCR）
- [ ] ChromaDB集成（本地向量化）
- [ ] 坐标映射系统（Answer <-> PDF双向跳转）

### Phase 3: 交互优化（Week 5-6）
- [ ] 按需OCR（视口触发 + SSE进度）
- [ ] 引用高亮动画（CSS transition）
- [ ] 批注功能（用户在PDF上划线提问）

### Phase 4: 生产就绪（Week 7-8）
- [ ] DeepSeek Key池负载均衡
- [ ] 移动端适配（响应式PDF阅读器）
- [ ] 导出功能（问答记录导出为Markdown/PDF）
