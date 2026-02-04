/** 后端API调用 */
import axios from 'axios';
import type { UploadResponse, AskRequest, AskResponse, LLMConfigRequest, LLMConfigResponse } from '../types';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

/** 上传PDF文件 */
export async function uploadPDF(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await api.post<UploadResponse>('/api/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });

  return response.data;
}

/** 提问 */
export async function askQuestion(request: AskRequest): Promise<AskResponse> {
  const response = await api.post<AskResponse>('/api/ask', request);
  return response.data;
}

/** 检索相关段落 */
export async function searchParagraphs(request: AskRequest) {
  const response = await api.post('/api/search', request);
  return response.data;
}

/** 健康检查 */
export async function healthCheck() {
  const response = await api.get('/health');
  return response.data;
}

/** LLM 状态检查 */
export async function checkLLMStatus() {
  const response = await api.get('/api/llm-status');
  return response.data;
}

/** 获取 LLM 配置（不含密钥明文） */
export async function getLLMConfig(): Promise<LLMConfigResponse> {
  const response = await api.get<LLMConfigResponse>('/api/llm-config');
  return response.data;
}

/** 更新 LLM 配置 */
export async function updateLLMConfig(payload: LLMConfigRequest): Promise<LLMConfigResponse> {
  const response = await api.post<LLMConfigResponse>('/api/llm-config', payload);
  return response.data;
}

export default api;
