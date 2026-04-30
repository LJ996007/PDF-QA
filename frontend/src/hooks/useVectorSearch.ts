import { useCallback } from 'react';
import { resolveEffectiveMultimodalApiKey } from '../constants/multimodal';
import { appendPageReferenceGroupsToHistoryContent } from '../utils/chatPageReferences';
import { useDocumentStore } from '../stores/documentStore';
import type {
    AuditReference,
    AuditProfile,
    AuditProfileRule,
    BoundingBox,
    ChatMessage,
    ChatPageReferenceGroup,
    ComplianceItem,
    LegacyAuditType,
    MultimodalAuditItem,
    MultimodalAuditSummary,
    TextChunk,
} from '../stores/documentStore';

export interface ChatStreamEvent {
    type: 'thinking' | 'references' | 'content' | 'done' | 'error';
    content?: string;
    text?: string;
    refs?: Array<{
        ref_id: string;
        chunk_id: string;
        page: number;
        bbox: {
            page: number;
            x: number;
            y: number;
            w: number;
            h: number;
        };
        content: string;
    }>;
    active_refs?: string[];
    final_refs?: string[];
}

export interface HistoryDocumentItem {
    doc_id: string;
    filename: string;
    created_at: string;
    total_pages: number;
    ocr_required_pages: number[];
    sha256: string;
    status: string;
    has_pdf?: boolean;
    source_format?: 'pdf' | 'doc' | 'docx';
    converted_from?: 'doc' | 'docx' | null;
    conversion_status?: 'pending' | 'ok' | 'failed' | null;
    conversion_ms?: number | null;
    conversion_fail_count?: number;
    ocr_triggered_pages?: number;
    indexed_chunks?: number;
    avg_context_tokens?: number | null;
    context_query_count?: number;
    text_fallback_used?: boolean;
}

export interface MultimodalAuditJobCreateRequest {
    audit_profile_id: string;
    bidder_name: string;
    allowed_pages: number[];
    multimodal_provider?: string;
    multimodal_api_key?: string;
    multimodal_base_url?: string;
    multimodal_model?: string;
    api_key?: string;
    model?: string;
}

export interface MultimodalAuditJobCreateResponse {
    job_id: string;
    status: 'queued' | 'running' | 'completed' | 'failed';
    progress_url: string;
    result_url: string;
}

export interface MultimodalAuditProgressEvent {
    job_id: string;
    doc_id: string;
    stage: string;
    status: 'queued' | 'running' | 'completed' | 'failed';
    current: number;
    total: number;
    message?: string;
}

export interface MultimodalAuditJobResult {
    jobId: string;
    status: 'queued' | 'running' | 'completed' | 'failed';
    generatedAt: string;
    auditProfileId: string;
    auditProfileName: string;
    auditProfileSnapshot: AuditProfile | null;
    legacyAuditType: LegacyAuditType | '';
    items: MultimodalAuditItem[];
    summary: MultimodalAuditSummary;
}

export interface AuditProfilePayload {
    name: string;
    bidder_name_required: boolean;
    rules: Array<{
        id: string;
        title: string;
        instruction: string;
        enabled: boolean;
    }>;
}

export type RecognizePagesResponse = Record<string, unknown> & {
    pages?: number[];
    message?: string;
};

export type DocumentProgressEvent = Record<string, unknown> & {
    stage?: string;
    current?: number;
    total?: number;
    message?: string;
    document_id?: string;
};

const getErrorMessageFromPayload = (payload: unknown): string => {
    if (!payload || typeof payload !== 'object') return '';

    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === 'string' && detail.trim()) {
        return detail.trim();
    }
    if (Array.isArray(detail)) {
        const parts = detail
            .map((item) => {
                if (typeof item === 'string') return item.trim();
                if (item && typeof item === 'object' && 'msg' in item) {
                    return String((item as { msg?: unknown }).msg || '').trim();
                }
                return '';
            })
            .filter(Boolean);
        if (parts.length > 0) {
            return parts.join('；');
        }
    }

    const message = (payload as { message?: unknown }).message;
    if (typeof message === 'string' && message.trim()) {
        return message.trim();
    }
    return '';
};

const readApiErrorMessage = async (response: Response, fallback: string): Promise<string> => {
    const contentType = response.headers.get('content-type') || '';

    if (contentType.includes('application/json')) {
        try {
            const payload = await response.json();
            const message = getErrorMessageFromPayload(payload);
            if (message) {
                return message;
            }
        } catch {
            // Fall through to text parsing.
        }
    }

    try {
        const text = (await response.text()).trim();
        if (!text) {
            return fallback;
        }

        try {
            const payload = JSON.parse(text);
            const message = getErrorMessageFromPayload(payload);
            if (message) {
                return message;
            }
        } catch {
            // Plain-text error body.
        }

        return text;
    } catch {
        return fallback;
    }
};

type UnknownRecord = Record<string, unknown>;

const isRecord = (value: unknown): value is UnknownRecord =>
    Boolean(value) && typeof value === 'object' && !Array.isArray(value);

const toText = (value: unknown, fallback = ''): string => {
    if (value === undefined || value === null) return fallback;
    return String(value);
};

const toNumber = (value: unknown, fallback = 0): number => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
};

const normalizeBbox = (rawBbox: unknown, page: number): BoundingBox => {
    const bbox = isRecord(rawBbox) ? rawBbox : {};
    const bboxPage = toNumber(bbox.page, page);
    return {
        page: bboxPage,
        x: toNumber(bbox.x, 0),
        y: toNumber(bbox.y, 0),
        w: toNumber(bbox.w, 100),
        h: toNumber(bbox.h, 20),
    };
};

const mapAuditReference = (docId: string, ref: unknown, index: number): AuditReference => {
    const raw = isRecord(ref) ? ref : {};
    const rawBbox = isRecord(raw.bbox) ? raw.bbox : {};
    const page = toNumber(raw.page || rawBbox.page, 1);
    const refId = toText(raw.ref_id || raw.refId, `ref-${index + 1}`);
    const bbox = normalizeBbox(raw.bbox, page);
    const evidenceText = toText(raw.evidence_text || raw.content);
    return {
        id: `${docId}_${refId}_${index + 1}`,
        refId,
        ref_id: refId,
        content: evidenceText,
        evidence_text: evidenceText,
        page,
        bbox,
        source: toText(raw.source, 'native'),
    };
};

const mapAuditItem = (docId: string, item: unknown, idx: number): MultimodalAuditItem => {
    const raw = isRecord(item) ? item : {};
    const refsRaw = Array.isArray(raw.references) ? raw.references : [];
    const rawStatus = toText(raw.status);
    return {
        checkKey: toText(raw.check_key || raw.checkKey, `check_${idx + 1}`),
        checkTitle: toText(raw.check_title || raw.checkTitle, `检查项 ${idx + 1}`),
        status: ['pass', 'fail', 'needs_review', 'error'].includes(rawStatus)
            ? (rawStatus as MultimodalAuditItem['status'])
            : 'needs_review',
        reason: toText(raw.reason),
        confidence: toNumber(raw.confidence, 0),
        references: refsRaw.map((ref, refIdx) => mapAuditReference(docId, ref, refIdx)),
    };
};

const mapAuditProfileRule = (rule: unknown, index: number): AuditProfileRule | null => {
    if (!isRecord(rule)) return null;
    const title = toText(rule.title).trim();
    const instruction = toText(rule.instruction).trim();
    if (!title || !instruction) return null;
    return {
        id: toText(rule.id, `rule_${index + 1}`),
        title,
        instruction,
        enabled: rule.enabled !== false,
    };
};

const mapAuditProfile = (profile: unknown): AuditProfile | null => {
    if (!isRecord(profile)) return null;
    const rulesRaw = Array.isArray(profile.rules) ? profile.rules : [];
    const rules = rulesRaw
        .map((rule, index) => mapAuditProfileRule(rule, index))
        .filter((rule: AuditProfileRule | null): rule is AuditProfileRule => Boolean(rule));
    const id = toText(profile.id).trim();
    const name = toText(profile.name).trim();
    if (!id || !name || rules.length === 0) return null;
    return {
        id,
        name,
        bidderNameRequired: Boolean(profile.bidder_name_required ?? profile.bidderNameRequired),
        rules,
        createdAt: toText(profile.created_at || profile.createdAt),
        updatedAt: toText(profile.updated_at || profile.updatedAt),
    };
};

const mapAuditSummary = (summaryRaw: unknown, itemCount: number): MultimodalAuditSummary => {
    const summary = isRecord(summaryRaw) ? summaryRaw : {};
    return {
        pass: toNumber(summary.pass, 0),
        fail: toNumber(summary.fail, 0),
        needs_review: toNumber(summary.needs_review, 0),
        error: toNumber(summary.error, 0),
        total: toNumber(summary.total, itemCount),
    };
};

const mapLegacyAuditType = (value: unknown): LegacyAuditType | '' =>
    value === 'contract' || value === 'certificate' || value === 'personnel'
        ? value
        : '';

export function useVectorSearch() {
    const {
        currentDocument,
        config,
        messages,
        setHighlights,
        addMessage,
        appendToMessage,
        updateMessage,
        setLoading,
    } = useDocumentStore();

    const apiOrigin = (config.apiBaseUrl || 'http://localhost:8000').replace(/\/$/, '');
    const API_BASE = `${apiOrigin}/api`;
    const effectiveMultimodalApiKey = resolveEffectiveMultimodalApiKey(config);

    const askQuestion = useCallback(
        async (
            question: string,
            opts?: {
                useContext?: boolean;
                allowedPages?: number[];
                useVision?: boolean;
                pageReferenceGroups?: ChatPageReferenceGroup[];
            }
        ): Promise<void> => {
            if (!currentDocument) {
                throw new Error('未打开任何文档');
            }

            const useContext = opts?.useContext !== false;
            const pageReferenceGroups = Array.isArray(opts?.pageReferenceGroups) ? opts.pageReferenceGroups : [];
            const historyPayload = useContext
                ? messages
                      .filter((m) => !m.isStreaming)
                      .slice(-20)
                      .map((m) => ({
                          role: m.role,
                          content: m.role === 'user'
                              ? appendPageReferenceGroupsToHistoryContent(m.content, m.pageReferenceGroups || [])
                              : m.content,
                      }))
                : [];

            const userMessageId = `user_${Date.now()}`;
            addMessage({
                id: userMessageId,
                role: 'user',
                content: question,
                references: [],
                pageReferenceGroups,
                activeRefs: [],
                timestamp: new Date(),
            });

            const assistantMessageId = `assistant_${Date.now()}`;
            addMessage({
                id: assistantMessageId,
                role: 'assistant',
                content: '',
                references: [],
                activeRefs: [],
                timestamp: new Date(),
                isStreaming: true,
            });

            setLoading(true);

            try {
                const response = await fetch(`${API_BASE}/chat`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        document_id: currentDocument.id,
                        question,
                        history: historyPayload,
                        zhipu_api_key: config.zhipuApiKey,
                        deepseek_api_key: config.deepseekApiKey,
                        mimo_api_key: config.mimoApiKey || undefined,
                        llm_provider: config.llmProvider !== 'auto' ? config.llmProvider : undefined,
                        ...(opts?.allowedPages && opts.allowedPages.length > 0
                            ? { allowed_pages: opts.allowedPages }
                            : {}),
                        ...(pageReferenceGroups.length > 0
                            ? { page_reference_groups: pageReferenceGroups }
                            : {}),
                        ...(opts?.useVision
                            ? {
                                  use_vision: true,
                                  multimodal_provider: config.multimodalProvider,
                                  multimodal_api_key: effectiveMultimodalApiKey || undefined,
                                  multimodal_base_url: config.multimodalBaseUrl || undefined,
                                  multimodal_model: config.multimodalModel || undefined,
                              }
                            : {}),
                    }),
                });

                if (!response.ok) {
                    throw new Error('请求失败');
                }

                const reader = response.body?.getReader();
                if (!reader) {
                    throw new Error('无法读取响应流');
                }

                const decoder = new TextDecoder();
                let buffer = '';
                let references: TextChunk[] = [];

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });

                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';

                    for (const line of lines) {
                        if (!line.startsWith('data: ')) continue;

                        try {
                            const data: ChatStreamEvent = JSON.parse(line.slice(6));

                            if (data.type === 'references' && data.refs) {
                                references = data.refs.map((ref) => ({
                                    id: ref.chunk_id,
                                    refId: ref.ref_id,
                                    content: ref.content,
                                    page: ref.page,
                                    bbox: ref.bbox,
                                    source: 'native' as const,
                                }));
                                updateMessage(assistantMessageId, { references });
                            } else if (data.type === 'content' && data.text) {
                                appendToMessage(assistantMessageId, data.text, data.active_refs);

                                if (data.active_refs && data.active_refs.length > 0) {
                                    const activeChunks = references.filter((r) =>
                                        data.active_refs?.includes(r.refId)
                                    );
                                    setHighlights(activeChunks);
                                }
                            } else if (data.type === 'done') {
                                updateMessage(assistantMessageId, { isStreaming: false });
                            } else if (data.type === 'error') {
                                updateMessage(assistantMessageId, {
                                    content: data.content || '发生错误',
                                    isStreaming: false,
                                });
                            }
                        } catch {
                            // ignore parse errors
                        }
                    }
                }
            } catch (error) {
                updateMessage(assistantMessageId, {
                    content: error instanceof Error ? error.message : '请求失败',
                    isStreaming: false,
                });
            } finally {
                setLoading(false);
            }
        },
        [
            currentDocument,
            config,
            messages,
            addMessage,
            appendToMessage,
            updateMessage,
            setHighlights,
            setLoading,
            API_BASE,
            effectiveMultimodalApiKey,
        ]
    );

    const uploadDocument = useCallback(
        async (file: File, ocrMode: 'manual' | 'full' = 'manual'): Promise<string> => {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('ocr_mode', ocrMode);

            if (config.zhipuApiKey) {
                formData.append('zhipu_api_key', config.zhipuApiKey);
            }
            formData.append('ocr_provider', 'baidu');

            if (config.baiduOcrUrl) {
                formData.append('baidu_ocr_url', config.baiduOcrUrl);
            }
            if (config.baiduOcrToken) {
                formData.append('baidu_ocr_token', config.baiduOcrToken);
            }

            try {
                const response = await fetch(`${API_BASE}/documents/upload`, {
                    method: 'POST',
                    body: formData,
                });

                if (!response.ok) {
                    const message = await readApiErrorMessage(response, '上传失败');
                    throw new Error(message);
                }

                const data = await response.json();
                if (!data?.document_id) {
                    throw new Error('上传接口未返回文档 ID');
                }
                return String(data.document_id);
            } catch (error) {
                console.error('上传错误:', error);
                if (error instanceof TypeError) {
                    throw new Error(`无法连接后端，请确认 ${apiOrigin} 已启动`);
                }
                if (error instanceof Error) {
                    throw error;
                }
                throw new Error('上传失败');
            }
        },
        [config, API_BASE, apiOrigin]
    );

    const getDocument = useCallback(
        async (docId: string) => {
            try {
                const response = await fetch(`${API_BASE}/documents/${docId}`);
                if (!response.ok) {
                    throw new Error('获取文档失败');
                }
                return await response.json();
            } catch (error) {
                console.error('获取文档错误:', error);
                return null;
            }
        },
        [API_BASE]
    );

    const getPdfUrl = useCallback(
        (docId: string): string => {
            return `${API_BASE}/documents/${docId}/pdf`;
        },
        [API_BASE]
    );

    const lookupDocument = useCallback(
        async (sha256: string): Promise<{ exists: boolean; doc_id?: string; status?: string }> => {
            try {
                const response = await fetch(`${API_BASE}/documents/lookup`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ sha256 }),
                });
                if (!response.ok) {
                    throw new Error('查重失败');
                }
                return await response.json();
            } catch (error) {
                console.error('查重错误:', error);
                return { exists: false };
            }
        },
        [API_BASE]
    );

    const listHistory = useCallback(async (): Promise<HistoryDocumentItem[]> => {
        try {
            const response = await fetch(`${API_BASE}/documents/history`);
            if (!response.ok) {
                throw new Error('历史记录加载失败');
            }
            const data = await response.json();
            return Array.isArray(data) ? data : [];
        } catch (error) {
            console.error('历史记录错误:', error);
            return [];
        }
    }, [API_BASE]);

    const deleteDocument = useCallback(
        async (docId: string): Promise<boolean> => {
            try {
                const resp = await fetch(`${API_BASE}/documents/${docId}`, { method: 'DELETE' });
                return resp.ok;
            } catch (e) {
                console.error('删除文档错误:', e);
                return false;
            }
        },
        [API_BASE]
    );

    const attachPdf = useCallback(
        async (docId: string, file: File): Promise<boolean> => {
            try {
                const form = new FormData();
                form.append('file', file);
                const resp = await fetch(`${API_BASE}/documents/${docId}/attach_pdf`, {
                    method: 'POST',
                    body: form,
                });
                return resp.ok;
            } catch (e) {
                console.error('补传 PDF 错误:', e);
                return false;
            }
        },
        [API_BASE]
    );

    const recognizePages = useCallback(
        async (docId: string, pages: number[], apiKey?: string): Promise<RecognizePagesResponse | null> => {
            try {
                const resp = await fetch(`${API_BASE}/documents/${docId}/recognize`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pages, api_key: apiKey }),
                });
                if (!resp.ok) {
                    const text = await resp.text();
                    throw new Error(text || '识别请求失败');
                }
                return await resp.json();
            } catch (e) {
                console.error('识别页面错误:', e);
                return null;
            }
        },
        [API_BASE]
    );

    const cancelOcr = useCallback(
        async (docId: string): Promise<boolean> => {
            try {
                const resp = await fetch(`${API_BASE}/documents/${docId}/ocr/cancel`, {
                    method: 'POST',
                });
                return resp.ok;
            } catch (e) {
                console.error('取消 OCR 错误:', e);
                return false;
            }
        },
        [API_BASE]
    );

    const getChatHistory = useCallback(
        async (docId: string): Promise<ChatMessage[]> => {
            try {
                const resp = await fetch(`${API_BASE}/documents/${docId}/chat_history`);
                if (!resp.ok) {
                    throw new Error('获取对话历史失败');
                }
                const data = await resp.json();
                const raw: unknown[] = Array.isArray(data?.messages) ? data.messages : [];

                return raw.map((entry) => {
                    const m = isRecord(entry) ? entry : {};
                    const ts = m.timestamp ? new Date(toText(m.timestamp)) : new Date();
                    const refs: unknown[] = Array.isArray(m.references) ? m.references : [];
                    const pageReferenceGroups = Array.isArray(m.page_reference_groups)
                        ? (m.page_reference_groups as ChatPageReferenceGroup[])
                        : Array.isArray(m.pageReferenceGroups)
                            ? (m.pageReferenceGroups as ChatPageReferenceGroup[])
                            : [];
                    const mappedRefs: TextChunk[] = refs
                        .map((ref): TextChunk => {
                            const r = isRecord(ref) ? ref : {};
                            const rawBbox = isRecord(r.bbox) ? r.bbox : {};
                            const page = toNumber(r.page || rawBbox.page, 1);
                            const refId = toText(r.ref_id || r.refId);
                            return {
                            id: toText(
                                r.chunk_id || r.chunkId || r.id,
                                `${docId}_${refId || 'ref'}_${page}`
                            ),
                            refId,
                            content: toText(r.content),
                            page,
                            bbox: normalizeBbox(r.bbox, page),
                            source: 'native' as const,
                            };
                        })
                        .filter((r): r is TextChunk => !!r.refId);

                    return {
                        id: toText(m.id, `${toText(m.role, 'assistant')}_${ts.getTime()}`),
                        role: m.role === 'user' ? 'user' : 'assistant',
                        content: toText(m.content),
                        references: mappedRefs,
                        pageReferenceGroups,
                        activeRefs: [],
                        timestamp: ts,
                        isStreaming: false,
                    } as ChatMessage;
                });
            } catch (e) {
                console.error('获取对话历史错误:', e);
                return [];
            }
        },
        [API_BASE]
    );

    const getComplianceHistory = useCallback(
        async (
            docId: string
        ): Promise<{ requirementsText: string; results: ComplianceItem[]; markdown: string; allowedPagesText: string } | null> => {
            try {
                const resp = await fetch(`${API_BASE}/documents/${docId}/compliance_history`);
                if (resp.status === 404) return null;
                if (!resp.ok) throw new Error('获取合规检查历史失败');

                const data = await resp.json();
                const reqs: string[] = Array.isArray(data?.requirements) ? data.requirements : [];
                const resultsRaw: unknown[] = Array.isArray(data?.results) ? data.results : [];
                const markdown: string = typeof data?.markdown === 'string' ? data.markdown : '';
                const allowedPagesRaw: number[] = Array.isArray(data?.allowed_pages) ? data.allowed_pages : [];

                const mapped: ComplianceItem[] = resultsRaw.map((entry, idx: number) => {
                    const item = isRecord(entry) ? entry : {};
                    const refs: unknown[] = Array.isArray(item.references) ? item.references : [];
                    const mappedRefs: TextChunk[] = refs
                        .map((ref) => {
                            const r = isRecord(ref) ? ref : {};
                            const rawBbox = isRecord(r.bbox) ? r.bbox : {};
                            const page = toNumber(r.page_number || r.page || rawBbox.page, 1);
                            const refId = toText(r.ref_id || r.refId);
                            return {
                                id: toText(
                                    r.id || r.chunk_id || r.chunkId,
                                    `${docId}_${refId || 'ref'}_${page}`
                                ),
                                refId,
                                content: toText(r.content),
                                page,
                                bbox: normalizeBbox(r.bbox, page),
                                source: (r.source_type === 'ocr' ? 'ocr' : 'native') as 'native' | 'ocr',
                            } as TextChunk;
                        })
                        .filter((r): r is TextChunk => !!r.refId);

                    return {
                        id: toNumber(item.id, idx + 1),
                        requirement: toText(item.requirement),
                        status: ['satisfied', 'unsatisfied', 'partial', 'unknown', 'error'].includes(toText(item.status))
                            ? (toText(item.status) as ComplianceItem['status'])
                            : 'unknown',
                        response: toText(item.response),
                        references: mappedRefs,
                    } as ComplianceItem;
                });
                return {
                    requirementsText: reqs.join('\n'),
                    results: mapped,
                    markdown,
                    allowedPagesText: allowedPagesRaw.length > 0 ? allowedPagesRaw.join(',') : '',
                };
            } catch (e) {
                console.error('获取合规检查历史错误:', e);
                return null;
            }
        },
        [API_BASE]
    );

    const getAuditProfiles = useCallback(async (): Promise<AuditProfile[]> => {
        try {
            const resp = await fetch(`${apiOrigin}/api/audit_profiles`);
            if (!resp.ok) {
                throw new Error(await readApiErrorMessage(resp, '加载审核模板失败'));
            }
            const data = await resp.json();
            const profilesRaw: unknown[] = Array.isArray(data) ? data : [];
            return profilesRaw
                .map((profile) => mapAuditProfile(profile))
                .filter((profile: AuditProfile | null): profile is AuditProfile => Boolean(profile));
        } catch (error) {
            console.error('加载审核模板错误:', error);
            throw error instanceof Error ? error : new Error('加载审核模板失败');
        }
    }, [apiOrigin]);

    const createAuditProfile = useCallback(
        async (payload: AuditProfilePayload): Promise<AuditProfile> => {
            const resp = await fetch(`${apiOrigin}/api/audit_profiles`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!resp.ok) {
                throw new Error(await readApiErrorMessage(resp, '创建审核模板失败'));
            }
            const data = await resp.json();
            const mapped = mapAuditProfile(data);
            if (!mapped) {
                throw new Error('审核模板返回数据无效');
            }
            return mapped;
        },
        [apiOrigin]
    );

    const updateAuditProfile = useCallback(
        async (profileId: string, payload: AuditProfilePayload): Promise<AuditProfile> => {
            const resp = await fetch(`${apiOrigin}/api/audit_profiles/${encodeURIComponent(profileId)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!resp.ok) {
                throw new Error(await readApiErrorMessage(resp, '保存审核模板失败'));
            }
            const data = await resp.json();
            const mapped = mapAuditProfile(data);
            if (!mapped) {
                throw new Error('审核模板返回数据无效');
            }
            return mapped;
        },
        [apiOrigin]
    );

    const deleteAuditProfile = useCallback(
        async (profileId: string): Promise<void> => {
            const resp = await fetch(`${apiOrigin}/api/audit_profiles/${encodeURIComponent(profileId)}`, {
                method: 'DELETE',
            });
            if (!resp.ok) {
                throw new Error(await readApiErrorMessage(resp, '删除审核模板失败'));
            }
        },
        [apiOrigin]
    );

    const createMultimodalAuditJob = useCallback(
        async (docId: string, payload: MultimodalAuditJobCreateRequest): Promise<MultimodalAuditJobCreateResponse | null> => {
            try {
                const resp = await fetch(`${API_BASE}/documents/${docId}/multimodal_audit/jobs`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (!resp.ok) {
                    throw new Error(await readApiErrorMessage(resp, '创建专项审查任务失败'));
                }
                return await resp.json();
            } catch (e) {
                console.error('创建专项审查任务错误:', e);
                return null;
            }
        },
        [API_BASE]
    );

    const watchMultimodalAuditProgress = useCallback(
        (docId: string, jobId: string, onProgress: (progress: MultimodalAuditProgressEvent) => void): (() => void) => {
            const eventSource = new EventSource(`${API_BASE}/documents/${docId}/multimodal_audit/jobs/${jobId}/progress`);

            eventSource.addEventListener('progress', (event) => {
                try {
                    const data = JSON.parse((event as MessageEvent).data) as MultimodalAuditProgressEvent;
                    onProgress(data);
                    if (data.stage === 'completed' || data.stage === 'failed') {
                        eventSource.close();
                    }
                } catch {
                    // ignore malformed event
                }
            });

            eventSource.onerror = () => {
                eventSource.close();
                // SSE 断开时，通过 HTTP 轮询回退获取结果
                const pollResult = (retries: number) => {
                    fetch(`${API_BASE}/documents/${docId}/multimodal_audit/jobs/${jobId}`)
                        .then(async (resp) => {
                            if (!resp.ok) return;
                            const data = await resp.json();
                            if (data?.status === 'completed') {
                                onProgress({
                                    status: 'completed',
                                    stage: 'completed',
                                    current: 100,
                                    total: 100,
                                    message: 'Audit completed.',
                                } as MultimodalAuditProgressEvent);
                            } else if (data?.status === 'failed') {
                                onProgress({
                                    status: 'failed',
                                    stage: 'failed',
                                    current: 100,
                                    total: 100,
                                    message: data?.error || 'Audit failed.',
                                } as MultimodalAuditProgressEvent);
                            } else if (retries > 0) {
                                // 后端仍在处理中，2 秒后重试
                                setTimeout(() => pollResult(retries - 1), 2000);
                            }
                        })
                        .catch(() => {});
                };
                pollResult(5); // 最多重试 5 次，覆盖 ~10 秒
            };

            return () => eventSource.close();
        },
        [API_BASE]
    );

    const getMultimodalAuditJobResult = useCallback(
        async (docId: string, jobId: string): Promise<MultimodalAuditJobResult | null> => {
            try {
                const resp = await fetch(`${API_BASE}/documents/${docId}/multimodal_audit/jobs/${jobId}`);
                if (!resp.ok) throw new Error('获取专项审查结果失败');
                const data = await resp.json();
                const itemsRaw: unknown[] = Array.isArray(data?.items) ? data.items : [];
                const mappedItems = itemsRaw.map((item, idx: number) => mapAuditItem(docId, item, idx));
                const summary = mapAuditSummary(data?.summary || {}, mappedItems.length);
                const auditProfileSnapshot = mapAuditProfile(data?.audit_profile_snapshot);
                const legacyAuditType = mapLegacyAuditType(data?.audit_type);

                return {
                    jobId: String(data?.job_id || jobId),
                    status: ['queued', 'running', 'completed', 'failed'].includes(String(data?.status))
                        ? (data.status as MultimodalAuditJobResult['status'])
                        : 'running',
                    generatedAt: String(data?.generated_at || ''),
                    auditProfileId: String(data?.audit_profile_id || auditProfileSnapshot?.id || legacyAuditType || ''),
                    auditProfileName: String(data?.audit_profile_name || auditProfileSnapshot?.name || ''),
                    auditProfileSnapshot,
                    legacyAuditType,
                    items: mappedItems,
                    summary,
                };
            } catch (e) {
                console.error('获取专项审查结果错误:', e);
                return null;
            }
        },
        [API_BASE]
    );

    const getMultimodalAuditHistory = useCallback(
        async (docId: string): Promise<MultimodalAuditJobResult | null> => {
            try {
                const resp = await fetch(`${API_BASE}/documents/${docId}/multimodal_audit/history`);
                if (!resp.ok) {
                    if (resp.status === 404) return null;
                    throw new Error('获取专项审查历史失败');
                }
                const data = await resp.json();
                const jobs = Array.isArray(data?.jobs) ? data.jobs : [];
                if (jobs.length === 0) return null;
                const latest = jobs[0];
                const latestRecord = isRecord(latest) ? latest : {};
                const itemsRaw: unknown[] = Array.isArray(latestRecord.items) ? latestRecord.items : [];
                const mappedItems = itemsRaw.map((item, idx: number) => mapAuditItem(docId, item, idx));
                const summary = mapAuditSummary(latestRecord.summary || {}, mappedItems.length);
                const auditProfileSnapshot = mapAuditProfile(latestRecord.audit_profile_snapshot);
                const legacyAuditType = mapLegacyAuditType(latestRecord.audit_type);
                return {
                    jobId: toText(latestRecord.job_id),
                    status: ['queued', 'running', 'completed', 'failed'].includes(toText(latestRecord.status))
                        ? (toText(latestRecord.status) as MultimodalAuditJobResult['status'])
                        : 'completed',
                    generatedAt: toText(latestRecord.generated_at || latestRecord.finished_at),
                    auditProfileId: toText(latestRecord.audit_profile_id || auditProfileSnapshot?.id || legacyAuditType),
                    auditProfileName: toText(latestRecord.audit_profile_name || auditProfileSnapshot?.name),
                    auditProfileSnapshot,
                    legacyAuditType,
                    items: mappedItems,
                    summary,
                };
            } catch (e) {
                console.error('获取专项审查历史错误:', e);
                return null;
            }
        },
        [API_BASE]
    );

    const watchProgress = useCallback(
        (docId: string, onProgress: (progress: DocumentProgressEvent) => void): (() => void) => {
            const eventSource = new EventSource(`${API_BASE}/documents/${docId}/progress`);

            eventSource.addEventListener('progress', (event) => {
                try {
                    const data = JSON.parse((event as MessageEvent).data);
                    onProgress(data);

                    if (data.stage === 'completed' || data.stage === 'failed') {
                        eventSource.close();
                    }
                } catch {
                    // ignore
                }
            });

            eventSource.onerror = () => {
                eventSource.close();
            };

            return () => eventSource.close();
        },
        [API_BASE]
    );

    return {
        askQuestion,
        uploadDocument,
        getDocument,
        getPdfUrl,
        lookupDocument,
        listHistory,
        deleteDocument,
        attachPdf,
        recognizePages,
        cancelOcr,
        getChatHistory,
        getComplianceHistory,
        getAuditProfiles,
        createAuditProfile,
        updateAuditProfile,
        deleteAuditProfile,
        createMultimodalAuditJob,
        watchMultimodalAuditProgress,
        getMultimodalAuditJobResult,
        getMultimodalAuditHistory,
        watchProgress,
    };
}
