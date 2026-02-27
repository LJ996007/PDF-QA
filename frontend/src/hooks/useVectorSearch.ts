import { useCallback } from 'react';
import { useDocumentStore } from '../stores/documentStore';
import type {
    AuditType,
    ChatMessage,
    ComplianceItem,
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
    audit_type: AuditType;
    bidder_name: string;
    allowed_pages: number[];
    custom_checks: string[];
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
    auditType: AuditType;
    items: MultimodalAuditItem[];
    summary: MultimodalAuditSummary;
}

const mapAuditReferenceToChunk = (docId: string, ref: any, index: number): TextChunk => {
    const page = Number(ref?.page || ref?.bbox?.page || 1);
    const refId = String(ref?.ref_id || ref?.refId || `ref-${index + 1}`);
    const bbox = ref?.bbox || { page, x: 0, y: 0, w: 100, h: 20 };
    return {
        id: `${docId}_${refId}_${index + 1}`,
        refId,
        content: String(ref?.evidence_text || ref?.content || ''),
        page,
        bbox,
        source: ref?.source === 'ocr' ? 'ocr' : 'native',
    };
};

const mapAuditItem = (docId: string, item: any, idx: number): MultimodalAuditItem => {
    const refsRaw = Array.isArray(item?.references) ? item.references : [];
    return {
        checkKey: String(item?.check_key || item?.checkKey || `check_${idx + 1}`),
        checkTitle: String(item?.check_title || item?.checkTitle || `检查项 ${idx + 1}`),
        status: ['pass', 'fail', 'needs_review', 'error'].includes(String(item?.status))
            ? (item.status as MultimodalAuditItem['status'])
            : 'needs_review',
        reason: String(item?.reason || ''),
        confidence: Number(item?.confidence || 0),
        references: refsRaw.map((ref: any, refIdx: number) => mapAuditReferenceToChunk(docId, ref, refIdx)),
    };
};

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

    const askQuestion = useCallback(
        async (question: string, opts?: { useContext?: boolean }): Promise<void> => {
            if (!currentDocument) {
                throw new Error('未打开任何文档');
            }

            const useContext = opts?.useContext !== false;
            const historyPayload = useContext
                ? messages
                      .filter((m) => !m.isStreaming)
                      .slice(-20)
                      .map((m) => ({ role: m.role, content: m.content }))
                : [];

            const userMessageId = `user_${Date.now()}`;
            addMessage({
                id: userMessageId,
                role: 'user',
                content: question,
                references: [],
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
        ]
    );

    const uploadDocument = useCallback(
        async (file: File, ocrMode: 'manual' | 'full' = 'manual'): Promise<string | null> => {
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
                    throw new Error('上传失败');
                }

                const data = await response.json();
                return data.document_id;
            } catch (error) {
                console.error('上传错误:', error);
                return null;
            }
        },
        [config, API_BASE]
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
        async (docId: string, pages: number[], apiKey?: string): Promise<any | null> => {
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
                const raw = Array.isArray(data?.messages) ? data.messages : [];

                return raw.map((m: any) => {
                    const ts = m.timestamp ? new Date(m.timestamp) : new Date();
                    const refs = Array.isArray(m.references) ? m.references : [];
                    const mappedRefs: TextChunk[] = refs
                        .map((r: any) => ({
                            id:
                                r.chunk_id ||
                                r.chunkId ||
                                r.id ||
                                `${docId}_${r.ref_id || r.refId || 'ref'}_${r.page || 0}`,
                            refId: r.ref_id || r.refId,
                            content: r.content || '',
                            page: r.page || 1,
                            bbox: r.bbox,
                            source: 'native' as const,
                        }))
                        .filter((r: any) => !!r.refId);

                    return {
                        id: m.id || `${m.role}_${ts.getTime()}`,
                        role: m.role,
                        content: m.content || '',
                        references: mappedRefs,
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
        ): Promise<{ requirementsText: string; results: ComplianceItem[]; markdown: string } | null> => {
            try {
                const resp = await fetch(`${API_BASE}/documents/${docId}/compliance_history`);
                if (resp.status === 404) return null;
                if (!resp.ok) throw new Error('获取合规检查历史失败');

                const data = await resp.json();
                const reqs: string[] = Array.isArray(data?.requirements) ? data.requirements : [];
                const resultsRaw: any[] = Array.isArray(data?.results) ? data.results : [];
                const markdown: string = typeof data?.markdown === 'string' ? data.markdown : '';

                const mapped: ComplianceItem[] = resultsRaw.map((item: any, idx: number) => {
                    const refs = Array.isArray(item?.references) ? item.references : [];
                    const mappedRefs: TextChunk[] = refs
                        .map((r: any) => {
                            const page = r?.page_number || r?.page || r?.bbox?.page || 1;
                            const refId = r?.ref_id || r?.refId;
                            const bbox = r?.bbox || { page, x: 0, y: 0, w: 100, h: 20 };
                            return {
                                id:
                                    r?.id ||
                                    r?.chunk_id ||
                                    r?.chunkId ||
                                    `${docId}_${refId || 'ref'}_${page}`,
                                refId,
                                content: r?.content || '',
                                page,
                                bbox,
                                source: (r?.source_type === 'ocr' ? 'ocr' : 'native') as 'native' | 'ocr',
                            } as TextChunk;
                        })
                        .filter((r: any) => !!r.refId);

                    return {
                        id: item?.id || idx + 1,
                        requirement: item?.requirement || '',
                        status: item?.status || 'unknown',
                        response: item?.response || '',
                        references: mappedRefs,
                    } as ComplianceItem;
                });

                return { requirementsText: reqs.join('\n'), results: mapped, markdown };
            } catch (e) {
                console.error('获取合规检查历史错误:', e);
                return null;
            }
        },
        [API_BASE]
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
                    const text = await resp.text();
                    throw new Error(text || '创建专项审查任务失败');
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
                const itemsRaw = Array.isArray(data?.items) ? data.items : [];
                const mappedItems = itemsRaw.map((item: any, idx: number) => mapAuditItem(docId, item, idx));

                const summaryRaw = data?.summary || {};
                const summary: MultimodalAuditSummary = {
                    pass: Number(summaryRaw.pass || 0),
                    fail: Number(summaryRaw.fail || 0),
                    needs_review: Number(summaryRaw.needs_review || 0),
                    error: Number(summaryRaw.error || 0),
                    total: Number(summaryRaw.total || mappedItems.length),
                };

                return {
                    jobId: String(data?.job_id || jobId),
                    status: ['queued', 'running', 'completed', 'failed'].includes(String(data?.status))
                        ? (data.status as MultimodalAuditJobResult['status'])
                        : 'running',
                    generatedAt: String(data?.generated_at || ''),
                    auditType: (data?.audit_type || 'contract') as AuditType,
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
                const itemsRaw = Array.isArray(latest?.items) ? latest.items : [];
                const mappedItems = itemsRaw.map((item: any, idx: number) => mapAuditItem(docId, item, idx));
                const summaryRaw = latest?.summary || {};
                const summary: MultimodalAuditSummary = {
                    pass: Number(summaryRaw.pass || 0),
                    fail: Number(summaryRaw.fail || 0),
                    needs_review: Number(summaryRaw.needs_review || 0),
                    error: Number(summaryRaw.error || 0),
                    total: Number(summaryRaw.total || mappedItems.length),
                };
                return {
                    jobId: String(latest?.job_id || ''),
                    status: ['queued', 'running', 'completed', 'failed'].includes(String(latest?.status))
                        ? (latest.status as MultimodalAuditJobResult['status'])
                        : 'completed',
                    generatedAt: String(latest?.generated_at || latest?.finished_at || ''),
                    auditType: (latest?.audit_type || 'contract') as AuditType,
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
        (docId: string, onProgress: (progress: any) => void): (() => void) => {
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
        createMultimodalAuditJob,
        watchMultimodalAuditProgress,
        getMultimodalAuditJobResult,
        getMultimodalAuditHistory,
        watchProgress,
    };
}
