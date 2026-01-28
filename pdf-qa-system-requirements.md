# PDF智能问答系统 - 项目需求文档

## 项目概述

开发一个类似NotebookLM的本地网页应用，用户上传PDF后可以基于文档内容进行问答，答案带有精确的段落级索引，点击可跳转并高亮PDF原文。

---

## 技术栈选择（新手友好）

```
前端: React + TypeScript + Tailwind CSS
PDF渲染: react-pdf (Mozilla PDF.js封装)
后端: Python + FastAPI
PDF解析: PyMuPDF (fitz) - 支持文字PDF和扫描版OCR
大模型: 兼容OpenAI格式的API（智谱/DeepSeek等）
向量数据库: ChromaDB (本地运行，无需安装)
```

---

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                      前端 (React)                        │
│  ┌─────────────────────┬─────────────────────────────┐  │
│  │                     │                             │  │
│  │   PDF查看器          │      问答对话区              │  │
│  │   - 原文显示         │      - 问题输入框            │  │
│  │   - 缩放控制         │      - 答案展示              │  │
│  │   - 页面导航         │      - 索引标签(可点击)       │  │
│  │   - 高亮显示         │      - 对话历史              │  │
│  │                     │                             │  │
│  └─────────────────────┴─────────────────────────────┘  │
│                可拖拽分隔线调节左右比例                    │
└─────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│                    后端 (FastAPI)                        │
│                                                         │
│  /api/upload    - 上传PDF，解析并建立索引                 │
│  /api/ask       - 接收问题，返回带索引的答案               │
│  /api/highlight - 获取指定段落的高亮坐标                  │
└─────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│                     数据处理层                           │
│                                                         │
│  PyMuPDF: PDF解析 + OCR(扫描版)                          │
│  ChromaDB: 文本向量化存储                                │
│  LLM API: 智谱/DeepSeek生成答案                          │
└─────────────────────────────────────────────────────────┘
```

---

## 目录结构

```
pdf-qa-system/
├── frontend/                   # 前端项目
│   ├── src/
│   │   ├── components/
│   │   │   ├── PDFViewer.tsx      # PDF查看器组件
│   │   │   ├── ChatPanel.tsx      # 问答面板组件
│   │   │   ├── MessageBubble.tsx  # 消息气泡(含索引标签)
│   │   │   └── ResizableSplit.tsx # 可调节分隔栏
│   │   ├── hooks/
│   │   │   ├── usePdfHighlight.ts # PDF高亮控制
│   │   │   └── useChat.ts         # 问答状态管理
│   │   ├── services/
│   │   │   └── api.ts             # 后端API调用
│   │   ├── types/
│   │   │   └── index.ts           # TypeScript类型定义
│   │   ├── App.tsx
│   │   └── main.tsx
│   ├── package.json
│   └── vite.config.ts
│
├── backend/                    # 后端项目
│   ├── app/
│   │   ├── main.py               # FastAPI入口
│   │   ├── routers/
│   │   │   ├── upload.py         # 上传接口
│   │   │   └── chat.py           # 问答接口
│   │   ├── services/
│   │   │   ├── pdf_parser.py     # PDF解析服务
│   │   │   ├── vector_store.py   # 向量存储服务
│   │   │   └── llm_service.py    # 大模型调用服务
│   │   ├── models/
│   │   │   └── schemas.py        # 数据模型
│   │   └── config.py             # 配置文件
│   ├── requirements.txt
│   └── .env.example              # 环境变量模板
│
├── data/                       # 运行时数据
│   ├── uploads/                  # 上传的PDF
│   └── chroma_db/                # 向量数据库
│
└── README.md                   # 项目说明
```

---

## 核心功能模块详细设计

### 模块1: PDF上传与解析

**功能描述:**
- 用户上传PDF文件
- 后端解析PDF，提取文字内容
- 对扫描版PDF自动进行OCR识别
- 按段落切分文本，记录每段的页码和位置坐标
- 将段落文本向量化存入ChromaDB

**数据结构:**
```python
# 段落信息
class Paragraph:
    id: str              # 唯一标识，如 "p1_para3" (第1页第3段)
    page_number: int     # 页码(从1开始)
    text: str            # 段落文本内容
    bbox: dict           # 位置坐标 {"x0": 0, "y0": 0, "x1": 100, "y1": 50}
    
# PDF文档信息
class Document:
    id: str              # 文档ID
    filename: str        # 文件名
    total_pages: int     # 总页数
    paragraphs: list     # 所有段落
```

**API设计:**
```
POST /api/upload
请求: multipart/form-data, 包含PDF文件
响应: {
    "document_id": "doc_xxx",
    "filename": "example.pdf",
    "total_pages": 50,
    "paragraph_count": 230,
    "message": "解析成功"
}
```

---

### 模块2: 智能问答

**功能描述:**
- 接收用户问题
- 从向量数据库检索相关段落(Top 5-10)
- 将问题+相关段落发送给大模型
- 大模型生成答案，必须标注引用的段落ID
- 返回答案和索引信息

**Prompt设计:**
```
你是一个专业的文档分析助手。请严格基于以下PDF文档内容回答用户问题。

【文档内容】
[段落ID: p1_para1] 这是第一段的内容...
[段落ID: p1_para2] 这是第二段的内容...
[段落ID: p2_para1] 这是第三段的内容...

【回答要求】
1. 只能使用上述文档内容回答，不要编造信息
2. 在回答中使用 [ref:段落ID] 格式标注信息来源
3. 如果文档中没有相关信息，请明确告知用户
4. 每个关键论点都要标注来源

【用户问题】
{user_question}
```

**API设计:**
```
POST /api/ask
请求: {
    "document_id": "doc_xxx",
    "question": "这份文档的主要内容是什么？"
}
响应: {
    "answer": "根据文档，主要内容包括...[ref:p1_para1]...另外还提到...[ref:p2_para3]",
    "references": [
        {
            "id": "p1_para1",
            "page": 1,
            "text": "原文片段...",
            "bbox": {"x0": 50, "y0": 100, "x1": 500, "y1": 150}
        },
        {
            "id": "p2_para3",
            "page": 2,
            "text": "原文片段...",
            "bbox": {"x0": 50, "y0": 200, "x1": 500, "y1": 280}
        }
    ]
}
```

---

### 模块3: PDF查看与高亮

**功能描述:**
- 左侧显示PDF原文，支持缩放和翻页
- 点击答案中的索引标签 [ref:xxx]
- PDF自动跳转到对应页面
- 对应段落用黄色高亮显示
- 高亮可以取消或切换

**前端交互流程:**
```
1. 用户点击答案中的 [1] 索引按钮
2. 前端获取该索引对应的 page 和 bbox
3. PDFViewer跳转到指定页面
4. 在bbox位置绘制黄色半透明矩形作为高亮
5. 滚动视图使高亮区域居中显示
```

**组件Props设计:**
```typescript
// PDF查看器
interface PDFViewerProps {
    fileUrl: string;                    // PDF文件URL
    currentPage: number;                // 当前页码
    onPageChange: (page: number) => void;
    highlights: Highlight[];            // 高亮列表
}

interface Highlight {
    id: string;
    page: number;
    bbox: { x0: number; y0: number; x1: number; y1: number };
    color?: string;  // 默认黄色
}

// 索引标签
interface ReferenceTagProps {
    index: number;              // 显示的序号 [1] [2]
    referenceId: string;        // 段落ID
    onClick: (refId: string) => void;
}
```

---

### 模块4: 可调节布局

**功能描述:**
- 左右两栏布局，中间有拖拽条
- 拖拽可调节左右宽度比例
- 记住用户的布局偏好(localStorage)
- 最小宽度限制，防止完全折叠

**实现方案:**
```typescript
// 使用 react-resizable-panels 库
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

<PanelGroup direction="horizontal">
    <Panel defaultSize={50} minSize={30}>
        <PDFViewer />
    </Panel>
    <PanelResizeHandle className="w-2 bg-gray-200 hover:bg-blue-400" />
    <Panel defaultSize={50} minSize={30}>
        <ChatPanel />
    </Panel>
</PanelGroup>
```

---

## 开发步骤（按顺序执行）

### 第一阶段: 后端基础 (建议先做)

```
Step 1: 初始化后端项目
- 创建Python虚拟环境
- 安装依赖: fastapi, uvicorn, pymupdf, chromadb, openai
- 创建基本目录结构
- 配置环境变量(.env)

Step 2: PDF解析服务
- 实现PDF文本提取
- 实现段落切分逻辑
- 记录段落位置坐标(bbox)
- 添加OCR支持(扫描版)

Step 3: 向量存储服务
- 初始化ChromaDB
- 实现段落向量化和存储
- 实现相似度检索

Step 4: 大模型服务
- 封装API调用(兼容OpenAI格式)
- 实现带索引的Prompt模板
- 解析返回结果中的索引

Step 5: API接口
- 实现 /api/upload
- 实现 /api/ask
- 添加CORS配置
```

### 第二阶段: 前端开发

```
Step 6: 初始化前端项目
- 使用Vite创建React+TS项目
- 安装依赖: react-pdf, react-resizable-panels, tailwindcss, axios
- 配置Tailwind

Step 7: PDF查看器
- 集成react-pdf显示PDF
- 实现翻页和缩放
- 实现高亮覆盖层

Step 8: 问答面板
- 实现消息列表
- 实现输入框和发送
- 解析答案中的[ref:xxx]为可点击标签

Step 9: 布局和交互
- 实现可拖拽分隔布局
- 连接索引点击→PDF跳转高亮
- 添加加载状态和错误处理

Step 10: 优化和测试
- 测试不同类型PDF
- 优化大文件处理
- 添加上传进度显示
```

---

## 环境配置

### 后端 .env 文件
```bash
# 大模型配置 (智谱为例)
LLM_API_KEY=your_api_key_here
LLM_API_BASE=https://open.bigmodel.cn/api/paas/v4
LLM_MODEL=glm-4

# DeepSeek配置 (二选一)
# LLM_API_KEY=your_deepseek_key
# LLM_API_BASE=https://api.deepseek.com/v1
# LLM_MODEL=deepseek-chat

# 服务配置
UPLOAD_DIR=./data/uploads
CHROMA_DIR=./data/chroma_db
MAX_FILE_SIZE=50  # MB
```

### 后端 requirements.txt
```
fastapi==0.109.0
uvicorn==0.27.0
python-multipart==0.0.6
pymupdf==1.23.8
chromadb==0.4.22
openai==1.12.0
python-dotenv==1.0.0
```

### 前端 package.json 关键依赖
```json
{
  "dependencies": {
    "react": "^18.2.0",
    "react-pdf": "^7.7.0",
    "react-resizable-panels": "^2.0.0",
    "axios": "^1.6.0"
  },
  "devDependencies": {
    "tailwindcss": "^3.4.0",
    "typescript": "^5.3.0",
    "vite": "^5.0.0"
  }
}
```

---

## 在Claude Code中的使用方式

将此文档保存后，在Claude Code中可以这样使用:

```bash
# 方式1: 直接引用文档开始开发
"请按照 pdf-qa-system-requirements.md 中的 Step 1 初始化后端项目"

# 方式2: 实现具体模块
"请实现需求文档中的 PDF解析服务 模块"

# 方式3: 询问细节
"需求文档中的向量存储服务应该如何与大模型服务配合？"
```

---

## 注意事项

1. **API密钥安全**: 不要将API密钥提交到代码仓库
2. **大文件处理**: 100页PDF解析可能需要30秒以上，要有进度提示
3. **OCR准确性**: 扫描版PDF的OCR可能有误差，影响问答质量
4. **索引精度**: 段落切分算法直接影响索引准确度，需要调优
5. **本地运行**: 确保Node.js(>=18)和Python(>=3.9)已安装

---

## 后续可扩展功能

- [ ] 支持多PDF同时上传对比
- [ ] 对话历史保存和加载
- [ ] PDF标注和笔记功能
- [ ] 导出问答报告
- [ ] 支持Word/PPT等其他格式
