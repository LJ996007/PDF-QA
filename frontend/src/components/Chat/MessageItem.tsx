import React from 'react';
import type { ChatMessage } from '../../stores/documentStore';
import { useDocumentStore } from '../../stores/documentStore';

interface MessageItemProps {
    message: ChatMessage;
}

export const MessageItem: React.FC<MessageItemProps> = ({ message }) => {
    const { setHighlights, setCurrentPage } = useDocumentStore();
    const [isRefsExpanded, setIsRefsExpanded] = React.useState(false);

    // 处理引用点击
    const handleRefClick = (refId: string) => {
        // 移除方括号获取纯refId，如 [ref-1] -> ref-1
        const cleanRefId = refId.replace(/[\[\]]/g, '');
        const ref = message.references.find((r) => r.refId === cleanRefId);
        if (ref) {
            setHighlights([ref]);
            setCurrentPage(ref.page);
        }
    };

    // 渲染带引用标记的内容
    const renderContent = (content: string) => {
        // 匹配 [ref-N] 格式
        const parts = content.split(/(\[ref-\d+\])/g);

        return parts.map((part, index) => {
            const refMatch = part.match(/\[ref-(\d+)\]/);
            if (refMatch) {
                return (
                    <span
                        key={index}
                        className="ref-tag inline-ref"
                        onClick={() => handleRefClick(part)}
                        title="点击跳转到引用位置"
                    >
                        {refMatch[1]}
                    </span>
                );
            }
            return <span key={index}>{part}</span>;
        });
    };

    return (
        <div className={`message-item ${message.role}`}>
            <div className={`message-bubble ${message.isStreaming ? 'streaming' : ''}`}>
                {/* 消息内容 */}
                <div className="message-content">
                    {renderContent(message.content)}
                </div>

                {/* 引用列表 */}
                {message.role === 'assistant' && message.references.length > 0 && !message.isStreaming && (
                    <div className="references-list">
                        <div
                            className="references-title"
                            onClick={() => setIsRefsExpanded(!isRefsExpanded)}
                            style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px' }}
                        >
                            <span>{isRefsExpanded ? '▼' : '▶'}</span>
                            <span>📚 引用来源 ({message.references.length})</span>
                        </div>

                        {isRefsExpanded && message.references.map((ref) => (
                            <div
                                key={ref.id}
                                className="reference-item"
                                onClick={() => handleRefClick(ref.refId)}
                            >
                                <span className="ref-badge">{ref.refId.replace('ref-', '')}</span>
                                <div>
                                    <div className="ref-content">{ref.content}</div>
                                    <div className="ref-page">第 {ref.page} 页</div>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
};
