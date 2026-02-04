/** 问答状态管理Hook */
import { useState, useCallback } from 'react';
import { askQuestion } from '../services/api';
import type { ChatMessage, AskRequest } from '../types';

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sendMessage = useCallback(async (documentId: string, question: string) => {
    if (!question.trim()) return;

    // 添加用户消息
    const userMessage: ChatMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: question,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setIsLoading(true);
    setError(null);

    try {
      const request: AskRequest = {
        document_id: documentId,
        question,
      };

      const response = await askQuestion(request);

      // 添加助手消息
      const assistantMessage: ChatMessage = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: response.answer,
        references: response.references,
        timestamp: new Date(),
      };

      setMessages((prev) => [...prev, assistantMessage]);
    } catch (err) {
      // 增强错误处理，提供更友好的错误消息
      let errorMessage = '发送消息失败';

      if (err instanceof Error) {
        const errorObj = err as any;
        const response = errorObj.response;

        if (response) {
          // 服务器返回了错误响应
          if (response.status === 401) {
            errorMessage = 'API 密钥未配置或无效，请检查后端环境变量';
          } else if (response.status === 500) {
            const detail = response.data?.detail;
            if (detail && typeof detail === 'string') {
              if (detail.includes('API key') || detail.includes('api_key') || detail.includes('LLM_API_KEY')) {
                errorMessage = '大模型 API 密钥未配置或无效，请设置 LLM_API_KEY 环境变量';
              } else if (detail.includes('rate') || detail.includes('quota')) {
                errorMessage = 'API 配额已用完或请求频率过高，请稍后再试';
              } else {
                errorMessage = detail;
              }
            } else {
              errorMessage = '服务器内部错误，请检查后端日志';
            }
          } else if (response.status === 503) {
            errorMessage = '大模型服务暂时不可用，请稍后再试';
          } else {
            errorMessage = `请求失败 (${response.status}): ${response.data?.detail || err.message}`;
          }
        } else if (errorObj.code === 'ECONNREFUSED' || errorObj.code === 'ERR_NETWORK') {
          errorMessage = '无法连接到后端服务，请确保后端正在运行';
        } else {
          errorMessage = err.message;
        }
      }

      // 添加错误消息到聊天记录
      const errorNotification: ChatMessage = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: errorMessage,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, errorNotification]);

      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([]);
    setError(null);
  }, []);

  return {
    messages,
    isLoading,
    error,
    sendMessage,
    clearMessages,
  };
}
