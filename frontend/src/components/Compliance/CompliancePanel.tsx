import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useDocumentStore } from '../../stores/documentStore';
import type { BoundingBox, TextChunk } from '../../stores/documentStore';
import './CompliancePanel.css';

interface CompliancePanelProps {
    className?: string;
}

type CompatTextChunk = TextChunk & {
    page_number?: number;
    ref_id?: string;
};

const createFallbackBbox = (page: number): BoundingBox => ({
    page,
    x: 0,
    y: 0,
    w: 100,
    h: 20,
});

const getRefPage = (ref: CompatTextChunk): number | undefined => {
    return ref.page_number ?? ref.bbox?.page ?? ref.page;
};

const getRefId = (ref: CompatTextChunk): string => {
    return ref.refId || ref.ref_id || '';
};

const toHighlightRef = (ref: CompatTextChunk, page: number): TextChunk => {
    return {
        ...ref,
        refId: getRefId(ref),
        page,
        bbox: ref.bbox ?? createFallbackBbox(page),
        source: ref.source ?? 'native',
    };
};

const parseAllowedPages = (raw: string): number[] => {
    const tokens = raw.split(/[,\s]+/).map((item) => item.trim()).filter(Boolean);
    const result = new Set<number>();
    for (const token of tokens) {
        const rangeMatch = token.match(/^(\d+)-(\d+)$/);
        if (rangeMatch) {
            const start = Number(rangeMatch[1]);
            const end = Number(rangeMatch[2]);
            if (Number.isInteger(start) && Number.isInteger(end) && start > 0 && end >= start) {
                for (let page = start; page <= end; page += 1) {
                    result.add(page);
                }
            }
            continue;
        }
        const page = Number(token);
        if (Number.isInteger(page) && page > 0) {
            result.add(page);
        }
    }
    return Array.from(result).sort((a, b) => a - b);
};

const formatSelectedPages = (pages: number[]): string => {
    if (pages.length === 0) return '未选择';
    if (pages.length <= 12) return pages.join(',');
    return `${pages.slice(0, 12).join(',')} ... 共 ${pages.length} 页`;
};

export const CompliancePanel: React.FC<CompliancePanelProps> = ({ className }) => {
    const {
        currentDocument,
        config,
        selectedPages,
        setSelectedPages,
        setHighlights,
        setCurrentPage,
        // 新增：合规性状态和操作
        complianceResults,
        complianceMarkdown,
        complianceRequirements,
        complianceAllowedPagesText,
        setComplianceResults,
        setComplianceRequirements,
        setComplianceAllowedPagesText
    } = useDocumentStore();

    const currentDocumentId = currentDocument?.id;
    const apiBaseUrl = config.apiBaseUrl || 'http://localhost:8000';
    const selectedPagesSummary = formatSelectedPages(selectedPages);
    const selectedPagesTitle = selectedPages.length > 0 ? selectedPages.join(',') : '未选择';

    // 移除本地 state，改用 store state
    const [loading, setLoading] = useState(false);
    // 有历史结果时默认折叠，否则展开
    const [inputCollapsed, setInputCollapsed] = useState(!!complianceMarkdown);

    const handleCheck = async () => {
        if (!currentDocumentId || !complianceRequirements.trim()) return;

        setLoading(true);
        try {
            const reqList = complianceRequirements.split('\n').filter(r => r.trim());
            const manualPages = parseAllowedPages(complianceAllowedPagesText);
            const selectedGridPages = [...new Set(selectedPages)].sort((a, b) => a - b);
            const mergedPages = Array.from(new Set([...selectedGridPages, ...manualPages])).sort((a, b) => a - b);

            const response = await fetch(`${apiBaseUrl}/api/documents/${currentDocumentId}/compliance`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    requirements: reqList,
                    api_key: config.deepseekApiKey || config.zhipuApiKey,
                    ...(mergedPages.length > 0 ? { allowed_pages: mergedPages } : {}),
                }),
            });

            if (!response.ok) throw new Error('Check failed');

            const data = await response.json();
            // 后端现在返回 { results: [...], markdown: "..." }

            // 更新到 store
            setComplianceResults(data.results || data, data.markdown || '');
            setInputCollapsed(true);
        } catch (error) {
            console.error(error);
            alert('检查失败，请重试');
        } finally {
            setLoading(false);
        }
    };



    // 处理页码点击 (从 Markdown 中的 [[P5]] 格式)
    const handlePageClick = (pageNum: number) => {
        console.log('[CompliancePanel] handlePageClick called with pageNum:', pageNum);
        console.log('[CompliancePanel] results:', complianceResults);

        // 在 results 中查找对应页码的引用
        for (const item of complianceResults) {
            if (item.references && item.references.length > 0) {
                for (const rawRef of item.references) {
                    const ref: CompatTextChunk = rawRef;
                    console.log('[CompliancePanel] Checking ref:', ref);
                    // 后端返回的是序列化后的 TextChunk，字段名可能是 page_number 或通过 bbox.page
                    const refPage = getRefPage(ref);
                    console.log('[CompliancePanel] refPage:', refPage, 'target:', pageNum);
                    if (refPage === pageNum) {
                        // 构建高亮对象
                        const highlightRef = toHighlightRef(ref, refPage);
                        console.log('[CompliancePanel] Setting highlight:', highlightRef);
                        setHighlights([highlightRef]);
                        setCurrentPage(pageNum);
                        return;
                    }
                }
            }
        }

        // 如果没找到具体引用，至少跳转到该页
        console.log('[CompliancePanel] No matching ref found, just navigating to page:', pageNum);
        setCurrentPage(pageNum);
    };

    // 处理 [ref-N] 格式引用点击
    const handleRefClick = (refIndex: number) => {
        console.log('[CompliancePanel] handleRefClick called with refIndex:', refIndex);
        const targetRefId = `ref-${refIndex}`;

        // 在所有 results 中查找对应 refId 的引用
        for (const item of complianceResults) {
            if (item.references && item.references.length > 0) {
                for (const rawRef of item.references) {
                    const ref: CompatTextChunk = rawRef;
                    // 兼容 ref_id (后端返回) 和 refId (前端定义)
                    const currentRefId = getRefId(ref);

                    if (currentRefId === targetRefId) {
                        const refPage = getRefPage(ref);

                        if (refPage) {
                            console.log('[CompliancePanel] Found matching ref:', ref);
                            // 构建高亮对象
                            const highlightRef = toHighlightRef(ref, refPage);
                            setHighlights([highlightRef]);
                            setCurrentPage(refPage);
                            return;
                        }
                    }
                }
            }
        }
        console.log('[CompliancePanel] No ref found for index:', refIndex);
    };

    return (
        <div className={`compliance-panel ${className || ''}`}>
            <div className="input-section">
                {/* 折叠标题行 —— 始终可见 */}
                <div
                    className="input-section-header"
                    onClick={() => setInputCollapsed(v => !v)}
                >
                    <span className="input-section-title">技术要求输入</span>
                    <span className={`input-collapse-icon ${inputCollapsed ? 'collapsed' : ''}`}>▾</span>
                </div>

                {/* 可折叠内容区 */}
                <div className={`input-section-body ${inputCollapsed ? 'input-section-body--collapsed' : ''}`}>
                    <textarea
                        className="req-input"
                        placeholder="请输入技术要求，每行一条..."
                        value={complianceRequirements}
                        onChange={(e) => setComplianceRequirements(e.target.value)}
                        disabled={loading}
                    />
                    <div className="page-scope-panel">
                        <div className="page-scope-toolbar">
                            <div className="page-scope-inline-group page-scope-inline-group--selected">
                                <span className="page-scope-inline-label">已选页</span>
                                <span className="page-scope-selected-value" title={selectedPagesTitle}>
                                    {selectedPagesSummary}
                                </span>
                            </div>
                            <div className="page-scope-inline-group page-scope-inline-group--manual">
                                <label className="page-scope-inline-label" htmlFor="compliance-page-range">
                                    人工页码
                                </label>
                                <input
                                    id="compliance-page-range"
                                    className="page-range-text-input"
                                    placeholder="例如: 1-5,8,10"
                                    value={complianceAllowedPagesText}
                                    onChange={(e) => setComplianceAllowedPagesText(e.target.value)}
                                    disabled={loading}
                                />
                            </div>
                            <button
                                type="button"
                                className="page-scope-clear-btn"
                                onClick={() => setSelectedPages([])}
                                disabled={loading || selectedPages.length === 0}
                            >
                                清空已选页
                            </button>
                        </div>
                        <div className="page-scope-hint">
                            已选页和人工页码会自动合并；都为空时默认分析全部已识别页。
                        </div>
                    </div>
                    <button
                        className="check-btn"
                        onClick={handleCheck}
                        disabled={loading || !currentDocumentId}
                    >
                        {loading ? '正在检查...' : '开始合规性检查'}
                    </button>
                </div>
            </div>

            <div className="results-section markdown-result">
                {complianceMarkdown ? (
                    <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                            // 自定义文本渲染，处理引用
                            p: ({ children }) => {
                                return <p>{processPageRefs(children, handlePageClick, handleRefClick)}</p>;
                            },
                            td: ({ children }) => {
                                return <td>{processPageRefs(children, handlePageClick, handleRefClick)}</td>;
                            }
                        }}
                    >
                        {complianceMarkdown}
                    </ReactMarkdown>
                ) : (
                    <div className="empty-state">
                        <p>输入技术要求后点击"开始合规性检查"</p>
                    </div>
                )}
            </div>
        </div>
    );
};

// 处理文本中的引用标记，转换为可点击的标签
// 支持两种格式: [[P数字]] 和 [ref-数字]
function processPageRefs(
    children: React.ReactNode,
    onPageClick: (page: number) => void,
    onRefClick?: (refIndex: number) => void
): React.ReactNode {
    if (typeof children === 'string') {
        // 匹配 [[P数字]] 或 [ref-数字] 格式
        const parts = children.split(/(\[\[P\d+\]\]|\[ref-\d+\])/g);
        return parts.map((part, index) => {
            // 匹配 [[P数字]] 格式
            const pageMatch = part.match(/\[\[P(\d+)\]\]/);
            if (pageMatch) {
                const pageNum = parseInt(pageMatch[1], 10);
                return (
                    <span
                        key={index}
                        className="ref-tag"
                        onClick={() => onPageClick(pageNum)}
                        title={`跳转到第 ${pageNum} 页`}
                    >
                        P{pageNum}
                    </span>
                );
            }

            // 匹配 [ref-数字] 格式
            const refMatch = part.match(/\[ref-(\d+)\]/);
            if (refMatch) {
                const refIndex = parseInt(refMatch[1], 10);
                return (
                    <span
                        key={index}
                        className="ref-tag ref-inline"
                        onClick={() => onRefClick?.(refIndex)}
                        title={`跳转到引用 ${refIndex}`}
                    >
                        {refIndex}
                    </span>
                );
            }

            return part;
        });
    }

    // 如果是数组，递归处理每个元素
    if (Array.isArray(children)) {
        return children.map((child, index) => (
            <React.Fragment key={index}>
                {processPageRefs(child, onPageClick, onRefClick)}
            </React.Fragment>
        ));
    }

    return children;
}

