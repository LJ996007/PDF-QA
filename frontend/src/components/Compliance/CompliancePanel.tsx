import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useDocumentStore } from '../../stores/documentStore';
import type { TextChunk } from '../../stores/documentStore';
import './CompliancePanel.css';

interface CompliancePanelProps {
    className?: string;
}

type BackendReference = TextChunk & {
    page_number?: number;
    ref_id?: string;
};

export const CompliancePanel: React.FC<CompliancePanelProps> = ({ className }) => {
    const {
        currentDocument,
        config,
        focusReference,
        focusPage,
        complianceResults,
        complianceMarkdown,
        complianceRequirements,
        setComplianceResults,
        setComplianceRequirements,
    } = useDocumentStore();

    const currentDocumentId = currentDocument?.id;
    const recognizedPages = currentDocument?.recognizedPages || [];
    const canRunCompliance = Boolean(currentDocumentId && recognizedPages.length > 0);
    const apiBaseUrl = config.apiBaseUrl || 'http://localhost:8000';

    const [loading, setLoading] = useState(false);

    const getRefPage = (ref: BackendReference): number => {
        return ref.page_number || ref.page || ref.bbox?.page || 0;
    };

    const toFocusChunk = (refValue: BackendReference): TextChunk | null => {
        const refPage = getRefPage(refValue);
        if (!refPage) {
            return null;
        }

        return {
            ...refValue,
            page: refPage,
            bbox: refValue.bbox || {
                page: refPage,
                x: 0,
                y: 0,
                w: 120,
                h: 120,
            },
        };
    };

    const handleCheck = async () => {
        if (!currentDocumentId || !complianceRequirements.trim() || !canRunCompliance) {
            return;
        }

        setLoading(true);
        try {
            const reqList = complianceRequirements.split('\n').map((r) => r.trim()).filter(Boolean);

            const response = await fetch(`${apiBaseUrl}/api/documents/${currentDocumentId}/compliance`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    requirements: reqList,
                    api_key: config.deepseekApiKey || config.zhipuApiKey,
                    allowed_pages: recognizedPages,
                }),
            });

            if (!response.ok) {
                throw new Error('Check failed');
            }

            const data = await response.json();
            setComplianceResults(data.results || data, data.markdown || '');
        } catch (error) {
            console.error(error);
            alert('\u68C0\u67E5\u5931\u8D25\uFF0C\u8BF7\u91CD\u8BD5');
        } finally {
            setLoading(false);
        }
    };

    const handlePageClick = (pageNum: number) => {
        for (const item of complianceResults) {
            if (!item.references || item.references.length === 0) {
                continue;
            }

            for (const ref of item.references) {
                const refValue = ref as BackendReference;
                const refPage = getRefPage(refValue);
                if (refPage === pageNum) {
                    const chunk = toFocusChunk(refValue);
                    if (chunk) {
                        focusReference(chunk, 'compliance');
                    } else {
                        focusPage(pageNum, 'compliance');
                    }
                    return;
                }
            }
        }

        focusPage(pageNum, 'compliance');
    };

    const handleRefClick = (refIndex: number) => {
        const targetRefId = `ref-${refIndex}`;

        for (const item of complianceResults) {
            if (!item.references || item.references.length === 0) {
                continue;
            }

            for (const ref of item.references) {
                const refValue = ref as BackendReference;
                const currentRefId = refValue.ref_id || refValue.refId;
                if (currentRefId !== targetRefId) {
                    continue;
                }

                const refPage = getRefPage(refValue);
                if (!refPage) {
                    continue;
                }

                const chunk = toFocusChunk(refValue);
                if (chunk) {
                    focusReference(chunk, 'compliance');
                } else {
                    focusPage(refPage, 'compliance');
                }
                return;
            }
        }
    };

    return (
        <div className={`compliance-panel ${className || ''}`}>
            <div className="input-section">
                <textarea
                    className="req-input"
                    placeholder="\u8BF7\u8F93\u5165\u6280\u672F\u8981\u6C42\uFF0C\u6BCF\u884C\u4E00\u6761..."
                    value={complianceRequirements}
                    onChange={(e) => setComplianceRequirements(e.target.value)}
                    disabled={loading}
                />
                <button
                    className="check-btn"
                    onClick={handleCheck}
                    disabled={loading || !canRunCompliance}
                >
                    {loading ? '\u6B63\u5728\u68C0\u67E5...' : '\u5F00\u59CB\u5408\u89C4\u6027\u68C0\u67E5'}
                </button>
            </div>

            <div className="results-section markdown-result">
                {!canRunCompliance ? (
                    <div className="empty-state">
                        <p>{'\u8BF7\u5148\u8BC6\u522B\u9875\u9762\uFF0C\u518D\u8FDB\u884C\u5408\u89C4\u68C0\u67E5\u3002'}</p>
                    </div>
                ) : complianceMarkdown ? (
                    <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                            p: ({ children }) => <p>{processPageRefs(children, handlePageClick, handleRefClick)}</p>,
                            td: ({ children }) => <td>{processPageRefs(children, handlePageClick, handleRefClick)}</td>,
                        }}
                    >
                        {complianceMarkdown}
                    </ReactMarkdown>
                ) : (
                    <div className="empty-state">
                        <p>{'\u8F93\u5165\u6280\u672F\u8981\u6C42\u540E\u70B9\u51FB\u201C\u5F00\u59CB\u5408\u89C4\u6027\u68C0\u67E5\u201D'}</p>
                    </div>
                )}
            </div>
        </div>
    );
};

function processPageRefs(
    children: React.ReactNode,
    onPageClick: (page: number) => void,
    onRefClick?: (refIndex: number) => void
): React.ReactNode {
    if (typeof children === 'string') {
        const parts = children.split(/(\[\[P\d+\]\]|\[ref-\d+\])/g);
        return parts.map((part, index) => {
            const pageMatch = part.match(/\[\[P(\d+)\]\]/);
            if (pageMatch) {
                const pageNum = parseInt(pageMatch[1], 10);
                return (
                    <span
                        key={index}
                        className="ref-tag"
                        onClick={() => onPageClick(pageNum)}
                        title={`\u8DF3\u8F6C\u5230\u7B2C ${pageNum} \u9875`}
                    >
                        P{pageNum}
                    </span>
                );
            }

            const refMatch = part.match(/\[ref-(\d+)\]/);
            if (refMatch) {
                const refIndex = parseInt(refMatch[1], 10);
                return (
                    <span
                        key={index}
                        className="ref-tag ref-inline"
                        onClick={() => onRefClick?.(refIndex)}
                        title={`\u8DF3\u8F6C\u5230\u5F15\u7528 ${refIndex}`}
                    >
                        {refIndex}
                    </span>
                );
            }

            return part;
        });
    }

    if (Array.isArray(children)) {
        return children.map((child, index) => (
            <React.Fragment key={index}>
                {processPageRefs(child, onPageClick, onRefClick)}
            </React.Fragment>
        ));
    }

    return children;
}
