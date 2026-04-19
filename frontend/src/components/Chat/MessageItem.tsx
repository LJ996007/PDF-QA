import React from 'react';
import type { ChatMessage } from '../../stores/documentStore';
import { useDocumentStore } from '../../stores/documentStore';
import { formatPageSelectionLabel } from '../../utils/pageSelection';
import { ChatMarkdownContent } from './ChatMarkdownContent';

interface MessageItemProps {
    message: ChatMessage;
}

interface MarkdownErrorBoundaryProps {
    content: string;
    fallback: React.ReactNode;
    children: React.ReactNode;
}

interface MarkdownErrorBoundaryState {
    hasError: boolean;
}

class MarkdownErrorBoundary extends React.Component<MarkdownErrorBoundaryProps, MarkdownErrorBoundaryState> {
    state: MarkdownErrorBoundaryState = {
        hasError: false,
    };

    static getDerivedStateFromError(): MarkdownErrorBoundaryState {
        return { hasError: true };
    }

    componentDidUpdate(prevProps: MarkdownErrorBoundaryProps): void {
        if (prevProps.content !== this.props.content && this.state.hasError) {
            this.setState({ hasError: false });
        }
    }

    componentDidCatch(error: Error): void {
        console.error('[Chat] Markdown render failed, fallback to plain text.', error);
    }

    render(): React.ReactNode {
        if (this.state.hasError) {
            return this.props.fallback;
        }
        return this.props.children;
    }
}

export const MessageItem: React.FC<MessageItemProps> = ({ message }) => {
    const setHighlights = useDocumentStore((state) => state.setHighlights);
    const setCurrentPage = useDocumentStore((state) => state.setCurrentPage);
    const [isRefsExpanded, setIsRefsExpanded] = React.useState(false);
    const validRefIds = React.useMemo(
        () => new Set(message.references.map((ref) => ref.refId)),
        [message.references]
    );

    const jumpToReference = React.useCallback((refId: string): boolean => {
        const cleanRefId = refId.replace(/\[|\]/g, '');
        const ref = message.references.find((r) => r.refId === cleanRefId);
        if (!ref) {
            return false;
        }

        setHighlights([ref]);
        setCurrentPage(ref.page);
        return true;
    }, [message.references, setCurrentPage, setHighlights]);

    const handleMissingRef = React.useCallback(() => {
        window.alert('未找到对应引用，请先展开“引用来源”核对后再定位。');
    }, []);

    const handleRefClick = React.useCallback((refId: string) => {
        if (!jumpToReference(refId)) {
            handleMissingRef();
        }
    }, [handleMissingRef, jumpToReference]);

    const handleMarkdownRefClick = React.useCallback((refId: string, isValid: boolean) => {
        if (!isValid || !jumpToReference(refId)) {
            handleMissingRef();
        }
    }, [handleMissingRef, jumpToReference]);

    const renderUserContentWithPageGroups = React.useCallback((content: string) => {
        const groups = message.pageReferenceGroups || [];
        if (groups.length === 0) {
            return content;
        }

        const placeholderMap = new Map(groups.map((group) => [group.placeholder, group]));
        const pattern = new RegExp(`(${groups.map((group) => group.placeholder.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'g');

        return content.split(pattern).map((part, index) => {
            const group = placeholderMap.get(part);
            if (!group) {
                return <span key={index}>{part}</span>;
            }

            return (
                <span key={group.id + index} className="message-page-group-token" title={formatPageSelectionLabel(group.pages)}>
                    {group.placeholder}
                </span>
            );
        });
    }, [message.pageReferenceGroups]);

    // 渲染带引用标记的内容
    const renderPlainContentWithRefs = (content: string) => {
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

    const plainFallback = (
        <div className="message-content plain-content">
            {renderPlainContentWithRefs(message.content)}
        </div>
    );

    return (
        <div className={`message-item ${message.role}`}>
            <div className={`message-bubble ${message.isStreaming ? 'streaming' : ''}`}>
                {/* 消息内容 */}
                {message.role === 'assistant' ? (
                    message.isStreaming ? (
                        <div className="message-content plain-content streaming-content">
                            {message.content}
                        </div>
                    ) : (
                        <MarkdownErrorBoundary content={message.content} fallback={plainFallback}>
                            <div className="message-content markdown-content">
                                <ChatMarkdownContent
                                    content={message.content}
                                    validRefIds={validRefIds}
                                    onRefClick={handleMarkdownRefClick}
                                />
                            </div>
                        </MarkdownErrorBoundary>
                    )
                ) : (
                    <div className="message-content plain-content">
                        {renderUserContentWithPageGroups(message.content)}
                    </div>
                )}

                {message.role === 'user' && (message.pageReferenceGroups || []).length > 0 && (
                    <div className="message-page-group-list">
                        {(message.pageReferenceGroups || []).map((group) => (
                            <span
                                key={group.id}
                                className="message-page-group-pill"
                                title={group.pages.join(',')}
                            >
                                {group.placeholder} = {formatPageSelectionLabel(group.pages)}
                            </span>
                        ))}
                    </div>
                )}

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
