import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useDocumentStore } from '../../stores/documentStore';
import type { BoundingBox, EvidenceItem } from '../../stores/documentStore';
import { useVectorSearch } from '../../hooks/useVectorSearch';
import './CompliancePanel.css';

interface CompliancePanelProps {
    className?: string;
}

type ViewMode = 'summary' | 'evidence';

const fallbackBbox = (page: number): BoundingBox => ({
    page,
    x: 0,
    y: 0,
    w: 100,
    h: 20,
});

export const CompliancePanel: React.FC<CompliancePanelProps> = ({ className }) => {
    const {
        currentDocument,
        config,
        setHighlights,
        setCurrentPage,
        complianceRequirements,
        complianceV2Result,
        evidenceItems,
        reviewState,
        setComplianceRequirements,
        setComplianceV2,
    } = useDocumentStore();

    const {
        checkComplianceV2,
        getComplianceV2History,
        getEvidence,
        submitReviewDecision,
    } = useVectorSearch();

    const [loading, setLoading] = useState(false);
    const [reviewing, setReviewing] = useState(false);
    const [viewMode, setViewMode] = useState<ViewMode>('summary');
    const currentDocumentId = currentDocument?.id;
    const apiBaseUrl = (config.apiBaseUrl || 'http://localhost:8000').replace(/\/$/, '');

    useEffect(() => {
        if (!currentDocumentId) return;
        let disposed = false;

        const bootstrap = async () => {
            const [history, evidence] = await Promise.all([
                getComplianceV2History(currentDocumentId),
                getEvidence(currentDocumentId),
            ]);

            if (disposed) return;

            if (history) {
                setComplianceV2({
                    complianceV2Result: history,
                    evidenceItems: evidence.length > 0 ? evidence : history.evidence,
                    reviewState: history.reviewState,
                });
                if (!complianceRequirements && history.requirements?.length) {
                    setComplianceRequirements(history.requirements.join('\n'));
                }
            } else {
                setComplianceV2({
                    complianceV2Result: null,
                    evidenceItems: [],
                    reviewState: null,
                });
            }
        };

        void bootstrap();
        return () => {
            disposed = true;
        };
    }, [
        currentDocumentId,
        getComplianceV2History,
        getEvidence,
        setComplianceV2,
        setComplianceRequirements,
        complianceRequirements,
    ]);

    const handleAnalyze = useCallback(async () => {
        if (!currentDocumentId) return;
        const requirements = complianceRequirements
            .split('\n')
            .map((item) => item.trim())
            .filter(Boolean);

        if (requirements.length === 0) {
            window.alert('请先输入审核要求，每行一条。');
            return;
        }

        setLoading(true);
        try {
            const result = await checkComplianceV2(currentDocumentId, {
                requirements,
                policy_set_id: 'contracts/base_rules',
                api_key: config.deepseekApiKey || config.zhipuApiKey || undefined,
                review_required: true,
            });

            if (!result) {
                throw new Error('合规分析失败');
            }

            setComplianceV2({
                complianceV2Result: result,
                evidenceItems: result.evidence || [],
                reviewState: result.reviewState,
            });
            setViewMode('summary');
        } catch (error) {
            console.error(error);
            window.alert('合规分析失败，请查看后端日志。');
        } finally {
            setLoading(false);
        }
    }, [
        checkComplianceV2,
        complianceRequirements,
        config.deepseekApiKey,
        config.zhipuApiKey,
        currentDocumentId,
        setComplianceV2,
    ]);

    const jumpToEvidence = useCallback(
        (evidence: EvidenceItem) => {
            const page = evidence.page || evidence.bbox?.page || 1;
            const bbox = evidence.bbox || fallbackBbox(page);
            setHighlights([
                {
                    id: `${evidence.refId}_${page}`,
                    refId: evidence.refId,
                    content: evidence.content || '',
                    page,
                    bbox,
                    source: evidence.sourceType === 'derived' ? 'native' : evidence.sourceType,
                },
            ]);
            setCurrentPage(page);
        },
        [setCurrentPage, setHighlights]
    );

    const submitReview = useCallback(
        async (decision: 'approved' | 'rejected') => {
            if (!currentDocumentId) return;
            setReviewing(true);
            try {
                const next = await submitReviewDecision(
                    currentDocumentId,
                    decision,
                    'manual-reviewer',
                    decision === 'approved' ? '人工复核通过' : '人工复核拒绝'
                );
                if (!next) throw new Error('review failed');

                setComplianceV2({
                    reviewState: next,
                    complianceV2Result: complianceV2Result
                        ? {
                              ...complianceV2Result,
                              reviewState: next,
                          }
                        : complianceV2Result,
                    evidenceItems,
                });
            } catch (error) {
                console.error(error);
                window.alert('提交复核失败，请重试。');
            } finally {
                setReviewing(false);
            }
        },
        [
            complianceV2Result,
            currentDocumentId,
            evidenceItems,
            setComplianceV2,
            submitReviewDecision,
        ]
    );

    const mergedEvidence = useMemo(() => {
        if (evidenceItems.length > 0) return evidenceItems;
        return complianceV2Result?.evidence || [];
    }, [complianceV2Result?.evidence, evidenceItems]);

    const displayReviewState = reviewState || complianceV2Result?.reviewState || null;

    return (
        <div className={`compliance-panel ${className || ''}`}>
            <div className="input-section">
                <textarea
                    className="req-input"
                    placeholder="请输入合同合规审核要求，每行一条"
                    value={complianceRequirements}
                    onChange={(event) => setComplianceRequirements(event.target.value)}
                    disabled={loading}
                />
                <div className="toolbar-row">
                    <button className="check-btn" onClick={handleAnalyze} disabled={loading || !currentDocumentId}>
                        {loading ? '分析中...' : '开始合规分析 V2'}
                    </button>
                    <span className="api-base-label">API: {apiBaseUrl}</span>
                </div>
            </div>

            <div className="view-tabs">
                <button
                    className={`view-tab ${viewMode === 'summary' ? 'active' : ''}`}
                    onClick={() => setViewMode('summary')}
                >
                    结论视图
                </button>
                <button
                    className={`view-tab ${viewMode === 'evidence' ? 'active' : ''}`}
                    onClick={() => setViewMode('evidence')}
                >
                    证据视图
                </button>
            </div>

            {!complianceV2Result ? (
                <div className="empty-state">
                    <p>尚无 V2 分析结果。输入要求后点击“开始合规分析 V2”。</p>
                </div>
            ) : (
                <div className="results-section">
                    {viewMode === 'summary' ? (
                        <>
                            <div className="summary-card">
                                <div className="summary-item"><strong>结论:</strong> {complianceV2Result.decision}</div>
                                <div className="summary-item"><strong>风险:</strong> {complianceV2Result.riskLevel}</div>
                                <div className="summary-item">
                                    <strong>置信度:</strong> {(complianceV2Result.confidence * 100).toFixed(1)}%
                                </div>
                            </div>
                            <div className="summary-text">{complianceV2Result.summary || '无摘要。'}</div>

                            <h4>字段提取</h4>
                            <table className="compliance-table">
                                <thead>
                                    <tr>
                                        <th>字段</th>
                                        <th>提取值</th>
                                        <th>状态</th>
                                        <th>置信度</th>
                                        <th>证据</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {complianceV2Result.fieldResults.map((item, idx) => (
                                        <tr key={`${item.fieldKey}_${idx}`}>
                                            <td>{item.fieldName}</td>
                                            <td>{item.value || '-'}</td>
                                            <td>{item.status}</td>
                                            <td>{(item.confidence * 100).toFixed(1)}%</td>
                                            <td>{item.evidenceRefs.join(', ') || '-'}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>

                            <h4>规则判定</h4>
                            <table className="compliance-table">
                                <thead>
                                    <tr>
                                        <th>规则</th>
                                        <th>状态</th>
                                        <th>说明</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {complianceV2Result.ruleResults.map((item, idx) => (
                                        <tr key={`${item.ruleId}_${idx}`}>
                                            <td>{item.ruleName}</td>
                                            <td>{item.status}</td>
                                            <td>{item.message}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>

                            <div className="review-panel">
                                <div className="review-status">
                                    <strong>人工复核状态:</strong> {displayReviewState?.state || 'pending_review'}
                                </div>
                                <div className="review-actions">
                                    <button
                                        className="review-btn approve"
                                        onClick={() => void submitReview('approved')}
                                        disabled={reviewing || !currentDocumentId}
                                    >
                                        复核通过
                                    </button>
                                    <button
                                        className="review-btn reject"
                                        onClick={() => void submitReview('rejected')}
                                        disabled={reviewing || !currentDocumentId}
                                    >
                                        复核拒绝
                                    </button>
                                </div>
                            </div>
                        </>
                    ) : (
                        <div className="evidence-list">
                            {mergedEvidence.length === 0 ? (
                                <div className="empty-state"><p>暂无证据数据。</p></div>
                            ) : (
                                mergedEvidence.map((item, idx) => (
                                    <button
                                        type="button"
                                        key={`${item.refId}_${idx}`}
                                        className="evidence-item"
                                        onClick={() => jumpToEvidence(item)}
                                    >
                                        <div className="evidence-head">
                                            <span className="evidence-ref">{item.refId}</span>
                                            <span className="evidence-meta">
                                                第 {item.page} 页 / {item.sourceType} / {item.supportLevel}
                                            </span>
                                        </div>
                                        <div className="evidence-field">{item.fieldName}</div>
                                        <div className="evidence-content">{item.content || '无文本片段'}</div>
                                    </button>
                                ))
                            )}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};
