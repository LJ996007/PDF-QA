import { useCallback } from 'react';
import { useDocumentStore } from '../stores/documentStore';
import type {
    ChatMessage,
    ComplianceItem,
    ComplianceV2Result,
    EvidenceItem,
    ReviewState,
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
}

export interface ComplianceV2Payload {
    requirements: string[];
    policy_set_id?: string;
    allowed_pages?: number[];
    api_key?: string;
    review_required?: boolean;
}

interface RawComplianceV2Response {
    decision?: string;
    confidence?: number;
    risk_level?: string;
    summary?: string;
    field_results?: any[];
    rule_results?: any[];
    evidence?: any[];
    review_state?: any;
    requirements?: string[];
    allowed_pages?: number[];
    policy_set_id?: string;
    markdown?: string;
    created_at?: string;
}

const mapEvidenceItem = (item: any): EvidenceItem => {
    const page = Number(item?.page || item?.bbox?.page || 1);
    return {
        refId: String(item?.ref_id || item?.refId || ''),
        page,
        bbox: item?.bbox || { page, x: 0, y: 0, w: 100, h: 20 },
        sourceType: (item?.source_type || item?.sourceType || 'native') as EvidenceItem['sourceType'],
        fieldName: String(item?.field_name || item?.fieldName || ''),
        supportLevel: (item?.support_level || item?.supportLevel || 'primary') as EvidenceItem['supportLevel'],
        content: String(item?.content || ''),
    };
};

const mapReviewState = (raw: any): ReviewState => ({
    state: (raw?.state || 'pending_review') as ReviewState['state'],
    reviewer: raw?.reviewer ? String(raw.reviewer) : undefined,
    note: raw?.note ? String(raw.note) : undefined,
    updatedAt: raw?.updated_at ? String(raw.updated_at) : raw?.updatedAt ? String(raw.updatedAt) : undefined,
});

const mapComplianceV2Result = (data: RawComplianceV2Response): ComplianceV2Result => {
    const evidence = Array.isArray(data?.evidence) ? data.evidence.map(mapEvidenceItem) : [];
    return {
        decision: (data?.decision || 'needs_review') as ComplianceV2Result['decision'],
        confidence: Number(data?.confidence || 0),
        riskLevel: (data?.risk_level || 'high') as ComplianceV2Result['riskLevel'],
        summary: String(data?.summary || ''),
        fieldResults: Array.isArray(data?.field_results)
            ? data.field_results.map((item) => ({
                  fieldKey: String(item?.field_key || ''),
                  fieldName: String(item?.field_name || ''),
                  requirement: String(item?.requirement || ''),
                  value: String(item?.value || ''),
                  confidence: Number(item?.confidence || 0),
                  status: (item?.status || 'uncertain') as ComplianceV2Result['fieldResults'][number]['status'],
                  evidenceRefs: Array.isArray(item?.evidence_refs) ? item.evidence_refs.map((x: any) => String(x)) : [],
              }))
            : [],
        ruleResults: Array.isArray(data?.rule_results)
            ? data.rule_results.map((item) => ({
                  ruleId: String(item?.rule_id || ''),
                  ruleName: String(item?.rule_name || ''),
                  status: (item?.status || 'warn') as ComplianceV2Result['ruleResults'][number]['status'],
                  message: String(item?.message || ''),
                  fieldNames: Array.isArray(item?.field_names) ? item.field_names.map((x: any) => String(x)) : [],
              }))
            : [],
        evidence,
        reviewState: mapReviewState(data?.review_state || {}),
        requirements: Array.isArray(data?.requirements) ? data.requirements.map((x) => String(x)) : [],
        allowedPages: Array.isArray(data?.allowed_pages) ? data.allowed_pages.map((x) => Number(x)) : [],
        policySetId: String(data?.policy_set_id || 'contracts/base_rules'),
        markdown: String(data?.markdown || ''),
        createdAt: data?.created_at ? String(data.created_at) : undefined,
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
                throw new Error('没有打开的文档');
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
                    throw new Error('无法读取响应');
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
                    throw new Error('lookup failed');
                }
                return await response.json();
            } catch (error) {
                console.error('lookup错误:', error);
                return { exists: false };
            }
        },
        [API_BASE]
    );

    const listHistory = useCallback(async (): Promise<HistoryDocumentItem[]> => {
        try {
            const response = await fetch(`${API_BASE}/documents/history`);
            if (!response.ok) {
                throw new Error('history failed');
            }
            const data = await response.json();
            return Array.isArray(data) ? data : [];
        } catch (error) {
            console.error('history错误:', error);
            return [];
        }
    }, [API_BASE]);

    const deleteDocument = useCallback(
        async (docId: string): Promise<boolean> => {
            try {
                const resp = await fetch(`${API_BASE}/documents/${docId}`, { method: 'DELETE' });
                return resp.ok;
            } catch (e) {
                console.error('deleteDocument错误:', e);
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
                console.error('attachPdf错误:', e);
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
                console.error('recognizePages错误:', e);
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
                console.error('cancelOcr错误:', e);
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
                    throw new Error('获取聊天历史失败');
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
                console.error('getChatHistory错误:', e);
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
                console.error('getComplianceHistory错误:', e);
                return null;
            }
        },
        [API_BASE]
    );

    const checkComplianceV2 = useCallback(
        async (docId: string, payload: ComplianceV2Payload): Promise<ComplianceV2Result | null> => {
            try {
                const resp = await fetch(`${API_BASE}/documents/${docId}/compliance/v2`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        requirements: payload.requirements || [],
                        policy_set_id: payload.policy_set_id || 'contracts/base_rules',
                        allowed_pages: payload.allowed_pages || [],
                        api_key: payload.api_key,
                        review_required: payload.review_required !== false,
                    }),
                });
                if (!resp.ok) {
                    const txt = await resp.text();
                    throw new Error(txt || 'compliance v2 failed');
                }
                const data = (await resp.json()) as RawComplianceV2Response;
                return mapComplianceV2Result(data);
            } catch (e) {
                console.error('checkComplianceV2错误:', e);
                return null;
            }
        },
        [API_BASE]
    );

    const getComplianceV2History = useCallback(
        async (docId: string): Promise<ComplianceV2Result | null> => {
            try {
                const resp = await fetch(`${API_BASE}/documents/${docId}/compliance_v2_history`);
                if (resp.status === 404) return null;
                if (!resp.ok) throw new Error('compliance_v2_history failed');
                const data = (await resp.json()) as RawComplianceV2Response;
                return mapComplianceV2Result(data);
            } catch (e) {
                console.error('getComplianceV2History错误:', e);
                return null;
            }
        },
        [API_BASE]
    );

    const getEvidence = useCallback(
        async (docId: string): Promise<EvidenceItem[]> => {
            try {
                const resp = await fetch(`${API_BASE}/documents/${docId}/evidence`);
                if (resp.status === 404) return [];
                if (!resp.ok) throw new Error('evidence fetch failed');
                const data = await resp.json();
                const evidenceRaw = Array.isArray(data?.evidence) ? data.evidence : [];
                return evidenceRaw.map(mapEvidenceItem);
            } catch (e) {
                console.error('getEvidence错误:', e);
                return [];
            }
        },
        [API_BASE]
    );

    const submitReviewDecision = useCallback(
        async (
            docId: string,
            decision: 'approved' | 'rejected' | 'pending_review',
            reviewer?: string,
            note?: string
        ): Promise<ReviewState | null> => {
            try {
                const resp = await fetch(`${API_BASE}/documents/${docId}/review/submit`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ decision, reviewer, note }),
                });
                if (!resp.ok) throw new Error('submit review failed');
                const data = await resp.json();
                return mapReviewState(data?.review_state || {});
            } catch (e) {
                console.error('submitReviewDecision错误:', e);
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
        checkComplianceV2,
        getComplianceV2History,
        getEvidence,
        submitReviewDecision,
        watchProgress,
    };
}
