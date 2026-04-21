import React, { useEffect, useMemo, useRef, useState } from 'react';
import { isMultimodalConfigured, resolveEffectiveMultimodalApiKey } from '../../constants/multimodal';
import { useDocumentStore } from '../../stores/documentStore';
import type { AuditProfile, MultimodalAuditItem, TextChunk } from '../../stores/documentStore';
import type { AuditProfilePayload } from '../../hooks/useVectorSearch';
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
                for (let page = start; page <= end; page += 1) result.add(page);
            }
            continue;
        }
        const page = Number(token);
        if (Number.isInteger(page) && page > 0) result.add(page);
    }
    return Array.from(result).sort((a, b) => a - b);
};

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

const cloneAuditProfile = (profile: AuditProfile): AuditProfile => ({
    ...profile,
    rules: profile.rules.map((rule) => ({ ...rule })),
});

const buildPayloadFromDraft = (profile: AuditProfile): AuditProfilePayload => ({
    name: profile.name.trim(),
    bidder_name_required: profile.bidderNameRequired,
    rules: profile.rules.map((rule) => ({
        id: rule.id.trim(),
        title: rule.title.trim(),
        instruction: rule.instruction.trim(),
        enabled: rule.enabled,
    })),
});

const createLocalRule = (index: number) => ({
    id: `rule_${Date.now()}_${index}`,
    title: '',
    instruction: '',
    enabled: true,
});

const createLocalProfileDraft = (existingProfiles: AuditProfile[]): AuditProfile => {
    const nextIndex = existingProfiles.length + 1;
    return {
        id: `draft_${Date.now()}`,
        name: `新建审核模板 ${nextIndex}`,
        bidderNameRequired: false,
        rules: [createLocalRule(1)],
        createdAt: '',
        updatedAt: '',
    };
};

const buildAuditStateFromProfiles = (
    profiles: AuditProfile[],
    preferredId: string,
    fallbackLegacyId: string
) => {
    const selected =
        profiles.find((profile) => profile.id === preferredId)
        || profiles.find((profile) => profile.id === fallbackLegacyId)
        || profiles[0]
        || null;

    return {
        auditProfiles: profiles,
        selectedAuditProfileId: selected?.id || '',
        auditProfileDraft: selected ? cloneAuditProfile(selected) : null,
        auditProfileDraftIsNew: false,
    };
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
        getAuditProfiles,
        createAuditProfile,
        updateAuditProfile,
        deleteAuditProfile,
        createMultimodalAuditJob,
        watchMultimodalAuditProgress,
        getMultimodalAuditJobResult,
    } = useVectorSearch();

    const [profilesLoading, setProfilesLoading] = useState(false);
    const [profileBusy, setProfileBusy] = useState(false);
    const [profileError, setProfileError] = useState('');
    const unwatchRef = useRef<(() => void) | null>(null);

    const selectedSavedProfile = useMemo(
        () => audit.auditProfiles.find((profile) => profile.id === audit.selectedAuditProfileId) || null,
        [audit.auditProfiles, audit.selectedAuditProfileId]
    );
    const currentDraft = audit.auditProfileDraft;
    const requiresBidderName = currentDraft?.bidderNameRequired || selectedSavedProfile?.bidderNameRequired || false;
    const isRunning = audit.progress?.status === 'queued' || audit.progress?.status === 'running';
    const multimodalReady = isMultimodalConfigured(config);
    const effectiveMultimodalApiKey = resolveEffectiveMultimodalApiKey(config);

    const isDraftDirty = useMemo(() => {
        if (!currentDraft) return false;
        if (audit.auditProfileDraftIsNew) return true;
        if (!selectedSavedProfile) return false;
        return JSON.stringify(buildPayloadFromDraft(currentDraft)) !== JSON.stringify(buildPayloadFromDraft(selectedSavedProfile));
    }, [audit.auditProfileDraftIsNew, currentDraft, selectedSavedProfile]);

    const loadProfiles = async (preferredId?: string) => {
        setProfilesLoading(true);
        setProfileError('');
        try {
            const profiles = await getAuditProfiles();
            setAuditState(buildAuditStateFromProfiles(
                profiles,
                preferredId || audit.selectedAuditProfileId || audit.lastAuditProfileId,
                audit.legacyAuditType
            ));
        } catch (error) {
            setProfileError(error instanceof Error ? error.message : '加载审核模板失败');
        } finally {
            setProfilesLoading(false);
        }
    };

    useEffect(() => {
        return () => {
            if (unwatchRef.current) {
                unwatchRef.current();
                unwatchRef.current = null;
            }
        };
    }, []);

    useEffect(() => {
        void loadProfiles();
        if (unwatchRef.current) {
            unwatchRef.current();
            unwatchRef.current = null;
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
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

    const validateDraft = (profile: AuditProfile | null): string => {
        if (!profile) return '请先选择或创建审核模板';
        if (!profile.name.trim()) return '模板名称不能为空';
        if (profile.rules.length === 0) return '至少保留一条审核项';
        if (!profile.rules.some((rule) => rule.enabled)) return '至少启用一条审核项';
        for (const rule of profile.rules) {
            if (!rule.title.trim()) return '审核项标题不能为空';
            if (!rule.instruction.trim()) return `审核项“${rule.title || rule.id}”的审核说明不能为空`;
        }
        return '';
    };

    const handleProfileSelect = (profileId: string) => {
        const selected = audit.auditProfiles.find((profile) => profile.id === profileId);
        if (!selected) return;
        setAuditState({
            selectedAuditProfileId: profileId,
            auditProfileDraft: cloneAuditProfile(selected),
            auditProfileDraftIsNew: profileId.startsWith('draft_'),
        });
        setProfileError('');
    };

    const updateDraft = (updater: (draft: AuditProfile) => AuditProfile) => {
        if (!currentDraft) return;
        setAuditState({ auditProfileDraft: updater(cloneAuditProfile(currentDraft)) });
    };

    const handleCreateProfile = () => {
        const draft = createLocalProfileDraft(audit.auditProfiles);
        setAuditState({
            auditProfiles: [...audit.auditProfiles, draft],
            selectedAuditProfileId: draft.id,
            auditProfileDraft: cloneAuditProfile(draft),
            auditProfileDraftIsNew: true,
        });
        setProfileError('');
    };

    const handleSaveProfile = async () => {
        const validationMessage = validateDraft(currentDraft);
        if (validationMessage) {
            window.alert(validationMessage);
            return;
        }
        if (!currentDraft) return;

        setProfileBusy(true);
        setProfileError('');
        try {
            const payload = buildPayloadFromDraft(currentDraft);
            const saved = audit.auditProfileDraftIsNew
                ? await createAuditProfile(payload)
                : await updateAuditProfile(currentDraft.id, payload);
            await loadProfiles(saved.id);
        } catch (error) {
            const message = error instanceof Error ? error.message : '保存审核模板失败';
            setProfileError(message);
            window.alert(message);
        } finally {
            setProfileBusy(false);
        }
    };

    const handleDeleteProfile = async () => {
        if (!currentDraft) return;
        const confirmed = window.confirm(`确定删除审核模板“${currentDraft.name}”吗？`);
        if (!confirmed) return;

        if (audit.auditProfileDraftIsNew) {
            const remaining = audit.auditProfiles.filter((profile) => profile.id !== currentDraft.id);
            if (remaining.length === 0) {
                window.alert('至少保留一个审核模板');
                return;
            }
            const nextSelected = remaining[0];
            setAuditState({
                auditProfiles: remaining,
                selectedAuditProfileId: nextSelected.id,
                auditProfileDraft: cloneAuditProfile(nextSelected),
                auditProfileDraftIsNew: false,
            });
            return;
        }

        setProfileBusy(true);
        setProfileError('');
        try {
            await deleteAuditProfile(currentDraft.id);
            const remainingSaved = audit.auditProfiles.filter((profile) => profile.id !== currentDraft.id);
            await loadProfiles(remainingSaved[0]?.id || '');
        } catch (error) {
            const message = error instanceof Error ? error.message : '删除审核模板失败';
            setProfileError(message);
            window.alert(message);
        } finally {
            setProfileBusy(false);
        }
    };

    const handleAddRule = () => {
        updateDraft((draft) => ({
            ...draft,
            rules: [...draft.rules, createLocalRule(draft.rules.length + 1)],
        }));
    };

    const handleRemoveRule = (ruleId: string) => {
        updateDraft((draft) => ({
            ...draft,
            rules: draft.rules.filter((rule) => rule.id !== ruleId),
        }));
    };

    const submitAudit = async () => {
        if (!currentDocument) return;
        const validationMessage = validateDraft(currentDraft);
        if (validationMessage) {
            window.alert(validationMessage);
            return;
        }
        if (isDraftDirty) {
            window.alert('当前审核模板有未保存的修改，请先保存后再启动专项审核。');
            return;
        }
        if (!selectedSavedProfile) {
            window.alert('请先选择已保存的审核模板');
            return;
        }
        if (!multimodalReady) {
            window.alert('请先在设置中配置可用的多模态模型后再启动专项审核。');
            return;
        }

        const bidderName = audit.bidderName.trim();
        if (requiresBidderName && !bidderName) {
            window.alert('当前审核模板要求填写投标人名称');
            return;
        }

        const allowedPages = parseAllowedPages(audit.allowedPagesText);

        setAuditState({
            progress: {
                jobId: '',
                status: 'queued',
                stage: 'queued',
                current: 0,
                total: 100,
                message: '正在创建审核任务...',
            },
            lastAuditProfileId: selectedSavedProfile.id,
            lastAuditProfileName: selectedSavedProfile.name,
            lastAuditProfileSnapshot: cloneAuditProfile(selectedSavedProfile),
        });

        const created = await createMultimodalAuditJob(currentDocument.id, {
            audit_profile_id: selectedSavedProfile.id,
            bidder_name: bidderName,
            allowed_pages: allowedPages,
            multimodal_provider: config.multimodalProvider,
            multimodal_api_key: effectiveMultimodalApiKey || undefined,
            multimodal_base_url: config.multimodalBaseUrl || undefined,
            multimodal_model: config.multimodalModel || undefined,
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
                        lastAuditProfileId: result.auditProfileId || selectedSavedProfile.id,
                        lastAuditProfileName: result.auditProfileName || selectedSavedProfile.name,
                        lastAuditProfileSnapshot: result.auditProfileSnapshot || cloneAuditProfile(selectedSavedProfile),
                        legacyAuditType: result.legacyAuditType,
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

    return (
        <div className="audit-panel">
            <div className="audit-form audit-form-top">
                <label className="audit-field audit-field-grow">
                    <span>审核模板</span>
                    <select
                        value={audit.selectedAuditProfileId}
                        onChange={(e) => handleProfileSelect(e.target.value)}
                        disabled={profilesLoading || profileBusy}
                    >
                        {audit.auditProfiles.map((profile) => (
                            <option key={profile.id} value={profile.id}>
                                {profile.name}
                            </option>
                        ))}
                    </select>
                </label>

                <div className="audit-profile-actions">
                    <button type="button" className="audit-secondary-btn" onClick={handleCreateProfile} disabled={profileBusy || profilesLoading || isRunning}>
                        新建
                    </button>
                    <button type="button" className="audit-secondary-btn primary" onClick={handleSaveProfile} disabled={profileBusy || profilesLoading || !currentDraft || isRunning}>
                        保存
                    </button>
                    <button type="button" className="audit-secondary-btn danger" onClick={handleDeleteProfile} disabled={profileBusy || profilesLoading || !currentDraft || isRunning}>
                        删除
                    </button>
                    <button
                        type="button"
                        className="audit-secondary-btn audit-run-btn"
                        onClick={submitAudit}
                        disabled={!currentDocument || isRunning || profilesLoading || profileBusy || !currentDraft}
                    >
                        {isRunning ? '审查中...' : '审查'}
                    </button>
                </div>
            </div>

            <div className="audit-form">
                <label className="audit-field">
                    <span>模板名称</span>
                    <input
                        value={currentDraft?.name || ''}
                        onChange={(e) => updateDraft((draft) => ({ ...draft, name: e.target.value }))}
                        placeholder="请输入审核模板名称"
                        disabled={!currentDraft || profileBusy || profilesLoading || isRunning}
                    />
                </label>

                <label className="audit-field">
                    <span>投标人名称</span>
                    <input
                        value={audit.bidderName}
                        onChange={(e) => setAuditState({ bidderName: e.target.value })}
                        placeholder={requiresBidderName ? '当前模板审核时必填' : '按需填写'}
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

                <label className="audit-field audit-checkbox-field">
                    <span>模板设置</span>
                    <label className="audit-checkbox-row">
                        <input
                            type="checkbox"
                            checked={currentDraft?.bidderNameRequired || false}
                            onChange={(e) => updateDraft((draft) => ({ ...draft, bidderNameRequired: e.target.checked }))}
                            disabled={!currentDraft || profileBusy || profilesLoading || isRunning}
                        />
                        <span>启动审核时要求填写投标人名称</span>
                    </label>
                </label>

                <div className="audit-rules-section">
                    <div className="audit-rules-header">
                        <div>
                            <span className="audit-rules-title">审核内容</span>
                            <p className="audit-rules-hint">每条审核项都会参与专项审查并写入模板，保存后永久生效。</p>
                        </div>
                        <button
                            type="button"
                            className="audit-secondary-btn"
                            onClick={handleAddRule}
                            disabled={!currentDraft || profileBusy || profilesLoading || isRunning}
                        >
                            新增审核项
                        </button>
                    </div>

                    <div className="audit-rule-list">
                        {currentDraft?.rules.map((rule, index) => (
                            <div key={rule.id} className="audit-rule-card">
                                <div className="audit-rule-card-header">
                                    <span className="audit-rule-index">审核项 {index + 1}</span>
                                    <label className="audit-checkbox-row">
                                        <input
                                            type="checkbox"
                                            checked={rule.enabled}
                                            onChange={(e) => updateDraft((draft) => ({
                                                ...draft,
                                                rules: draft.rules.map((item) => item.id === rule.id ? { ...item, enabled: e.target.checked } : item),
                                            }))}
                                            disabled={profileBusy || profilesLoading || isRunning}
                                        />
                                        <span>启用</span>
                                    </label>
                                    <button
                                        type="button"
                                        className="audit-rule-remove"
                                        onClick={() => handleRemoveRule(rule.id)}
                                        disabled={profileBusy || profilesLoading || isRunning || (currentDraft?.rules.length || 0) <= 1}
                                    >
                                        删除
                                    </button>
                                </div>

                                <label className="audit-field">
                                    <span>审核项标题</span>
                                    <input
                                        value={rule.title}
                                        onChange={(e) => updateDraft((draft) => ({
                                            ...draft,
                                            rules: draft.rules.map((item) => item.id === rule.id ? { ...item, title: e.target.value } : item),
                                        }))}
                                        placeholder="例如：证书有效期核验"
                                        disabled={profileBusy || profilesLoading || isRunning}
                                    />
                                </label>

                                <label className="audit-field">
                                    <span>审核说明</span>
                                    <textarea
                                        value={rule.instruction}
                                        onChange={(e) => updateDraft((draft) => ({
                                            ...draft,
                                            rules: draft.rules.map((item) => item.id === rule.id ? { ...item, instruction: e.target.value } : item),
                                        }))}
                                        placeholder="描述该审核项要检查什么、如何判断通过或不通过"
                                        rows={3}
                                        disabled={profileBusy || profilesLoading || isRunning}
                                    />
                                </label>
                            </div>
                        ))}
                    </div>
                </div>

            </div>

            {(profilesLoading || profileError || isDraftDirty) && (
                <div className={`audit-profile-banner ${profileError ? 'error' : isDraftDirty ? 'warning' : ''}`}>
                    {profilesLoading
                        ? '正在加载审核模板...'
                        : profileError
                            ? profileError
                            : '当前模板有未保存的修改，保存后才会用于后续专项审核。'}
                </div>
            )}

            {(audit.lastAuditProfileName || audit.generatedAt) && (
                <div className="audit-meta-banner">
                    {audit.lastAuditProfileName ? `最近结果模板：${audit.lastAuditProfileName}` : '最近结果模板：未记录'}
                    {audit.generatedAt ? ` ｜ 生成时间：${new Date(audit.generatedAt).toLocaleString()}` : ''}
                </div>
            )}

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
