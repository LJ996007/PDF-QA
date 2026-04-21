import React, { useEffect, useRef, useState } from 'react';
import {
    getMultimodalDefaults,
    isMultimodalConfigured,
    resolveEffectiveMultimodalApiKey,
} from '../../constants/multimodal';
import type { ChatPageReferenceGroup } from '../../stores/documentStore';
import { useDocumentStore } from '../../stores/documentStore';
import { useVectorSearch } from '../../hooks/useVectorSearch';
import {
    formatPageSelectionLabel,
    normalizePageNumbers,
    parsePageSelectionInput,
} from '../../utils/pageSelection';
import { MessageItem } from './MessageItem';
import './ChatPanel.css';

const PAGE_GROUP_LABEL_PREFIX = '页面组';

const getAliasFromIndex = (index: number): string => {
    let value = index;
    let alias = '';

    do {
        alias = String.fromCharCode(65 + (value % 26)) + alias;
        value = Math.floor(value / 26) - 1;
    } while (value >= 0);

    return alias;
};

const getNextPageGroupAlias = (existingAliases: string[]): string => {
    const aliasSet = new Set(existingAliases.map((alias) => alias.trim().toUpperCase()).filter(Boolean));

    for (let index = 0; index < 512; index += 1) {
        const alias = getAliasFromIndex(index);
        if (!aliasSet.has(alias)) {
            return alias;
        }
    }

    return `Z${Date.now()}`;
};

const escapeRegExp = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

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
    const [manualGroupPagesText, setManualGroupPagesText] = useState('');
    const [pageReferenceGroups, setPageReferenceGroups] = useState<ChatPageReferenceGroup[]>([]);
    const [isPageReferencePanelCollapsed, setIsPageReferencePanelCollapsed] = useState(true);
    const [useVision, setUseVision] = useState(true);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);
    const pageReferenceGroupIdRef = useRef(0);
    const multimodalReady = isMultimodalConfigured(config);
    const effectiveMultimodalApiKey = resolveEffectiveMultimodalApiKey(config);
    const currentMultimodalLabel = getMultimodalDefaults(config.multimodalProvider).label;

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages]);

    const insertTextAtCursor = (text: string) => {
        const textarea = inputRef.current;
        if (!textarea) {
            setInputValue((prev) => prev + text);
            return;
        }

        const start = textarea.selectionStart ?? inputValue.length;
        const end = textarea.selectionEnd ?? inputValue.length;
        const nextValue = `${inputValue.slice(0, start)}${text}${inputValue.slice(end)}`;
        setInputValue(nextValue);

        window.requestAnimationFrame(() => {
            textarea.focus();
            const caretPosition = start + text.length;
            textarea.setSelectionRange(caretPosition, caretPosition);
        });
    };

    const createPageReferenceGroup = (pages: number[]) => {
        const normalizedPages = normalizePageNumbers(pages);
        if (normalizedPages.length === 0) {
            window.alert('请选择有效页码后再创建页面组。');
            return;
        }

        const alias = getNextPageGroupAlias(pageReferenceGroups.map((group) => group.alias));
        const label = `${PAGE_GROUP_LABEL_PREFIX}${alias}`;
        const placeholder = `【${label}】`;
        pageReferenceGroupIdRef.current += 1;
        const nextGroup: ChatPageReferenceGroup = {
            id: `page_group_${pageReferenceGroupIdRef.current}_${alias}`,
            alias,
            label,
            placeholder,
            pages: normalizedPages,
        };

        setPageReferenceGroups((prev) => [...prev, nextGroup]);
        setIsPageReferencePanelCollapsed(false);
        insertTextAtCursor(placeholder);
    };

    const handleCreateGroupFromSelectedPages = () => {
        if (selectedPages.length === 0) {
            window.alert('请先在左侧网格视图选择页面。');
            return;
        }

        createPageReferenceGroup(selectedPages);
    };

    const handleCreateGroupFromManualPages = () => {
        if (!currentDocument) return;

        const manualPages = parsePageSelectionInput(manualGroupPagesText, currentDocument.totalPages);
        if (manualPages.length === 0) {
            window.alert('请输入有效页码，例如 1,3-5,8。');
            return;
        }

        createPageReferenceGroup(manualPages);
        setManualGroupPagesText('');
    };

    const handleInsertPageReferenceGroup = (group: ChatPageReferenceGroup) => {
        insertTextAtCursor(group.placeholder);
    };

    const handleDeletePageReferenceGroup = (groupId: string) => {
        const targetGroup = pageReferenceGroups.find((group) => group.id === groupId);
        if (!targetGroup) {
            return;
        }

        const placeholderPattern = new RegExp(escapeRegExp(targetGroup.placeholder), 'g');
        setPageReferenceGroups((prev) => prev.filter((group) => group.id !== groupId));
        setInputValue((prev) => prev.replace(placeholderPattern, ''));
    };

    const handleSend = async () => {
        const question = inputValue.trim();
        if (!question || isLoading || !currentDocument) return;

        setInputValue('');

        const hasPageReferenceGroups = pageReferenceGroups.length > 0;
        const totalPages = currentDocument.totalPages;
        let allowedPages: number[] | undefined;

        if (!hasPageReferenceGroups && selectedPages.length > 0) {
            allowedPages = [...selectedPages].sort((a, b) => a - b);
        } else if (!hasPageReferenceGroups) {
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
            await askQuestion(question, {
                allowedPages,
                useVision: useVision && multimodalReady,
                pageReferenceGroups,
            });
            setPageReferenceGroups([]);
            setManualGroupPagesText('');
            setIsPageReferencePanelCollapsed(true);
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
    const manualGroupPagesPreview = currentDocument
        ? parsePageSelectionInput(manualGroupPagesText, currentDocument.totalPages)
        : [];
    return (
        <div className="chat-panel">
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
                    {currentDocument && (
                        <div className="page-reference-toolbar-group">
                            <button
                                type="button"
                                className={`page-reference-toggle-btn ${!isPageReferencePanelCollapsed ? 'is-open' : ''}`}
                                onClick={() => setIsPageReferencePanelCollapsed((prev) => !prev)}
                                aria-expanded={!isPageReferencePanelCollapsed}
                            >
                                <span className="page-reference-toggle-label">引用页面</span>
                                <span
                                    className={`page-reference-chevron ${!isPageReferencePanelCollapsed ? 'is-open' : ''}`}
                                    aria-hidden="true"
                                >
                                    ⌄
                                </span>
                            </button>
                            {(selectedPages.length > 0 || pageReferenceGroups.length > 0) && (
                                <div
                                    className="page-reference-inline-summary"
                                    title={[
                                        selectedPages.length > 0
                                            ? `左侧已选 ${formatPageSelectionLabel(selectedPages)}`
                                            : '',
                                        ...pageReferenceGroups.map((group) => `${group.placeholder} ${formatPageSelectionLabel(group.pages)}`),
                                    ].filter(Boolean).join('；')}
                                >
                                    {selectedPages.length > 0 && (
                                        <span className="page-reference-inline-chip page-reference-inline-chip--selected">
                                            <span className="page-reference-inline-placeholder">已选</span>
                                            <span className="page-reference-inline-pages">
                                                {formatPageSelectionLabel(selectedPages)}
                                            </span>
                                        </span>
                                    )}
                                    {pageReferenceGroups.map((group) => (
                                        <span key={group.id} className="page-reference-inline-chip">
                                            <span className="page-reference-inline-placeholder">{group.placeholder}</span>
                                            <span className="page-reference-inline-pages">
                                                {formatPageSelectionLabel(group.pages)}
                                            </span>
                                        </span>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}
                    {currentDocument && (
                        <div className="page-range-selector">
                            <span className="page-range-label">页码</span>
                            <input
                                type="number"
                                className="page-range-input"
                                value={pageFrom}
                                onChange={(e) => setPageFrom(e.target.value)}
                                placeholder="起始"
                                min={1}
                                max={currentDocument.totalPages}
                                disabled={isLoading || pageReferenceGroups.length > 0}
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
                                disabled={isLoading || pageReferenceGroups.length > 0}
                            />
                            {(pageFrom || pageTo) && (
                                <button
                                    className="page-range-clear"
                                    onClick={() => { setPageFrom(''); setPageTo(''); }}
                                    disabled={isLoading || pageReferenceGroups.length > 0}
                                    title="清除范围限制"
                                >
                                    ×
                                </button>
                            )}
                        </div>
                    )}
                </div>
                {currentDocument && !isPageReferencePanelCollapsed && (
                    <div className="page-reference-panel">
                        <div className="page-reference-panel-intro">
                            把页码保存为页面组，再插入到问题里
                        </div>
                        <div className="page-reference-body">
                            <div className="page-reference-source-row">
                                <div className="page-reference-source-card">
                                    <span className="page-reference-source-label">当前已选页</span>
                                    <span className="page-reference-source-value">
                                        {selectedPages.length > 0 ? formatPageSelectionLabel(selectedPages) : '未选择'}
                                    </span>
                                </div>
                                <button
                                    type="button"
                                    className="page-reference-action-btn"
                                    onClick={handleCreateGroupFromSelectedPages}
                                    disabled={isLoading || selectedPages.length === 0}
                                >
                                    保存为页面组
                                </button>
                                <button
                                    type="button"
                                    className="page-reference-secondary-btn"
                                    onClick={() => setSelectedPages([])}
                                    disabled={isLoading || selectedPages.length === 0}
                                >
                                    清空已选页
                                </button>
                            </div>

                            <div className="page-reference-source-row">
                                <input
                                    type="text"
                                    className="page-reference-manual-input"
                                    value={manualGroupPagesText}
                                    onChange={(e) => setManualGroupPagesText(e.target.value)}
                                    placeholder="手工页码，例如 1,3-5,8"
                                    disabled={isLoading}
                                />
                                <button
                                    type="button"
                                    className="page-reference-action-btn"
                                    onClick={handleCreateGroupFromManualPages}
                                    disabled={isLoading || !manualGroupPagesText.trim()}
                                >
                                    从手工页码建组
                                </button>
                            </div>

                            {manualGroupPagesText.trim() && (
                                <div className="page-reference-manual-preview">
                                    {manualGroupPagesPreview.length > 0
                                        ? `将创建：${formatPageSelectionLabel(manualGroupPagesPreview)}`
                                        : '未识别到有效页码'}
                                </div>
                            )}

                            {pageReferenceGroups.length > 0 && (
                                <div className="page-reference-group-list">
                                    {pageReferenceGroups.map((group) => (
                                        <div key={group.id} className="page-reference-group-chip">
                                            <div className="page-reference-group-main">
                                                <span className="page-reference-group-placeholder">{group.placeholder}</span>
                                                <span className="page-reference-group-pages">
                                                    {formatPageSelectionLabel(group.pages)}
                                                </span>
                                            </div>
                                            <div className="page-reference-group-actions">
                                                <button
                                                    type="button"
                                                    className="page-reference-chip-btn"
                                                    onClick={() => handleInsertPageReferenceGroup(group)}
                                                    disabled={isLoading}
                                                >
                                                    再次插入
                                                </button>
                                                <button
                                                    type="button"
                                                    className="page-reference-chip-btn page-reference-chip-btn--danger"
                                                    onClick={() => handleDeletePageReferenceGroup(group.id)}
                                                    disabled={isLoading}
                                                >
                                                    删除
                                                </button>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>
                )}
                {currentDocument && !multimodalReady && (
                    <div className="vision-config-hint">
                        请先在设置中配置多模态模型。当前供应商为 {currentMultimodalLabel}，
                        {config.multimodalBaseUrl && config.multimodalModel
                            ? (effectiveMultimodalApiKey ? '参数已完整。' : '还缺少可用的 API Key。')
                            : '还缺少 Base URL 或模型名。'}
                    </div>
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
