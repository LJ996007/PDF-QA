import { useCallback } from 'react';
import { useDocumentStore } from '../stores/documentStore';
import type { PageOcrStatus, TextChunk } from '../stores/documentStore';

const API_BASE = 'http://localhost:8000/api';

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

export interface OCRPageResult {
    page: number;
    ok: boolean;
    alreadyRecognized?: boolean;
    error?: string;
}

interface ProgressPayload {
    stage: string;
    current: number;
    total: number;
    message?: string;
    document_id?: string;
}

const toConsistentRecognizedPages = (
    recognizedPages: number[] | undefined,
    pageOcrStatus: Record<number, PageOcrStatus> | Record<string, PageOcrStatus> | undefined,
    totalPages: number
): number[] => {
    const pages = new Set<number>((recognizedPages || []).map((p) => Number(p)).filter((p) => Number.isFinite(p)));

    Object.entries(pageOcrStatus || {}).forEach(([key, status]) => {
        if (status !== 'recognized') {
            return;
        }
        const page = Number(key);
        if (!Number.isNaN(page)) {
            pages.add(page);
        }
    });

    return [...pages]
        .filter((page) => page >= 1 && page <= totalPages)
        .sort((a, b) => a - b);
};

const toConsistentRequiredPages = (totalPages: number, recognizedPages: number[]): number[] => {
    const recognizedSet = new Set(recognizedPages);
    const required: number[] = [];
    for (let page = 1; page <= totalPages; page += 1) {
        if (!recognizedSet.has(page)) {
            required.push(page);
        }
    }
    return required;
};

export function useVectorSearch() {
    const {
        currentDocument,
        config,
        setHighlights,
        addMessage,
        appendToMessage,
        updateMessage,
        setLoading,
        setPageOcrStatus,
        setOcrQueueProgress,
        updateCurrentDocument,
        clearSelectedPages,
    } = useDocumentStore();

    const askQuestion = useCallback(async (question: string): Promise<void> => {
        if (!currentDocument) {
            throw new Error('No document is open');
        }

        const allowedPages = toConsistentRecognizedPages(
            currentDocument.recognizedPages,
            currentDocument.pageOcrStatus,
            currentDocument.totalPages
        );

        if (allowedPages.length === 0) {
            throw new Error('请先识别页面后再提问');
        }

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
                    history: [],
                    zhipu_api_key: config.zhipuApiKey,
                    deepseek_api_key: config.deepseekApiKey,
                    allowed_pages: allowedPages,
                }),
            });

            if (!response.ok) {
                throw new Error('Request failed');
            }

            const reader = response.body?.getReader();
            if (!reader) {
                throw new Error('Unable to read response stream');
            }

            const decoder = new TextDecoder();
            let buffer = '';
            let references: TextChunk[] = [];

            while (true) {
                const { done, value } = await reader.read();
                if (done) {
                    break;
                }

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (!line.startsWith('data: ')) {
                        continue;
                    }

                    try {
                        const data: ChatStreamEvent = JSON.parse(line.slice(6));

                        if (data.type === 'references' && data.refs) {
                            references = data.refs.map((ref) => ({
                                id: ref.chunk_id,
                                refId: ref.ref_id,
                                content: ref.content,
                                page: ref.page,
                                bbox: ref.bbox,
                                source: 'ocr' as const,
                            }));
                            updateMessage(assistantMessageId, { references });
                        } else if (data.type === 'content' && data.text) {
                            appendToMessage(assistantMessageId, data.text, data.active_refs);

                            if (data.active_refs && data.active_refs.length > 0) {
                                const activeChunks = references.filter((r) => data.active_refs?.includes(r.refId));
                                setHighlights(activeChunks);
                            }
                        } else if (data.type === 'done') {
                            updateMessage(assistantMessageId, { isStreaming: false });
                        } else if (data.type === 'error') {
                            updateMessage(assistantMessageId, {
                                content: data.content || 'Error',
                                isStreaming: false,
                            });
                        }
                    } catch {
                        // Ignore SSE parse errors.
                    }
                }
            }
        } catch (error) {
            updateMessage(assistantMessageId, {
                content: error instanceof Error ? error.message : 'Request failed',
                isStreaming: false,
            });
        } finally {
            setLoading(false);
        }
    }, [
        currentDocument,
        config,
        addMessage,
        appendToMessage,
        updateMessage,
        setHighlights,
        setLoading,
    ]);

    const uploadDocument = useCallback(async (file: File, ocrMode: 'manual' | 'full' = 'manual'): Promise<string | null> => {
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
                throw new Error('Upload failed');
            }

            const data = await response.json();
            return data.document_id;
        } catch (error) {
            console.error('Upload error:', error);
            return null;
        }
    }, [config]);

    const getDocument = useCallback(async (docId: string) => {
        try {
            const response = await fetch(`${API_BASE}/documents/${docId}`);
            if (!response.ok) {
                throw new Error('Failed to fetch document');
            }
            return await response.json();
        } catch (error) {
            console.error('Get document error:', error);
            return null;
        }
    }, []);

    const watchProgress = useCallback((docId: string, onProgress: (progress: ProgressPayload) => void): (() => void) => {
        const eventSource = new EventSource(`${API_BASE}/documents/${docId}/progress`);

        eventSource.addEventListener('progress', (event) => {
            try {
                const data = JSON.parse(event.data) as ProgressPayload;
                onProgress(data);

                if (data.stage === 'completed' || data.stage === 'failed') {
                    eventSource.close();
                }
            } catch {
                // Ignore event parse errors.
            }
        });

        eventSource.onerror = () => {
            eventSource.close();
        };

        return () => eventSource.close();
    }, []);

    const ocrPage = useCallback(async (pageNumber: number): Promise<OCRPageResult> => {
        if (!currentDocument) {
            return { page: pageNumber, ok: false, error: 'No open document' };
        }

        setPageOcrStatus(pageNumber, 'processing');

        try {
            const response = await fetch(`${API_BASE}/documents/${currentDocument.id}/pages/${pageNumber}/ocr`, {
                method: 'POST',
            });

            const data: Record<string, unknown> = await response.json().catch(() => ({} as Record<string, unknown>));

            if (!response.ok) {
                const detail = typeof data.detail === 'string' ? data.detail : 'OCR failed';
                setPageOcrStatus(pageNumber, 'failed');
                return {
                    page: pageNumber,
                    ok: false,
                    error: detail,
                };
            }

            const alreadyRecognized = Boolean(data.already_recognized);
            setPageOcrStatus(pageNumber, 'recognized');

            return {
                page: pageNumber,
                ok: true,
                alreadyRecognized,
            };
        } catch (error) {
            setPageOcrStatus(pageNumber, 'failed');
            return {
                page: pageNumber,
                ok: false,
                error: error instanceof Error ? error.message : 'OCR failed',
            };
        }
    }, [currentDocument, setPageOcrStatus]);

    const ocrPagesBatch = useCallback(async (pageNumbers: number[]) => {
        if (!currentDocument) {
            return { total: 0, completed: 0, failed: 0, results: [] as OCRPageResult[] };
        }

        const pages = [...new Set(pageNumbers)].sort((a, b) => a - b);
        if (pages.length === 0) {
            return { total: 0, completed: 0, failed: 0, results: [] as OCRPageResult[] };
        }

        const queue = [...pages];
        const results: OCRPageResult[] = [];
        let completed = 0;
        let failed = 0;

        const updateProgress = () => {
            setOcrQueueProgress({
                total: pages.length,
                completed,
                failed,
                isRunning: true,
                message: `识别中 ${completed + failed}/${pages.length}`,
            });
        };

        setOcrQueueProgress({
            total: pages.length,
            completed: 0,
            failed: 0,
            isRunning: true,
            message: `识别中 0/${pages.length}`,
        });

        const worker = async () => {
            while (queue.length > 0) {
                const page = queue.shift();
                if (!page) {
                    break;
                }

                const result = await ocrPage(page);
                results.push(result);
                if (result.ok) {
                    completed += 1;
                } else {
                    failed += 1;
                }
                updateProgress();
            }
        };

        const workers = Array.from({ length: Math.min(3, pages.length) }, () => worker());
        await Promise.all(workers);

        const refreshed = await getDocument(currentDocument.id);
        if (refreshed) {
            const parsedStatus: Record<number, 'unrecognized' | 'processing' | 'recognized' | 'failed'> = {};
            Object.entries(refreshed.page_ocr_status || {}).forEach(([key, value]) => {
                const page = Number(key);
                if (!Number.isNaN(page)) {
                    parsedStatus[page] = value as 'unrecognized' | 'processing' | 'recognized' | 'failed';
                }
            });
            const totalPages = Number(refreshed.total_pages || currentDocument.totalPages || 0);
            const consistentRecognized = toConsistentRecognizedPages(
                refreshed.recognized_pages || [],
                parsedStatus,
                totalPages
            );
            const consistentRequired = toConsistentRequiredPages(totalPages, consistentRecognized);

            updateCurrentDocument({
                totalPages,
                ocrRequiredPages: consistentRequired,
                recognizedPages: consistentRecognized,
                pageOcrStatus: parsedStatus,
                ocrMode: refreshed.ocr_mode || 'manual',
            });
        }

        clearSelectedPages();
        setOcrQueueProgress({
            total: pages.length,
            completed,
            failed,
            isRunning: false,
            message: failed > 0 ? `完成：成功 ${completed}，失败 ${failed}` : `完成：共识别 ${completed} 页`,
        });

        return {
            total: pages.length,
            completed,
            failed,
            results,
        };
    }, [
        currentDocument,
        clearSelectedPages,
        getDocument,
        ocrPage,
        setOcrQueueProgress,
        updateCurrentDocument,
    ]);

    return {
        askQuestion,
        uploadDocument,
        getDocument,
        watchProgress,
        ocrPage,
        ocrPagesBatch,
    };
}
