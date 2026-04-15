import React, { useEffect, useRef, useState } from 'react';
import {
    getMultimodalDefaults,
    isMultimodalConfigured,
    resolveEffectiveMultimodalApiKey,
} from '../../constants/multimodal';
import { useDocumentStore } from '../../stores/documentStore';
import { useVectorSearch } from '../../hooks/useVectorSearch';
import { MessageItem } from './MessageItem';
import './ChatPanel.css';

const formatPageList = (pages: number[]): string => {
    const sorted = [...pages].sort((a, b) => a - b);
    return sorted.join('、');
};

export const ChatPanel: React.FC = () => {
    const {
        messages,
        isLoading,
        currentDocument,
        clearMessages,
        clearHighlights,
        config,
        selectedPages,
        setSelectedPages,
    } = useDocumentStore();
    const { askQuestion } = useVectorSearch();

    const [inputValue, setInputValue] = useState('');
    const [pageFrom, setPageFrom] = useState('');
    const [pageTo, setPageTo] = useState('');
    const [useVision, setUseVision] = useState(true);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);
    const multimodalReady = isMultimodalConfigured(config);
    const effectiveMultimodalApiKey = resolveEffectiveMultimodalApiKey(config);
    const currentMultimodalLabel = getMultimodalDefaults(config.multimodalProvider).label;

    // 自动滚动到底部
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages]);

    const handleSend = async () => {
        const question = inputValue.trim();
        if (!question || isLoading || !currentDocument) return;

        setInputValue('');

        const totalPages = currentDocument.totalPages;
        let allowedPages: number[] | undefined;
        if (selectedPages.length > 0) {
            allowedPages = [...selectedPages].sort((a, b) => a - b);
        } else {
            const from = parseInt(pageFrom, 10);
            const to = parseInt(pageTo, 10);
            if (!isNaN(from) || !isNaN(to)) {
                const start = isNaN(from) ? 1 : Math.max(1, from);
                const end = isNaN(to) ? totalPages : Math.min(totalPages, to);
                if (start <= end) {
                    allowedPages = Array.from({ length: end - start + 1 }, (_, i) => start + i);
                }
            }
        }

        try {
            await askQuestion(question, { allowedPages, useVision: useVision && multimodalReady });
        } catch (error) {
            console.error('问答错误:', error);
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    const quickQuestions = [
        '这份文档的主要内容是什么？',
        '总结第一页的要点',
        '文档中有哪些关键数据？',
    ];
    const selectedPagesText = selectedPages.length > 0 ? formatPageList(selectedPages) : '';
    const pageScopeText = selectedPages.length > 0
        ? `当前问答范围：已选页面 ${selectedPagesText}`
        : (pageFrom || pageTo)
            ? `当前问答范围：第 ${pageFrom || '1'} ~ ${pageTo || currentDocument?.totalPages || ''} 页`
            : '当前问答范围：全文，可在左侧网格视图勾选页面';

    return (
        <div className="chat-panel">
            <div className="chat-header">
                <h3>📄 智能问答</h3>
                {currentDocument && <span className="doc-name">{currentDocument.name}</span>}
            </div>

            <div className="chat-messages">
                {messages.length === 0 ? (
                    <div className="chat-empty">
                        <div className="empty-icon">💬</div>
                        <p>开始提问，AI 将根据文档内容回答</p>

                        {currentDocument && (
                            <div className="quick-questions">
                                <p className="quick-title">快捷提问：</p>
                                {quickQuestions.map((q, i) => (
                                    <button key={i} className="quick-btn" onClick={() => setInputValue(q)}>
                                        {q}
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                ) : (
                    <>
                        {messages.map((msg) => (
                            <MessageItem key={msg.id} message={msg} />
                        ))}
                        <div ref={messagesEndRef} />
                    </>
                )}
            </div>

            <div className="chat-input-container">
                <div className="chat-options">
                    <button
                        className="clear-context-btn"
                        onClick={() => {
                            clearMessages();
                            clearHighlights();
                        }}
                        disabled={!currentDocument || isLoading}
                        title="清空右侧聊天窗口（不会删除后端保存的历史）"
                    >
                        清空上下文
                    </button>
                    {currentDocument && selectedPages.length > 0 && (
                        <div className="selected-pages-selector" title="问答会优先按这些页面检索和回答">
                            <span className="selected-pages-label">已选页面：</span>
                            <span className="selected-pages-value">{selectedPagesText}</span>
                            <button
                                className="selected-pages-clear"
                                onClick={() => setSelectedPages([])}
                                disabled={isLoading}
                                title="清除页面选择"
                            >
                                清除
                            </button>
                        </div>
                    )}
                    {currentDocument && (
                        <div className="page-range-selector">
                            <span className="page-range-label">手动页码：</span>
                            <input
                                type="number"
                                className="page-range-input"
                                value={pageFrom}
                                onChange={(e) => setPageFrom(e.target.value)}
                                placeholder="起始"
                                min={1}
                                max={currentDocument.totalPages}
                                disabled={isLoading || selectedPages.length > 0}
                            />
                            <span className="page-range-sep">~</span>
                            <input
                                type="number"
                                className="page-range-input"
                                value={pageTo}
                                onChange={(e) => setPageTo(e.target.value)}
                                placeholder="结束"
                                min={1}
                                max={currentDocument.totalPages}
                                disabled={isLoading || selectedPages.length > 0}
                            />
                            {(pageFrom || pageTo) && (
                                <button
                                    className="page-range-clear"
                                    onClick={() => { setPageFrom(''); setPageTo(''); }}
                                    disabled={isLoading || selectedPages.length > 0}
                                    title="清除范围限制"
                                >×</button>
                            )}
                        </div>
                    )}
                    {currentDocument && (
                        <label
                            className={`vision-toggle ${!multimodalReady ? 'disabled' : ''}`}
                            title={multimodalReady
                                ? `使用 ${config.multimodalModel || '多模态模型'} 直接读取页面图像回答，适合表格、图片、扫描件`
                                : '请先在设置中配置多模态模型'}
                        >
                            <input
                                type="checkbox"
                                checked={useVision}
                                onChange={(e) => setUseVision(e.target.checked)}
                                disabled={isLoading || !multimodalReady}
                            />
                            <span>🖼 多模态回答</span>
                        </label>
                    )}
                </div>
                {currentDocument && !multimodalReady && (
                    <div className="vision-config-hint">
                        请先在设置中配置多模态模型。当前供应商为 {currentMultimodalLabel}，
                        {config.multimodalBaseUrl && config.multimodalModel
                            ? (effectiveMultimodalApiKey ? '参数已完整。' : '还缺少可用的 API Key。')
                            : '还缺少 Base URL 或模型名。'}
                    </div>
                )}
                {currentDocument && (
                    <div className="page-scope-summary">{pageScopeText}</div>
                )}

                <div className="chat-input-row">
                    <textarea
                        ref={inputRef}
                        className="chat-input"
                        value={inputValue}
                        onChange={(e) => setInputValue(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder={currentDocument ? '输入问题，按 Enter 发送...' : '请先上传 PDF 文档'}
                        disabled={!currentDocument || isLoading}
                        rows={2}
                    />
                    <button
                        className="send-btn"
                        onClick={handleSend}
                        disabled={!inputValue.trim() || isLoading || !currentDocument}
                    >
                        {isLoading ? <span className="loading-dots">...</span> : <span>发送</span>}
                    </button>
                </div>
            </div>
        </div>
    );
};

