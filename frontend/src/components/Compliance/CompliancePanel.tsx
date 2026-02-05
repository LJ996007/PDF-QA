import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useDocumentStore, type ComplianceItem } from '../../stores/documentStore';
import './CompliancePanel.css';

interface CompliancePanelProps {
    className?: string;
}

export const CompliancePanel: React.FC<CompliancePanelProps> = ({ className }) => {
    const {
        currentDocument,
        config,
        setHighlights,
        setCurrentPage,
        // 新增：合规性状态和操作
        complianceResults,
        complianceMarkdown,
        complianceRequirements,
        setComplianceResults,
        setComplianceRequirements
    } = useDocumentStore();

    const currentDocumentId = currentDocument?.id;
    const apiBaseUrl = config.apiBaseUrl || 'http://localhost:8000';

    // 移除本地 state，改用 store state
    const [loading, setLoading] = useState(false);

    const handleCheck = async () => {
        if (!currentDocumentId || !complianceRequirements.trim()) return;

        setLoading(true);
        try {
            const reqList = complianceRequirements.split('\n').filter(r => r.trim());

            const response = await fetch(`${apiBaseUrl}/api/documents/${currentDocumentId}/compliance`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    requirements: reqList,
                    api_key: config.deepseekApiKey || config.zhipuApiKey
                }),
            });

            if (!response.ok) throw new Error('Check failed');

            const data = await response.json();
            // 后端现在返回 { results: [...], markdown: "..." }

            // 更新到 store
            setComplianceResults(data.results || data, data.markdown || '');
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
                for (const ref of item.references) {
                    console.log('[CompliancePanel] Checking ref:', ref);
                    // 后端返回的是序列化后的 TextChunk，字段名可能是 page_number 或通过 bbox.page
                    const refPage = (ref as any).page_number || (ref as any).bbox?.page;
                    console.log('[CompliancePanel] refPage:', refPage, 'target:', pageNum);
                    if (refPage === pageNum) {
                        // 构建高亮对象
                        const highlightRef = {
                            ...ref,
                            page: refPage,
                            // 确保 bbox 格式正确
                            bbox: (ref as any).bbox || {
                                page: refPage,
                                x: 0,
                                y: 0,
                                w: 100,
                                h: 20
                            }
                        };
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
                for (const ref of item.references) {
                    // 兼容 ref_id (后端返回) 和 refId (前端定义)
                    const currentRefId = (ref as any).ref_id || ref.refId;

                    if (currentRefId === targetRefId) {
                        const refPage = (ref as any).page_number || (ref as any).bbox?.page;

                        if (refPage) {
                            console.log('[CompliancePanel] Found matching ref:', ref);
                            // 构建高亮对象
                            const highlightRef = {
                                ...ref,
                                page: refPage,
                                // 确保使用正确的 bbox
                                bbox: ref.bbox || (ref as any).bbox || { page: refPage, x: 0, y: 0, w: 100, h: 20 }
                            };
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
                <textarea
                    className="req-input"
                    placeholder="请输入技术要求，每行一条..."
                    value={complianceRequirements}
                    onChange={(e) => setComplianceRequirements(e.target.value)}
                    disabled={loading}
                />
                <button
                    className="check-btn"
                    onClick={handleCheck}
                    disabled={loading || !currentDocumentId}
                >
                    {loading ? '正在检查...' : '开始合规性检查'}
                </button>
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

