import React, { useEffect, useRef, useState } from 'react';
import { useDocumentStore } from '../../stores/documentStore';
import { useVectorSearch } from '../../hooks/useVectorSearch';
import { MessageItem } from './MessageItem';
import './ChatPanel.css';

export const ChatPanel: React.FC = () => {
    const { messages, isLoading, currentDocument } = useDocumentStore();
    const { askQuestion } = useVectorSearch();

    const [inputValue, setInputValue] = useState('');
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);

    const canAsk = Boolean(currentDocument && (currentDocument.recognizedPages || []).length > 0);

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages]);

    const handleSend = async () => {
        const question = inputValue.trim();
        if (!question || isLoading || !currentDocument || !canAsk) {
            return;
        }

        setInputValue('');

        try {
            await askQuestion(question);
        } catch (error) {
            console.error('Ask question error:', error);
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
        '总结当前已识别页面的关键点',
        '提取已识别页面中的关键数据',
    ];

    return (
        <div className="chat-panel">
            <div className="chat-header">
                <h3>智能问答</h3>
                {currentDocument && <span className="doc-name">{currentDocument.name}</span>}
            </div>

            <div className="chat-messages">
                {messages.length === 0 ? (
                    <div className="chat-empty">
                        <div className="empty-icon">💬</div>
                        {currentDocument ? (
                            canAsk ? (
                                <p>开始提问，系统将基于已识别页面回答</p>
                            ) : (
                                <p>请先在左侧缩小到网格模式，选择页面并执行识别。</p>
                            )
                        ) : (
                            <p>请先上传 PDF 文档</p>
                        )}

                        {currentDocument && canAsk && (
                            <div className="quick-questions">
                                <p className="quick-title">快捷提问：</p>
                                {quickQuestions.map((q, i) => (
                                    <button
                                        key={i}
                                        className="quick-btn"
                                        onClick={() => setInputValue(q)}
                                    >
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
                <textarea
                    ref={inputRef}
                    className="chat-input"
                    value={inputValue}
                    onChange={(e) => setInputValue(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder={
                        !currentDocument
                            ? '请先上传 PDF 文档'
                            : canAsk
                                ? '输入问题，按 Enter 发送...'
                                : '请先识别页面后再提问'
                    }
                    disabled={!currentDocument || isLoading || !canAsk}
                    rows={2}
                />
                <button
                    className="send-btn"
                    onClick={handleSend}
                    disabled={!inputValue.trim() || isLoading || !currentDocument || !canAsk}
                >
                    {isLoading ? <span className="loading-dots">...</span> : <span>发送</span>}
                </button>
            </div>
        </div>
    );
};
