/** 消息气泡组件 - 显示聊天消息和引用标签 */
import React from 'react';
import type { ChatMessage } from '../types';

interface MessageBubbleProps {
  message: ChatMessage;
  onReferenceClick?: (
    refId: string,
    page: number,
    bbox: { x0: number; y0: number; x1: number; y1: number },
    pageWidth?: number,
    pageHeight?: number,
  ) => void;
}

export function MessageBubble({ message, onReferenceClick }: MessageBubbleProps) {
  const isUser = message.role === 'user';

  // 解析答案中的引用标记 [ref:xxx] 转换为可点击的标签
  const parseContent = (content: string) => {
    const refRegex = /\[ref:([^\]]+)\]/g;
    const parts: React.ReactNode[] = [];
    let lastIndex = 0;
    let match;
    let refIndex = 0;

    while ((match = refRegex.exec(content)) !== null) {
      // 添加引用前的文本
      if (match.index > lastIndex) {
        parts.push(
          <span key={`text-${lastIndex}`}>
            {content.slice(lastIndex, match.index)}
          </span>
        );
      }

      // 添加可点击的引用标签
      const refId = match[1];
      refIndex++;

      if (message.references) {
        const ref = message.references.find((r) => r.id === refId);
        if (ref) {
          parts.push(
            <button
              key={`ref-${refIndex}`}
              className="inline-flex items-center px-2 py-0.5 mx-1 text-xs font-medium bg-blue-100 text-blue-800 rounded hover:bg-blue-200 transition-colors cursor-pointer"
              onClick={() => onReferenceClick?.(ref.id, ref.page, ref.bbox, ref.page_width, ref.page_height)}
            >
              [{refIndex}]
            </button>
          );
        } else {
          // 找不到对应引用时保留原始标记
          parts.push(
            <span key={`ref-miss-${refIndex}`}>
              {match[0]}
            </span>
          );
        }
      } else {
        parts.push(
          <span key={`ref-miss-${refIndex}`}>
            {match[0]}
          </span>
        );
      }

      lastIndex = match.index + match[0].length;
    }

    // 添加剩余文本
    if (lastIndex < content.length) {
      parts.push(
        <span key={`text-${lastIndex}`}>
          {content.slice(lastIndex)}
        </span>
      );
    }

    return parts;
  };

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`}>
      <div
        className={`max-w-[80%] rounded-lg px-4 py-2 ${
          isUser
            ? 'bg-blue-500 text-white'
            : 'bg-gray-100 text-gray-900'
        }`}
      >
        <div className="prose prose-sm max-w-none">
          {parseContent(message.content)}
        </div>
        {/* 显示引用列表 */}
        {!isUser && message.references && message.references.length > 0 && (
          <div className="mt-3 pt-3 border-t border-gray-200">
            <p className="text-xs text-gray-500 mb-2">引用来源：</p>
            {message.references.map((ref, idx) => (
              <button
                key={ref.id}
                className="block text-xs text-left text-gray-600 hover:text-blue-600 hover:underline truncate"
                onClick={() => onReferenceClick?.(ref.id, ref.page, ref.bbox, ref.page_width, ref.page_height)}
              >
                [{idx + 1}] 第{ref.page}页: {ref.text}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
