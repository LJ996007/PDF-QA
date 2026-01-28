/** TypeScript类型定义 */

/** 段落信息 */
export interface Paragraph {
  id: string;
  page_number: number;
  text: string;
  bbox: BBox;
}

/** 位置坐标 */
export interface BBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/** 文档信息 */
export interface Document {
  id: string;
  filename: string;
  total_pages: number;
  paragraph_count: number;
}

/** 上传响应 */
export interface UploadResponse {
  document_id: string;
  filename: string;
  total_pages: number;
  paragraph_count: number;
  message: string;
}

/** 问答请求 */
export interface AskRequest {
  document_id: string;
  question: string;
}

/** 引用信息 */
export interface Reference {
  id: string;
  page: number;
  text: string;
  bbox: BBox;
}

/** 问答响应 */
export interface AskResponse {
  answer: string;
  references: Reference[];
}

/** 聊天消息 */
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  references?: Reference[];
  timestamp: Date;
}

/** 高亮信息 */
export interface Highlight {
  id: string;
  page: number;
  bbox: BBox;
  color?: string;
}
