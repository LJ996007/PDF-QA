import React, { useEffect, useRef } from 'react';
import { useDocumentStore } from '../../stores/documentStore';
import type { AuditType, MultimodalAuditItem, TextChunk } from '../../stores/documentStore';
import { useVectorSearch } from '../../hooks/useVectorSearch';
import './MultimodalAuditPanel.css';

const REF_PATTERN = /\[ref-(\d+)\]/g;

const parseAllowedPages = (raw: string): number[] => {
    const tokens = raw.split(/[,\s]+/).map((item) => item.trim()).filter(Boolean);
    const result = new Set<number>();
    for (const token of tokens) {
        const rangeMatch = token.match(/^(\d+)-(\d+)$/);
        if (rangeMatch) {
            const start = Number(rangeMatch[1]);
            const end = Number(rangeMatch[2]);
            if (Number.isInteger(start) && Number.isInteger(end) && start > 0 && end >= start) {
                for (let p = start; p <= end; p += 1) result.add(p);
            }
            continue;
        }
        const page = Number(token);
        if (Number.isInteger(page) && page > 0) result.add(page);
    }
    return Array.from(result).sort((a, b) => a - b);
};

const parseCustomChecks = (raw: string): string[] =>
    raw.split('\n').map((line) => line.trim()).filter(Boolean);

const statusLabel = (status: MultimodalAuditItem['status']): string => {
    if (status === 'pass') return '通过';
    if (status === 'fail') return '不通过';
    if (status === 'error') return '错误';
    return '需复核';
};

const statusClass = (status: MultimodalAuditItem['status']): string => {
    if (status === 'pass') return 'status-pass';
    if (status === 'fail') return 'status-fail';
    if (status === 'error') return 'status-error';
    return 'status-review';
};

export const MultimodalAuditPanel: React.FC = () => {
    const {
        currentDocument,
        audit,
        config,
        setAuditState,
        setHighlights,
        setCurrentPage,
    } = useDocumentStore();
    const {
        createMultimodalAuditJob,
        watchMultimodalAuditProgress,
        getMultimodalAuditJobResult,
    } = useVectorSearch();

    const unwatchRef = useRef<(() => void) | null>(null);

    useEffect(() => {
        return () => {
            if (unwatchRef.current) {
                unwatchRef.current();
                unwatchRef.current = null;
            }
        };
    }, []);

    useEffect(() => {
        if (unwatchRef.current) {
            unwatchRef.current();
            unwatchRef.current = null;
        }
    }, [currentDocument?.id]);

    const handleRefClick = (refId: string) => {
        for (const item of audit.items) {
            const ref = item.references.find((r) => r.refId === refId || r.ref_id === refId);
            if (!ref) continue;
            const page = Number(ref.page || ref.bbox?.page || 1);
            const normalized: TextChunk = {
                ...ref,
                refId,
                id: ref.id || `${currentDocument?.id || 'doc'}_${refId}_${page}`,
                page,
                bbox: ref.bbox || { page, x: 0, y: 0, w: 100, h: 20 },
                source: ref.source || 'native',
            };
            setHighlights([normalized]);
            setCurrentPage(page);
            return;
        }
    };

    const renderReason = (reason: string) => {
        const parts = reason.split(REF_PATTERN);
        if (parts.length <= 1) return reason;
        return parts.map((part, idx) => {
            if (idx % 2 === 1) {
                const refId = `ref-${part}`;
                return (
                    <button
                        key={`${refId}_${idx}`}
                        className="audit-ref-btn"
                        onClick={() => handleRefClick(refId)}
                    >
                        {refId}
                    </button>
                );
            }
            return <React.Fragment key={idx}>{part}</React.Fragment>;
        });
    };

    const submitAudit = async () => {
        if (!currentDocument) return;
        const auditType: AuditType = audit.auditType;
        const bidderName = audit.bidderName.trim();
        if ((auditType === 'certificate' || auditType === 'personnel') && !bidderName) {
            window.alert('证件和人员资质审核必须填写投标人名称');
            return;
        }

        const allowedPages = parseAllowedPages(audit.allowedPagesText);
        const customChecks = parseCustomChecks(audit.customChecksText);

        setAuditState({
            progress: {
                jobId: '',
                status: 'queued',
                stage: 'queued',
                current: 0,
                total: 100,
                message: '正在创建审核任务...',
            },
        });

        const created = await createMultimodalAuditJob(currentDocument.id, {
            audit_type: auditType,
            bidder_name: bidderName,
            allowed_pages: allowedPages,
            custom_checks: customChecks,
            api_key: config.dashscopeApiKey || undefined,
        });
        if (!created) {
            setAuditState({
                progress: {
                    jobId: '',
                    status: 'failed',
                    stage: 'failed',
                    current: 100,
                    total: 100,
                    message: '审核任务创建失败',
                },
            });
            return;
        }

        if (unwatchRef.current) {
            unwatchRef.current();
            unwatchRef.current = null;
        }

        setAuditState({
            lastJobId: created.job_id,
            progress: {
                jobId: created.job_id,
                status: created.status === 'queued' ? 'queued' : 'running',
                stage: 'queued',
                current: 0,
                total: 100,
                message: '任务已创建，等待执行',
            },
            items: [],
            summary: { pass: 0, fail: 0, needs_review: 0, error: 0, total: 0 },
        });

        unwatchRef.current = watchMultimodalAuditProgress(currentDocument.id, created.job_id, async (progress) => {
            setAuditState({
                progress: {
                    jobId: created.job_id,
                    status: progress.status,
                    stage: progress.stage,
                    current: Number(progress.current || 0),
                    total: Number(progress.total || 100),
                    message: progress.message || '',
                },
            });
            if (progress.stage === 'completed') {
                const result = await getMultimodalAuditJobResult(currentDocument.id, created.job_id);
                if (result) {
                    setAuditState({
                        items: result.items,
                        summary: result.summary,
                        generatedAt: result.generatedAt,
                        progress: {
                            jobId: result.jobId,
                            status: 'completed',
                            stage: 'completed',
                            current: 100,
                            total: 100,
                            message: '专项审核完成',
                        },
                    });
                }
            }
        });
    };

    const isRunning = audit.progress?.status === 'queued' || audit.progress?.status === 'running';

    return (
        <div className="audit-panel">
            <div className="audit-form">
                <label className="audit-field">
                    <span>审核类型</span>
                    <select
                        value={audit.auditType}
                        onChange={(e) => setAuditState({ auditType: e.target.value as AuditType })}
                        disabled={isRunning}
                    >
                        <option value="contract">合同扫描件审核</option>
                        <option value="certificate">证件扫描件审核</option>
                        <option value="personnel">人员资质审核</option>
                    </select>
                </label>
                <label className="audit-field">
                    <span>投标人名称</span>
                    <input
                        value={audit.bidderName}
                        onChange={(e) => setAuditState({ bidderName: e.target.value })}
                        placeholder="证件/资质审核必填"
                        disabled={isRunning}
                    />
                </label>
                <label className="audit-field">
                    <span>页范围</span>
                    <input
                        value={audit.allowedPagesText}
                        onChange={(e) => setAuditState({ allowedPagesText: e.target.value })}
                        placeholder="例如: 1-5,8,10"
                        disabled={isRunning}
                    />
                </label>
                <label className="audit-field">
                    <span>自定义补充规则</span>
                    <textarea
                        value={audit.customChecksText}
                        onChange={(e) => setAuditState({ customChecksText: e.target.value })}
                        placeholder="每行一条"
                        rows={3}
                        disabled={isRunning}
                    />
                </label>
                <button
                    className="audit-submit-btn"
                    onClick={submitAudit}
                    disabled={!currentDocument || isRunning}
                >
                    {isRunning ? '审核中...' : '启动专项审核'}
                </button>
            </div>

            {audit.progress && (
                <div className="audit-progress">
                    <div className="audit-progress-meta">
                        <span>{audit.progress.message || '处理中...'}</span>
                        <span>{audit.progress.current}/{audit.progress.total}</span>
                    </div>
                    <div className="audit-progress-track">
                        <div
                            className="audit-progress-bar"
                            style={{ width: `${Math.max(0, Math.min(100, (audit.progress.current / Math.max(1, audit.progress.total)) * 100))}%` }}
                        />
                    </div>
                </div>
            )}

            <div className="audit-summary">
                <span>通过: {audit.summary.pass}</span>
                <span>不通过: {audit.summary.fail}</span>
                <span>需复核: {audit.summary.needs_review}</span>
                <span>错误: {audit.summary.error}</span>
                <span>总数: {audit.summary.total}</span>
            </div>

            <div className="audit-results">
                {audit.items.length === 0 ? (
                    <div className="audit-empty">暂无专项审核结果</div>
                ) : (
                    <table className="audit-table">
                        <thead>
                            <tr>
                                <th>检查项</th>
                                <th>状态</th>
                                <th>说明</th>
                                <th>置信度</th>
                            </tr>
                        </thead>
                        <tbody>
                            {audit.items.map((item) => (
                                <tr key={item.checkKey}>
                                    <td>{item.checkTitle}</td>
                                    <td><span className={`audit-status ${statusClass(item.status)}`}>{statusLabel(item.status)}</span></td>
                                    <td>{renderReason(item.reason)}</td>
                                    <td>{Math.round((item.confidence || 0) * 100)}%</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>
        </div>
    );
};
