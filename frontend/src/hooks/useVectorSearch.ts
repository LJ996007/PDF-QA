import { useCallback } from 'react';
import { useDocumentStore } from '../stores/documentStore';
import type { ChatMessage, ComplianceItem, TextChunk } from '../stores/documentStore';

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
                        question: question,
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

    const watchProgress = useCallback(
        (docId: string, onProgress: (progress: any) => void): () => void => {
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
        getChatHistory,
        getComplianceHistory,
        watchProgress,
    };
}
