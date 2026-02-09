import { useCallback } from 'react';
import { useDocumentStore } from '../stores/documentStore';
import type { TextChunk } from '../stores/documentStore';

const API_BASE = 'http://localhost:8000/api';

export interface ChatStreamEvent {
    type: 'thinking' | 'references' | 'content' | 'done' | 'error';
    content?: string;
    text?: string;
    refs?: Array<{
        ref_id: string;
        chunk_id: string;
        page: number;
        source?: 'native' | 'ocr' | 'vision';
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

export function useVectorSearch() {
    const { currentDocument, config, setHighlights, addMessage, appendToMessage, updateMessage, setLoading } = useDocumentStore();

    /**
     * 发送问题并处理流式响应
     */
    const askQuestion = useCallback(async (question: string): Promise<void> => {
        if (!currentDocument) {
            throw new Error('没有打开的文档');
        }

        // 添加用户消息
        const userMessageId = `user_${Date.now()}`;
        addMessage({
            id: userMessageId,
            role: 'user',
            content: question,
            references: [],
            activeRefs: [],
            timestamp: new Date(),
        });

        // 添加助手消息占位
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
                    history: [],
                    zhipu_api_key: config.zhipuApiKey,
                    deepseek_api_key: config.deepseekApiKey,
                    vision_enabled: config.visionEnabled,
                    vision_base_url: config.visionBaseUrl,
                    vision_api_key: config.visionApiKey,
                    vision_model: config.visionModel,
                    vision_max_pages: config.visionMaxPages,
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

                // 解析SSE事件
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            const data: ChatStreamEvent = JSON.parse(line.slice(6));

                            if (data.type === 'references' && data.refs) {
                                // 保存引用信息
                                references = data.refs.map((ref) => ({
                                    id: ref.chunk_id,
                                    refId: ref.ref_id,
                                    content: ref.content,
                                    page: ref.page,
                                    bbox: ref.bbox,
                                    source: (ref.source ?? 'native') as TextChunk['source'],
                                }));

                                updateMessage(assistantMessageId, { references });
                            } else if (data.type === 'content' && data.text) {
                                // 追加文本
                                appendToMessage(assistantMessageId, data.text, data.active_refs);

                                // 高亮当前引用
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
                            // 忽略解析错误
                        }
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
    }, [currentDocument, addMessage, appendToMessage, updateMessage, setHighlights, setLoading]);

    /**
     * 上传文档
     */
    /**
     * 上传文档
     */
    const uploadDocument = useCallback(async (file: File): Promise<string | null> => {
        const formData = new FormData();
        formData.append('file', file);

        // 智谱配置
        if (config.zhipuApiKey) {
            formData.append('zhipu_api_key', config.zhipuApiKey);
        }
        // OCR提供商选择
        formData.append('ocr_provider', 'baidu');

        // 百度OCR配置
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
    }, [config]);

    /**
     * 获取文档信息
     */
    const getDocument = useCallback(async (docId: string) => {
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
    }, []);

    /**
     * 监听处理进度
     */
    const watchProgress = useCallback((docId: string, onProgress: (progress: any) => void): () => void => {
        const eventSource = new EventSource(`${API_BASE}/documents/${docId}/progress`);

        eventSource.addEventListener('progress', (event) => {
            try {
                const data = JSON.parse(event.data);
                onProgress(data);

                if (data.stage === 'completed' || data.stage === 'failed') {
                    eventSource.close();
                }
            } catch {
                // 忽略
            }
        });

        eventSource.onerror = () => {
            eventSource.close();
        };

        return () => eventSource.close();
    }, []);

    return {
        askQuestion,
        uploadDocument,
        getDocument,
        watchProgress,
    };
}
