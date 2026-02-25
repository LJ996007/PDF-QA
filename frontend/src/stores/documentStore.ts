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
export type ViewMode = 'list' | 'grid';
export type RightPanelMode = 'chat' | 'compliance';

export interface Document {
    id: string;
    name: string;
    totalPages: number;
    initialOcrRequiredPages?: number[];
    ocrRequiredPages: number[];
    recognizedPages?: number[];
    pageOcrStatus?: Record<number, PageOcrStatus>;
    ocrMode?: 'manual' | 'full';
    thumbnails: string[];
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

export interface TabProgress {
    stage: 'extracting' | 'embedding' | 'ocr' | 'completed' | 'failed';
    current: number;
    total: number;
    message?: string;
    document_id?: string;
}

export interface TabState {
    docId: string;
    document: Document;
    pdfUrl: string | null;
    scale: number;
    currentPage: number;
    highlights: TextChunk[];
    viewMode: ViewMode;
    selectedPages: number[];
    messages: ChatMessage[];
    isLoading: boolean;
    complianceResults: ComplianceItem[];
    complianceMarkdown: string;
    complianceRequirements: string;
    rightPanelMode: RightPanelMode;
    progress: TabProgress | null;
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
    pdfScale: 1,
    selectedPromptId: '',
    customPrompts: [],
};

const toDate = (value: unknown): Date => {
    if (value instanceof Date) return value;
    if (typeof value === 'string' || typeof value === 'number') {
        const parsed = new Date(value);
        if (!Number.isNaN(parsed.getTime())) {
            return parsed;
        }
    }
    return new Date();
};

const normalizeMessages = (messages: unknown): ChatMessage[] => {
    if (!Array.isArray(messages)) return [];
    return messages
        .map((m) => {
            if (!m || typeof m !== 'object') return null;
            const raw = m as Partial<ChatMessage>;
            if (!raw.id || !raw.role) return null;
            return {
                id: String(raw.id),
                role: raw.role,
                content: String(raw.content || ''),
                references: Array.isArray(raw.references) ? raw.references : [],
                activeRefs: Array.isArray(raw.activeRefs) ? raw.activeRefs : [],
                timestamp: toDate(raw.timestamp),
                isStreaming: Boolean(raw.isStreaming),
            } as ChatMessage;
        })
        .filter((m): m is ChatMessage => Boolean(m));
};

const normalizeSelectedPages = (pages: unknown): number[] => {
    if (!Array.isArray(pages)) return [];
    const uniq = new Set<number>();
    pages.forEach((p) => {
        const num = Number(p);
        if (Number.isInteger(num) && num > 0) {
            uniq.add(num);
        }
    });
    return Array.from(uniq).sort((a, b) => a - b);
};

const normalizeProgress = (progress: unknown): TabProgress | null => {
    if (!progress || typeof progress !== 'object') return null;
    const raw = progress as Partial<TabProgress>;
    const stage = raw.stage;
    if (!stage || !['extracting', 'embedding', 'ocr', 'completed', 'failed'].includes(stage)) {
        return null;
    }
    return {
        stage,
        current: Number(raw.current || 0),
        total: Number(raw.total || 100),
        message: raw.message ? String(raw.message) : undefined,
        document_id: raw.document_id ? String(raw.document_id) : undefined,
    };
};

const createTabState = (doc: Document, pdfUrl: string | null): TabState => ({
    docId: doc.id,
    document: doc,
    pdfUrl,
    scale: 1,
    currentPage: 1,
    highlights: [],
    viewMode: 'list',
    selectedPages: [],
    messages: [],
    isLoading: false,
    complianceResults: [],
    complianceMarkdown: '',
    complianceRequirements: '',
    rightPanelMode: 'chat',
    progress: null,
});

const normalizeTabState = (tab: unknown, docId: string): TabState | null => {
    if (!tab || typeof tab !== 'object') return null;
    const raw = tab as Partial<TabState>;
    const doc = raw.document;
    if (!doc || typeof doc !== 'object') return null;

    const normalizedDoc: Document = {
        id: String((doc as Document).id || docId),
        name: String((doc as Document).name || docId),
        totalPages: Number((doc as Document).totalPages || 0),
        initialOcrRequiredPages: Array.isArray((doc as Document).initialOcrRequiredPages)
            ? (doc as Document).initialOcrRequiredPages
            : [],
        ocrRequiredPages: Array.isArray((doc as Document).ocrRequiredPages)
            ? (doc as Document).ocrRequiredPages
            : [],
        recognizedPages: Array.isArray((doc as Document).recognizedPages)
            ? (doc as Document).recognizedPages
            : [],
        pageOcrStatus: (doc as Document).pageOcrStatus || {},
        ocrMode: (doc as Document).ocrMode || 'manual',
        thumbnails: Array.isArray((doc as Document).thumbnails) ? (doc as Document).thumbnails : [],
    };

    return {
        docId,
        document: normalizedDoc,
        pdfUrl: typeof raw.pdfUrl === 'string' ? raw.pdfUrl : null,
        scale: Number(raw.scale || 1),
        currentPage: Number(raw.currentPage || 1),
        highlights: Array.isArray(raw.highlights) ? raw.highlights : [],
        viewMode: raw.viewMode === 'grid' ? 'grid' : 'list',
        selectedPages: normalizeSelectedPages(raw.selectedPages),
        messages: normalizeMessages(raw.messages),
        isLoading: Boolean(raw.isLoading),
        complianceResults: Array.isArray(raw.complianceResults) ? raw.complianceResults : [],
        complianceMarkdown: String(raw.complianceMarkdown || ''),
        complianceRequirements: String(raw.complianceRequirements || ''),
        rightPanelMode: raw.rightPanelMode === 'compliance' ? 'compliance' : 'chat',
        progress: normalizeProgress(raw.progress),
    };
};

interface DocumentState {
    tabsOrder: string[];
    tabsByDocId: Record<string, TabState>;
    activeDocId: string | null;
    activeTab: TabState | null;

    currentDocument: Document | null;
    pdfUrl: string | null;

    scale: number;
    currentPage: number;
    highlights: TextChunk[];
    viewMode: ViewMode;
    selectedPages: number[];

    messages: ChatMessage[];
    isLoading: boolean;

    config: AppConfig;

    complianceResults: ComplianceItem[];
    complianceMarkdown: string;
    complianceRequirements: string;
    rightPanelMode: RightPanelMode;
    activeProgress: TabProgress | null;

    openOrFocusTab: (doc: Document, pdfUrl: string | null) => void;
    activateTab: (docId: string) => void;
    closeTab: (docId: string) => string | null;
    updateTabDocument: (docId: string, doc: Document) => void;
    setTabPdfUrl: (docId: string, pdfUrl: string | null) => void;
    setTabMessages: (docId: string, messages: ChatMessage[]) => void;
    setTabViewerState: (
        docId: string,
        patch: Partial<Pick<TabState, 'scale' | 'currentPage' | 'viewMode' | 'highlights' | 'selectedPages'>>
    ) => void;
    setTabProgress: (docId: string, progress: TabProgress | null) => void;
    setTabCompliance: (
        docId: string,
        payload: { results?: ComplianceItem[]; markdown?: string; requirements?: string }
    ) => void;
    setTabLoading: (docId: string, loading: boolean) => void;
    setTabRightPanelMode: (docId: string, mode: RightPanelMode) => void;

    setDocument: (doc: Document, pdfUrl: string | null) => void;
    updateDocumentOcrStatus: (recognizedPages: number[], pageOcrStatus: Record<number, PageOcrStatus>) => void;
    clearDocument: () => void;

    setComplianceResults: (results: ComplianceItem[], markdown: string) => void;
    setComplianceRequirements: (text: string) => void;

    setScale: (scale: number) => void;
    setCurrentPage: (page: number) => void;
    setHighlights: (chunks: TextChunk[]) => void;
    addHighlight: (chunk: TextChunk) => void;
    clearHighlights: () => void;
    setViewMode: (mode: ViewMode) => void;
    setSelectedPages: (pages: number[]) => void;

    addMessage: (message: ChatMessage) => void;
    setMessages: (messages: ChatMessage[]) => void;
    updateMessage: (id: string, updates: Partial<ChatMessage>) => void;
    appendToMessage: (id: string, text: string, refs?: string[]) => void;
    clearMessages: () => void;
    setLoading: (loading: boolean) => void;

    setRightPanelMode: (mode: RightPanelMode) => void;

    updateConfig: (config: Partial<AppConfig>) => void;
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
            config.customPrompts = config.customPrompts.map((p: any) => ({
                id: p.id,
                name: p.name,
                description: p.description,
                content: p.content,
                createdAt: p.createdAt ? new Date(p.createdAt) : new Date(),
                updatedAt: p.updatedAt ? new Date(p.updatedAt) : new Date(),
            }));
        }

        if (!config?.customPrompts || config.customPrompts.length === 0) {
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

const syncActiveFromTabs = (state: DocumentState): void => {
    const activeDocId = state.activeDocId;
    const tab = activeDocId ? state.tabsByDocId[activeDocId] : null;

    state.activeTab = tab || null;
    state.currentDocument = tab?.document || null;
    state.pdfUrl = tab?.pdfUrl || null;

    state.scale = tab?.scale ?? 1;
    state.currentPage = tab?.currentPage ?? 1;
    state.highlights = tab?.highlights ?? [];
    state.viewMode = tab?.viewMode ?? 'list';
    state.selectedPages = tab?.selectedPages ?? [];

    state.messages = tab?.messages ?? [];
    state.isLoading = tab?.isLoading ?? false;

    state.complianceResults = tab?.complianceResults ?? [];
    state.complianceMarkdown = tab?.complianceMarkdown ?? '';
    state.complianceRequirements = tab?.complianceRequirements ?? '';
    state.rightPanelMode = tab?.rightPanelMode ?? 'chat';
    state.activeProgress = tab?.progress ?? null;
};

const updateActiveTab = (state: DocumentState, updater: (tab: TabState) => void): void => {
    if (!state.activeDocId) return;
    const tab = state.tabsByDocId[state.activeDocId];
    if (!tab) return;
    updater(tab);
    syncActiveFromTabs(state);
};

export const useDocumentStore = create<DocumentState>()(
    persist(
        immer((set) => ({
            tabsOrder: [],
            tabsByDocId: {},
            activeDocId: null,
            activeTab: null,

            currentDocument: null,
            pdfUrl: null,

            scale: 1,
            currentPage: 1,
            highlights: [],
            viewMode: 'list',
            selectedPages: [],

            messages: [],
            isLoading: false,

            config: initializeConfig(),

            complianceResults: [],
            complianceMarkdown: '',
            complianceRequirements: '',
            rightPanelMode: 'chat',
            activeProgress: null,

            openOrFocusTab: (doc, pdfUrl) => set((state) => {
                const existing = state.tabsByDocId[doc.id];
                if (existing) {
                    existing.document = doc;
                    if (pdfUrl || !existing.pdfUrl) {
                        existing.pdfUrl = pdfUrl;
                    }
                } else {
                    state.tabsByDocId[doc.id] = createTabState(doc, pdfUrl);
                    state.tabsOrder.push(doc.id);
                }
                state.activeDocId = doc.id;
                syncActiveFromTabs(state);
            }),

            activateTab: (docId) => set((state) => {
                if (!state.tabsByDocId[docId]) return;
                state.activeDocId = docId;
                syncActiveFromTabs(state);
            }),

            closeTab: (docId) => {
                let closedPdfUrl: string | null = null;
                set((state) => {
                    const existing = state.tabsByDocId[docId];
                    if (!existing) return;

                    closedPdfUrl = existing.pdfUrl;
                    delete state.tabsByDocId[docId];
                    const idx = state.tabsOrder.indexOf(docId);
                    if (idx !== -1) {
                        state.tabsOrder.splice(idx, 1);
                    }

                    if (state.activeDocId === docId) {
                        const fallbackId = state.tabsOrder[idx - 1] || state.tabsOrder[idx] || state.tabsOrder[0] || null;
                        state.activeDocId = fallbackId;
                    }

                    syncActiveFromTabs(state);
                });
                return closedPdfUrl;
            },

            updateTabDocument: (docId, doc) => set((state) => {
                const tab = state.tabsByDocId[docId];
                if (!tab) return;
                tab.document = doc;
                if (state.activeDocId === docId) {
                    syncActiveFromTabs(state);
                }
            }),

            setTabPdfUrl: (docId, pdfUrl) => set((state) => {
                const tab = state.tabsByDocId[docId];
                if (!tab) return;
                tab.pdfUrl = pdfUrl;
                if (state.activeDocId === docId) {
                    syncActiveFromTabs(state);
                }
            }),

            setTabMessages: (docId, messages) => set((state) => {
                const tab = state.tabsByDocId[docId];
                if (!tab) return;
                tab.messages = messages;
                if (state.activeDocId === docId) {
                    syncActiveFromTabs(state);
                }
            }),

            setTabViewerState: (docId, patch) => set((state) => {
                const tab = state.tabsByDocId[docId];
                if (!tab) return;

                if (typeof patch.scale === 'number') tab.scale = patch.scale;
                if (typeof patch.currentPage === 'number') tab.currentPage = patch.currentPage;
                if (patch.viewMode === 'grid' || patch.viewMode === 'list') tab.viewMode = patch.viewMode;
                if (Array.isArray(patch.highlights)) tab.highlights = patch.highlights;
                if (Array.isArray(patch.selectedPages)) tab.selectedPages = normalizeSelectedPages(patch.selectedPages);

                if (state.activeDocId === docId) {
                    syncActiveFromTabs(state);
                }
            }),

            setTabProgress: (docId, progress) => set((state) => {
                const tab = state.tabsByDocId[docId];
                if (!tab) return;
                tab.progress = progress;
                if (state.activeDocId === docId) {
                    syncActiveFromTabs(state);
                }
            }),

            setTabCompliance: (docId, payload) => set((state) => {
                const tab = state.tabsByDocId[docId];
                if (!tab) return;
                if (Array.isArray(payload.results)) {
                    tab.complianceResults = payload.results;
                }
                if (typeof payload.markdown === 'string') {
                    tab.complianceMarkdown = payload.markdown;
                }
                if (typeof payload.requirements === 'string') {
                    tab.complianceRequirements = payload.requirements;
                }
                if (state.activeDocId === docId) {
                    syncActiveFromTabs(state);
                }
            }),

            setTabLoading: (docId, loading) => set((state) => {
                const tab = state.tabsByDocId[docId];
                if (!tab) return;
                tab.isLoading = loading;
                if (state.activeDocId === docId) {
                    syncActiveFromTabs(state);
                }
            }),

            setTabRightPanelMode: (docId, mode) => set((state) => {
                const tab = state.tabsByDocId[docId];
                if (!tab) return;
                tab.rightPanelMode = mode;
                if (state.activeDocId === docId) {
                    syncActiveFromTabs(state);
                }
            }),

            setDocument: (doc, pdfUrl) => set((state) => {
                const existing = state.tabsByDocId[doc.id];
                if (existing) {
                    existing.document = doc;
                    if (pdfUrl || !existing.pdfUrl) {
                        existing.pdfUrl = pdfUrl;
                    }
                    existing.currentPage = 1;
                    existing.highlights = [];
                } else {
                    state.tabsByDocId[doc.id] = createTabState(doc, pdfUrl);
                    state.tabsOrder.push(doc.id);
                }
                state.activeDocId = doc.id;
                syncActiveFromTabs(state);
            }),

            updateDocumentOcrStatus: (recognizedPages, pageOcrStatus) => set((state) => {
                updateActiveTab(state, (tab) => {
                    tab.document.recognizedPages = recognizedPages;
                    tab.document.pageOcrStatus = pageOcrStatus;
                });
            }),

            clearDocument: () => {
                const activeDocId = useDocumentStore.getState().activeDocId;
                if (!activeDocId) return;
                useDocumentStore.getState().closeTab(activeDocId);
            },

            setComplianceResults: (results, markdown) => set((state) => {
                updateActiveTab(state, (tab) => {
                    tab.complianceResults = results;
                    tab.complianceMarkdown = markdown;
                });
            }),

            setComplianceRequirements: (text) => set((state) => {
                updateActiveTab(state, (tab) => {
                    tab.complianceRequirements = text;
                });
            }),

            setScale: (scale) => set((state) => {
                updateActiveTab(state, (tab) => {
                    tab.scale = scale;
                });
            }),

            setCurrentPage: (page) => set((state) => {
                updateActiveTab(state, (tab) => {
                    tab.currentPage = page;
                });
            }),

            setHighlights: (chunks) => set((state) => {
                updateActiveTab(state, (tab) => {
                    tab.highlights = chunks;
                });
            }),

            addHighlight: (chunk) => set((state) => {
                updateActiveTab(state, (tab) => {
                    const exists = tab.highlights.some((h) => h.id === chunk.id);
                    if (!exists) {
                        tab.highlights.push(chunk);
                    }
                });
            }),

            clearHighlights: () => set((state) => {
                updateActiveTab(state, (tab) => {
                    tab.highlights = [];
                });
            }),

            setViewMode: (mode) => set((state) => {
                updateActiveTab(state, (tab) => {
                    tab.viewMode = mode;
                });
            }),

            setSelectedPages: (pages) => set((state) => {
                updateActiveTab(state, (tab) => {
                    tab.selectedPages = normalizeSelectedPages(pages);
                });
            }),

            addMessage: (message) => set((state) => {
                updateActiveTab(state, (tab) => {
                    tab.messages.push({
                        ...message,
                        timestamp: toDate(message.timestamp),
                    });
                });
            }),

            setMessages: (messages) => set((state) => {
                updateActiveTab(state, (tab) => {
                    tab.messages = normalizeMessages(messages);
                });
            }),

            updateMessage: (id, updates) => set((state) => {
                updateActiveTab(state, (tab) => {
                    const idx = tab.messages.findIndex((m) => m.id === id);
                    if (idx === -1) return;
                    const nextUpdates = { ...updates };
                    if (nextUpdates.timestamp) {
                        nextUpdates.timestamp = toDate(nextUpdates.timestamp);
                    }
                    Object.assign(tab.messages[idx], nextUpdates);
                });
            }),

            appendToMessage: (id, text, refs) => set((state) => {
                updateActiveTab(state, (tab) => {
                    const idx = tab.messages.findIndex((m) => m.id === id);
                    if (idx === -1) return;
                    tab.messages[idx].content += text;
                    if (refs) {
                        tab.messages[idx].activeRefs = [...new Set([...tab.messages[idx].activeRefs, ...refs])];
                    }
                });
            }),

            clearMessages: () => set((state) => {
                updateActiveTab(state, (tab) => {
                    tab.messages = [];
                });
            }),

            setLoading: (loading) => set((state) => {
                updateActiveTab(state, (tab) => {
                    tab.isLoading = loading;
                });
            }),

            setRightPanelMode: (mode) => set((state) => {
                updateActiveTab(state, (tab) => {
                    tab.rightPanelMode = mode;
                });
            }),

            updateConfig: (config) => set((state) => {
                Object.assign(state.config, config);
            }),
        })),
        {
            name: 'pdf-qa-storage',
            version: 2,
            migrate: (persistedState: unknown) => {
                if (!persistedState || typeof persistedState !== 'object') {
                    return persistedState as DocumentState;
                }

                const state = persistedState as Partial<DocumentState>;
                const rawTabs = state.tabsByDocId && typeof state.tabsByDocId === 'object'
                    ? state.tabsByDocId
                    : {};

                const tabsByDocId: Record<string, TabState> = {};
                Object.entries(rawTabs).forEach(([docId, tab]) => {
                    const normalized = normalizeTabState(tab, docId);
                    if (normalized) {
                        tabsByDocId[docId] = normalized;
                    }
                });

                if (Object.keys(tabsByDocId).length === 0 && state.currentDocument?.id) {
                    tabsByDocId[state.currentDocument.id] = createTabState(state.currentDocument, state.pdfUrl || null);
                    tabsByDocId[state.currentDocument.id].messages = normalizeMessages(state.messages || []);
                    tabsByDocId[state.currentDocument.id].highlights = Array.isArray(state.highlights) ? state.highlights : [];
                    tabsByDocId[state.currentDocument.id].viewMode = state.viewMode === 'grid' ? 'grid' : 'list';
                    tabsByDocId[state.currentDocument.id].scale = Number(state.scale || 1);
                    tabsByDocId[state.currentDocument.id].currentPage = Number(state.currentPage || 1);
                    tabsByDocId[state.currentDocument.id].complianceResults = Array.isArray(state.complianceResults) ? state.complianceResults : [];
                    tabsByDocId[state.currentDocument.id].complianceMarkdown = String(state.complianceMarkdown || '');
                    tabsByDocId[state.currentDocument.id].complianceRequirements = String(state.complianceRequirements || '');
                    tabsByDocId[state.currentDocument.id].rightPanelMode = state.rightPanelMode === 'compliance' ? 'compliance' : 'chat';
                    tabsByDocId[state.currentDocument.id].selectedPages = normalizeSelectedPages(state.selectedPages);
                    tabsByDocId[state.currentDocument.id].isLoading = Boolean(state.isLoading);
                }

                const tabsOrderSource = Array.isArray(state.tabsOrder) ? state.tabsOrder : Object.keys(tabsByDocId);
                const tabsOrder = tabsOrderSource.filter((docId) => Boolean(tabsByDocId[docId]));
                const activeDocId = state.activeDocId && tabsByDocId[state.activeDocId]
                    ? state.activeDocId
                    : tabsOrder[0] || null;

                return {
                    ...state,
                    tabsByDocId,
                    tabsOrder,
                    activeDocId,
                } as DocumentState;
            },
            partialize: (state) => {
                const serializedTabs: Record<string, TabState> = {};
                Object.entries(state.tabsByDocId).forEach(([docId, tab]) => {
                    serializedTabs[docId] = {
                        ...tab,
                        pdfUrl: tab.pdfUrl && tab.pdfUrl.startsWith('blob:') ? null : tab.pdfUrl,
                        selectedPages: normalizeSelectedPages(tab.selectedPages),
                    };
                });

                return {
                    config: state.config,
                    tabsOrder: state.tabsOrder,
                    tabsByDocId: serializedTabs,
                    activeDocId: state.activeDocId,
                };
            },
            onRehydrateStorage: () => (state) => {
                if (!state) return;
                setTimeout(() => {
                    useDocumentStore.setState((current) => {
                        const next = { ...current };
                        syncActiveFromTabs(next as DocumentState);
                        return next;
                    });
                }, 0);
            },
        }
    )
);
