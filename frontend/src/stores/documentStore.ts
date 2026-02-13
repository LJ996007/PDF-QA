import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type { PromptTemplate } from '../constants/prompts';
import { createExamplePrompts } from '../constants/prompts';

export interface BoundingBox {
    page: number;
    x: number;
    y: number;
    w: number;
    h: number;
}

export interface TextChunk {
    id: string;
    refId: string;
    ref_id?: string;
    content: string;
    page: number;
    bbox: BoundingBox;
    source: 'native' | 'ocr';
}

export interface ComplianceItem {
    id: number;
    requirement: string;
    status: 'satisfied' | 'unsatisfied' | 'partial' | 'unknown' | 'error';
    response: string;
    references: TextChunk[];
}

export type PageOcrStatus = 'unrecognized' | 'processing' | 'recognized' | 'failed';

export type ViewerFocusSource = 'chat' | 'compliance';

export interface ViewerFocusRequest {
    requestId: number;
    page: number;
    bbox: BoundingBox;
    source: ViewerFocusSource;
}

export interface Document {
    id: string;
    name: string;
    totalPages: number;
    ocrRequiredPages: number[];
    recognizedPages: number[];
    pageOcrStatus: Record<number, PageOcrStatus>;
    ocrMode: 'manual' | 'full';
    thumbnails: string[];
}

export interface OcrQueueProgress {
    total: number;
    completed: number;
    failed: number;
    isRunning: boolean;
    message: string;
}

export interface ChatMessage {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    references: TextChunk[];
    activeRefs: string[];
    timestamp: Date;
    isStreaming?: boolean;
}

export interface AppConfig {
    apiBaseUrl: string;
    embeddingsProvider: 'zhipu' | 'ollama';
    zhipuApiKey: string;
    baiduApiKey: string;
    baiduSecretKey: string;
    baiduOcrUrl: string;
    baiduOcrToken: string;
    ocrProvider: 'baidu';
    deepseekApiKey: string;
    theme: 'light' | 'dark';
    pdfScale: number;
    selectedPromptId: string;
    customPrompts: PromptTemplate[];
}

const DEFAULT_CONFIG: AppConfig = {
    apiBaseUrl: 'http://localhost:8000',
    embeddingsProvider: 'zhipu',
    zhipuApiKey: '',
    baiduApiKey: '',
    baiduSecretKey: '',
    baiduOcrUrl: '',
    baiduOcrToken: '',
    ocrProvider: 'baidu',
    deepseekApiKey: '',
    theme: 'light',
    pdfScale: 1.0,
    selectedPromptId: '',
    customPrompts: [],
};

interface DocumentState {
    currentDocument: Document | null;
    pdfUrl: string | null;

    scale: number;
    currentPage: number;
    highlights: TextChunk[];
    viewerFocusRequest: ViewerFocusRequest | null;

    selectedPages: number[];
    ocrQueueProgress: OcrQueueProgress;

    messages: ChatMessage[];
    isLoading: boolean;

    config: AppConfig;

    complianceResults: ComplianceItem[];
    complianceMarkdown: string;
    complianceRequirements: string;

    setDocument: (doc: Document, pdfUrl: string) => void;
    updateCurrentDocument: (updates: Partial<Document>) => void;
    setPageOcrStatus: (page: number, status: PageOcrStatus) => void;
    clearDocument: () => void;

    setSelectedPages: (pages: number[]) => void;
    toggleSelectedPage: (page: number) => void;
    clearSelectedPages: () => void;

    setOcrQueueProgress: (progress: Partial<OcrQueueProgress>) => void;
    resetOcrQueueProgress: () => void;

    setComplianceResults: (results: ComplianceItem[], markdown: string) => void;
    setComplianceRequirements: (text: string) => void;

    setScale: (scale: number) => void;
    setCurrentPage: (page: number) => void;
    focusReference: (chunk: TextChunk, source: ViewerFocusSource) => void;
    focusPage: (page: number, source: ViewerFocusSource) => void;
    setHighlights: (chunks: TextChunk[]) => void;
    addHighlight: (chunk: TextChunk) => void;
    clearHighlights: () => void;

    addMessage: (message: ChatMessage) => void;
    updateMessage: (id: string, updates: Partial<ChatMessage>) => void;
    appendToMessage: (id: string, text: string, refs?: string[]) => void;
    clearMessages: () => void;
    setLoading: (loading: boolean) => void;

    updateConfig: (config: Partial<AppConfig>) => void;
}

const INITIAL_OCR_QUEUE_PROGRESS: OcrQueueProgress = {
    total: 0,
    completed: 0,
    failed: 0,
    isRunning: false,
    message: '',
};

const toRecordNumberKey = (value: Record<number, PageOcrStatus> | Record<string, PageOcrStatus> | undefined): Record<number, PageOcrStatus> => {
    if (!value) {
        return {};
    }

    const output: Record<number, PageOcrStatus> = {};
    Object.entries(value).forEach(([key, status]) => {
        const page = Number(key);
        if (!Number.isNaN(page)) {
            output[page] = status;
        }
    });
    return output;
};

const toConsistentRecognizedPages = (
    recognizedPages: number[] | undefined,
    pageOcrStatus: Record<number, PageOcrStatus> | undefined,
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

interface StoredPromptTemplate {
    id: string;
    name: string;
    description: string;
    content: string;
    createdAt?: string | Date;
    updatedAt?: string | Date;
}

const initializeConfig = (): AppConfig => {
    const stored = localStorage.getItem('pdf-qa-storage');

    if (!stored) {
        const examplePrompts = createExamplePrompts();
        return {
            ...DEFAULT_CONFIG,
            selectedPromptId: examplePrompts[0].id,
            customPrompts: examplePrompts,
        };
    }

    try {
        const parsed = JSON.parse(stored);
        const config = parsed.state?.config || parsed.config;

        if (!config) {
            const examplePrompts = createExamplePrompts();
            return {
                ...DEFAULT_CONFIG,
                selectedPromptId: examplePrompts[0].id,
                customPrompts: examplePrompts,
            };
        }

        if (config.customPrompts) {
            config.customPrompts = config.customPrompts.map((p: StoredPromptTemplate) => ({
                id: p.id,
                name: p.name,
                description: p.description,
                content: p.content,
                createdAt: p.createdAt ? new Date(p.createdAt) : new Date(),
                updatedAt: p.updatedAt ? new Date(p.updatedAt) : new Date(),
            }));
        }

        if (!config.customPrompts || config.customPrompts.length === 0) {
            const examplePrompts = createExamplePrompts();
            config.customPrompts = examplePrompts;
            config.selectedPromptId = examplePrompts[0].id;
        }

        return { ...DEFAULT_CONFIG, ...config };
    } catch (error) {
        console.error('Failed to parse stored config:', error);
        const examplePrompts = createExamplePrompts();
        return {
            ...DEFAULT_CONFIG,
            selectedPromptId: examplePrompts[0].id,
            customPrompts: examplePrompts,
        };
    }
};

export const useDocumentStore = create<DocumentState>()(
    persist(
        immer((set) => ({
            currentDocument: null,
            pdfUrl: null,
            scale: 1.0,
            currentPage: 1,
            highlights: [],
            viewerFocusRequest: null,
            selectedPages: [],
            ocrQueueProgress: INITIAL_OCR_QUEUE_PROGRESS,
            messages: [],
            isLoading: false,
            config: initializeConfig(),

            complianceResults: [],
            complianceMarkdown: '',
            complianceRequirements: '',

            setDocument: (doc, pdfUrl) => set((state) => {
                const normalizedStatus = toRecordNumberKey(doc.pageOcrStatus);
                const normalizedRecognized = toConsistentRecognizedPages(
                    doc.recognizedPages || [],
                    normalizedStatus,
                    doc.totalPages || 0
                );
                const normalizedRequired = toConsistentRequiredPages(doc.totalPages || 0, normalizedRecognized);

                state.currentDocument = {
                    ...doc,
                    recognizedPages: normalizedRecognized,
                    pageOcrStatus: normalizedStatus,
                    ocrRequiredPages: normalizedRequired,
                };
                state.pdfUrl = pdfUrl;
                state.currentPage = 1;
                state.highlights = [];
                state.viewerFocusRequest = null;
                state.messages = [];
                state.selectedPages = [];
                state.ocrQueueProgress = { ...INITIAL_OCR_QUEUE_PROGRESS };
                state.complianceResults = [];
                state.complianceMarkdown = '';
                state.complianceRequirements = '';
            }),

            updateCurrentDocument: (updates) => set((state) => {
                if (!state.currentDocument) {
                    return;
                }

                const nextTotalPages = updates.totalPages ?? state.currentDocument.totalPages;
                const nextStatus = updates.pageOcrStatus
                    ? {
                        ...state.currentDocument.pageOcrStatus,
                        ...toRecordNumberKey(updates.pageOcrStatus),
                    }
                    : state.currentDocument.pageOcrStatus;

                const nextRecognized = toConsistentRecognizedPages(
                    updates.recognizedPages ?? state.currentDocument.recognizedPages,
                    nextStatus,
                    nextTotalPages
                );
                const nextRequired = toConsistentRequiredPages(nextTotalPages, nextRecognized);

                state.currentDocument = {
                    ...state.currentDocument,
                    ...updates,
                    pageOcrStatus: nextStatus,
                    recognizedPages: nextRecognized,
                    ocrRequiredPages: nextRequired,
                };
            }),

            setPageOcrStatus: (page, status) => set((state) => {
                if (!state.currentDocument) {
                    return;
                }

                state.currentDocument.pageOcrStatus[page] = status;
                state.currentDocument.recognizedPages = toConsistentRecognizedPages(
                    state.currentDocument.recognizedPages,
                    state.currentDocument.pageOcrStatus,
                    state.currentDocument.totalPages
                );
                state.currentDocument.ocrRequiredPages = toConsistentRequiredPages(
                    state.currentDocument.totalPages,
                    state.currentDocument.recognizedPages
                );
            }),

            clearDocument: () => set((state) => {
                state.currentDocument = null;
                state.pdfUrl = null;
                state.highlights = [];
                state.viewerFocusRequest = null;
                state.messages = [];
                state.selectedPages = [];
                state.ocrQueueProgress = { ...INITIAL_OCR_QUEUE_PROGRESS };
                state.complianceResults = [];
                state.complianceMarkdown = '';
                state.complianceRequirements = '';
            }),

            setSelectedPages: (pages) => set((state) => {
                state.selectedPages = [...new Set(pages)].sort((a, b) => a - b);
            }),

            toggleSelectedPage: (page) => set((state) => {
                if (state.selectedPages.includes(page)) {
                    state.selectedPages = state.selectedPages.filter((p) => p !== page);
                } else {
                    state.selectedPages = [...state.selectedPages, page].sort((a, b) => a - b);
                }
            }),

            clearSelectedPages: () => set((state) => {
                state.selectedPages = [];
            }),

            setOcrQueueProgress: (progress) => set((state) => {
                state.ocrQueueProgress = {
                    ...state.ocrQueueProgress,
                    ...progress,
                };
            }),

            resetOcrQueueProgress: () => set((state) => {
                state.ocrQueueProgress = { ...INITIAL_OCR_QUEUE_PROGRESS };
            }),

            setComplianceResults: (results, markdown) => set((state) => {
                state.complianceResults = results;
                state.complianceMarkdown = markdown;
            }),

            setComplianceRequirements: (text) => set((state) => {
                state.complianceRequirements = text;
            }),

            setScale: (scale) => set((state) => {
                state.scale = scale;
            }),

            setCurrentPage: (page) => set((state) => {
                state.currentPage = page;
            }),

            focusReference: (chunk, source) => set((state) => {
                const nextRequestId = (state.viewerFocusRequest?.requestId || 0) + 1;

                state.highlights = [chunk];
                state.currentPage = chunk.page;
                if (state.scale <= 0.7) {
                    state.scale = 1.2;
                }
                state.viewerFocusRequest = {
                    requestId: nextRequestId,
                    page: chunk.page,
                    bbox: chunk.bbox,
                    source,
                };
            }),

            focusPage: (page, source) => set((state) => {
                const nextRequestId = (state.viewerFocusRequest?.requestId || 0) + 1;
                const safePage = Math.max(1, page);

                state.currentPage = safePage;
                if (state.scale <= 0.7) {
                    state.scale = 1.2;
                }
                state.viewerFocusRequest = {
                    requestId: nextRequestId,
                    page: safePage,
                    bbox: {
                        page: safePage,
                        x: 0,
                        y: 0,
                        w: 120,
                        h: 120,
                    },
                    source,
                };
            }),

            setHighlights: (chunks) => set((state) => {
                state.highlights = chunks;
            }),

            addHighlight: (chunk) => set((state) => {
                const exists = state.highlights.some((h) => h.id === chunk.id);
                if (!exists) {
                    state.highlights.push(chunk);
                }
            }),

            clearHighlights: () => set((state) => {
                state.highlights = [];
            }),

            addMessage: (message) => set((state) => {
                state.messages.push(message);
            }),

            updateMessage: (id, updates) => set((state) => {
                const idx = state.messages.findIndex((m) => m.id === id);
                if (idx !== -1) {
                    Object.assign(state.messages[idx], updates);
                }
            }),

            appendToMessage: (id, text, refs) => set((state) => {
                const idx = state.messages.findIndex((m) => m.id === id);
                if (idx !== -1) {
                    state.messages[idx].content += text;
                    if (refs) {
                        state.messages[idx].activeRefs = [
                            ...new Set([...state.messages[idx].activeRefs, ...refs]),
                        ];
                    }
                }
            }),

            clearMessages: () => set((state) => {
                state.messages = [];
            }),

            setLoading: (loading) => set((state) => {
                state.isLoading = loading;
            }),

            updateConfig: (config) => set((state) => {
                Object.assign(state.config, config);
            }),
        })),
        {
            name: 'pdf-qa-storage',
            partialize: (state) => ({
                config: state.config,
            }),
        }
    )
);
