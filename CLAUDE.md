# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 编码与终端（Windows）

- 建议在 PowerShell 中先执行 `chcp 65001`，再查看/编辑含中文文件，降低乱码风险。
- 编辑器统一使用 UTF-8（无 BOM），避免在不同工具间来回切换时发生转码污染。
- 提交前运行 `cd frontend && npm run lint:mojibake`，发现乱码会直接失败（阻断模式）。
- 如需只看告警不阻断，可运行 `cd frontend && npm run check:mojibake:warn`。
- 启用提交钩子：`git config core.hooksPath .githooks`（仓库内 `pre-commit` 会自动执行乱码检查）。

## 项目概述

**PDF 智能问答系统 V6.0** - 基于"渐进式视觉 RAG"的本地化文档助手，支持精确到字符坐标的双向引用跳转。

核心特性：
- PDF 三层渲染体系（Canvas 视觉层 + TextLayer 文本层 + Annotation 交互层）
- ChromaDB 本地向量库 + 智能 OCR 路由（原生文本优先，视觉 OCR 兜底）
- 混合检索（向量相似度 + BM25 关键词）+ 坐标映射系统
- 百度 OCR（在线）+ RapidOCR（本地离线备用）

## 开发环境设置

### 后端启动（FastAPI）

```bash
# 安装依赖
cd backend
pip install -r requirements.txt

# 配置环境变量（复制 .env.example）
cp .env.example .env
# 编辑 .env 填写必要的 API Key

# 启动开发服务器
cd ..
python backend/main.py
# 或使用 uvicorn（从项目根目录）
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

后端默认运行在 `http://localhost:8000`

### 前端启动（React + Vite）

```bash
cd frontend
npm install
npm run dev
```

前端默认运行在 `http://localhost:5173`

### Docker 部署

```bash
# 完整系统一键启动
docker-compose up --build

# 访问：
# - 前端: http://localhost:5173
# - 后端 API: http://localhost:8000
```

## 架构与核心模块

### 后端架构（FastAPI）

```
backend/
├── main.py                  # FastAPI 应用入口，CORS 配置，路由注册
├── app/
│   ├── routers/
│   │   ├── documents.py     # 文档上传、处理、进度查询、识别接口
│   │   ├── ocr.py          # 单页 OCR 接口（按需 OCR）
│   │   └── chat.py         # RAG 对话接口（SSE 流式）
│   ├── services/
│   │   ├── parser.py       # PyMuPDF 文档解析、智能分流（Native vs OCR）
│   │   ├── rag_engine.py   # ChromaDB 向量检索 + 混合搜索（Vector + BM25）
│   │   ├── llm_router.py   # LLM 调用路由（DeepSeek/智谱切换）
│   │   ├── baidu_ocr.py    # 百度 OCR 网关
│   │   ├── local_ocr.py    # RapidOCR 本地识别（离线备用）
│   │   └── compliance_service.py  # 合规性检查服务
│   └── models/
│       └── schemas.py      # Pydantic 数据模型
├── chroma_db/              # ChromaDB 持久化存储（自动创建）
├── uploads/                # PDF 文件存储（自动创建）
└── thumbnails/             # 缩略图缓存（自动创建）
```

**关键模块说明**：

1. **documents.py** - 文档生命周期管理
   - `/api/documents/upload` - 上传 PDF，支持 `ocr_mode=manual|full`
   - `/api/documents/{doc_id}/progress` - SSE 流式返回处理进度
   - `/api/documents/{doc_id}/recognize` - 批量识别指定页面
   - 全局状态：`documents` dict 存储文档元数据，`document_locks` 管理并发安全

2. **rag_engine.py** - 核心检索引擎
   - 使用智谱 `embedding-3` 模型（2048 维向量）
   - 混合检索策略：RRF 融合（Reciprocal Rank Fusion）
   - `_is_low_value_text()` - 过滤 OCR 噪音（单字符、纯符号等）
   - `_select_best_line_index()` - 行级 bbox 精准定位（提高高亮精度）
   - 支持 `ensure_page_coverage` 模式（合规性检查时保证每页都有结果）

3. **parser.py** - PDF 智能解析
   - PyMuPDF 提取原生文本 + 坐标
   - 检测文本密度决定是否需要 OCR
   - 渲染页面为 Base64 图片（用于前端缩略图 + OCR 输入）

### 前端架构（React + TypeScript）

```
frontend/src/
├── components/
│   ├── PDFViewer/
│   │   ├── PDFViewer.tsx       # 主渲染器（react-virtuoso 虚拟滚动）
│   │   ├── PageLayer.tsx       # 单页三层渲染（Canvas + TextLayer + Highlight）
│   │   ├── HighlightLayer.tsx  # SVG 高亮层（引用坐标映射）
│   │   └── PageGridItem.tsx    # 网格视图页面项
│   ├── Chat/
│   │   ├── ChatPanel.tsx           # 对话面板主容器
│   │   ├── MessageItem.tsx         # 单条消息渲染
│   │   └── ChatMarkdownContent.tsx # Markdown + 引用标签解析
│   ├── Compliance/
│   │   └── CompliancePanel.tsx     # 合规性检查面板
│   └── Settings/
│       └── Settings.tsx            # 配置面板（API Key、提示词等）
├── stores/
│   └── documentStore.ts    # Zustand 全局状态（文档、消息、配置）
├── hooks/
│   ├── usePdfLoader.ts     # PDF 加载逻辑（PDF.js 封装）
│   └── useVectorSearch.ts  # API 调用封装（上传、查询、OCR）
└── utils/
    └── remarkRefTag.tsx    # Remark 插件（解析 [ref-N] 标签）
```

**关键交互流程**：

1. **文档上传与处理**
   - 用户选择 PDF → `uploadDocument(file, ocrMode)`
   - 后端返回 `doc_id` → 前端通过 SSE 监听 `/progress`
   - 根据 `ocr_mode`：
     - `manual`：仅提取原生文本，前端手动选择页面识别
     - `full`：后台自动 OCR 所有页面

2. **按需 OCR 触发**
   - 用户在网格视图选择未识别页面 → 批量调用 `/recognize`
   - 后端异步处理，前端通过轮询 `page_ocr_status` 更新状态

3. **RAG 对话与高亮**
   - 用户提问 → `/api/chat` SSE 流式返回
   - 后端检索 chunks → LLM 生成答案（标注 `[ref-N]`）
   - 前端解析引用 → 实时高亮 PDF 对应坐标区域
   - 点击引用标签 → 自动滚动到对应页面

## 坐标系统说明

### PDF 坐标 → CSS 像素转换

PDF 使用左下角为原点，CSS 使用左上角：

```typescript
// PDFViewer 中的转换逻辑
const pdfToCss = (bbox: BoundingBox, pageHeight: number, scale: number) => {
  return {
    left: bbox.x * scale,
    top: (pageHeight - bbox.y - bbox.h) * scale,  // Y 轴翻转
    width: bbox.w * scale,
    height: bbox.h * scale
  };
};
```

### Bbox 数据结构

```typescript
interface BoundingBox {
  page: number;   // 页码（1-based）
  x: number;      // PDF 坐标系左下角 x
  y: number;      // PDF 坐标系左下角 y
  w: number;      // 宽度
  h: number;      // 高度
}
```

## API 接口规范

### 核心端点

1. **上传文档**
```http
POST /api/documents/upload
Content-Type: multipart/form-data

file: <PDF 文件>
zhipu_api_key: <可选>
ocr_mode: manual | full
baidu_ocr_url: <可选>
baidu_ocr_token: <可选>

Response:
{
  "document_id": "doc_xxx",
  "status": "processing",
  "ocr_mode": "manual"
}
```

2. **识别页面**
```http
POST /api/documents/{doc_id}/recognize
{
  "pages": [1, 3, 5],
  "api_key": "<可选>"
}
```

3. **RAG 对话**
```http
POST /api/chat
{
  "document_id": "doc_xxx",
  "question": "总结第三页的重点",
  "history": [],
  "zhipu_api_key": "<可选>",
  "deepseek_api_key": "<可选>",
  "allowed_pages": [1,2,3]  // 可选，限制检索范围
}

Response: SSE 流式
data: {"type": "references", "refs": [...]}
data: {"type": "content", "text": "...", "active_refs": ["ref-1"]}
data: {"type": "done", "final_refs": ["ref-1", "ref-2"]}
```

4. **合规性检查**
```http
POST /api/documents/{doc_id}/compliance
{
  "requirements": ["需要提供营业执照", "注册资本不少于500万"],
  "api_key": "<可选>",
  "allowed_pages": [1,2,3]  // 可选
}

Response:
{
  "results": [
    {
      "requirement": "需要提供营业执照",
      "status": "satisfied",
      "response": "文档中包含营业执照",
      "references": [...]
    }
  ]
}
```

## 环境变量配置

必填项（`.env` 文件）：

```bash
# 智谱 API Key（用于 Embedding + OCR fallback）
ZHIPU_API_KEY=sk-xxxxxxxx

# 可选：DeepSeek API Key（用于 LLM 推理，不填则使用智谱）
DEEPSEEK_API_KEY=sk-yyyyyyyy

# 可选：百度 OCR（在线识别，不填则使用本地 RapidOCR）
BAIDU_OCR_API_URL=https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic
BAIDU_OCR_TOKEN=24.xxxxx
```

## 测试与调试

### 后端测试

```bash
# 测试 OCR 功能
python backend/test_baidu_ocr.py

# 测试用户上传流程
python backend/test_user_upload.py

# 检查 ChromaDB 索引
python backend/check_chromadb.py
```

### 前端调试

```bash
# 开发模式（热重载）
cd frontend && npm run dev

# 类型检查
npm run lint

# 构建生产版本
npm run build
```

### 常见问题排查

1. **ChromaDB 维度不匹配错误**
   - 删除 `backend/chroma_db/` 目录重新索引
   - 确保使用智谱 `embedding-3` 模型（2048 维）

2. **OCR 识别失败**
   - 检查百度 OCR Token 是否过期
   - 系统会自动降级到本地 RapidOCR

3. **PDF 渲染空白**
   - 确认 PDF.js worker 路径正确（`pdfjs-dist/build/pdf.worker.min.mjs`）
   - 检查浏览器控制台 CORS 错误

4. **高亮坐标偏移**
   - OCR 坐标系与 PDF 原生坐标系不一致时会出现
   - 检查 `parser.py` 中 `render_page_to_image()` 的缩放比例

## 数据持久化

- **文档存储**：`backend/uploads/{doc_id}.pdf`
- **向量索引**：`backend/chroma_db/`（SQLite 存储）
- **前端配置**：LocalStorage（`pdf-qa-storage`）
- **运行时状态**：内存 dict（`documents`, `document_progress`）

注意：当前版本未实现数据库持久化，重启后端会丢失上传的文档状态。

## 代码约定

1. **后端**
   - 使用 `async/await` 异步编程
   - 日志输出：`logger.info()` + `print(flush=True)`（Windows GBK 兼容）
   - 错误处理：抛出 `HTTPException` 带详细 `detail` 信息

2. **前端**
   - 状态管理：Zustand + Immer（不可变更新）
   - 组件命名：PascalCase，文件名与组件名一致
   - 类型优先：所有 API 响应定义 TypeScript 接口

3. **坐标处理**
   - 后端统一使用 PDF 坐标系（左下角原点）
   - 前端渲染时转换为 CSS 坐标（左上角原点）
   - Bbox 字段始终为 `{x, y, w, h}`，page 单独存储

## Git 工作流

当前分支：`master`
主分支（PR 目标）：`main`

提交信息示例：
- `refactor pdf ocr flow and chat markdown rendering`
- `Fix table header spacing`
- `Fix OCR fallback and improve RAG`

推送前检查：
- 确保后端和前端都能正常启动
- 测试核心流程（上传 → OCR → 问答）
- 不要提交 `.env`、`chroma_db/`、`uploads/` 目录
