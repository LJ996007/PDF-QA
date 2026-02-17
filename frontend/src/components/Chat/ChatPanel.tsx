import React, { useEffect, useRef, useState } from 'react';
import { useDocumentStore } from '../../stores/documentStore';
import { useVectorSearch } from '../../hooks/useVectorSearch';
import { MessageItem } from './MessageItem';
import './ChatPanel.css';

export const ChatPanel: React.FC = () => {
    const { messages, isLoading, currentDocument, clearMessages, clearHighlights } = useDocumentStore();
    const { askQuestion } = useVectorSearch();

    const [inputValue, setInputValue] = useState('');
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);

    // 自动滚动到底部
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages]);

    const handleSend = async () => {
        const question = inputValue.trim();
        if (!question || isLoading || !currentDocument) return;

        setInputValue('');

        try {
            await askQuestion(question);
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
                </div>

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

