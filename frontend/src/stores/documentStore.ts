import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type { PromptTemplate } from '../constants/prompts';
import { createExamplePrompts } from '../constants/prompts';

/**
 * 边界框类型（PDF坐标）
 */
export interface BoundingBox {
    page: number;
    x: number;
    y: number;
    w: number;
    h: number;
}

/**
 * 文本块类型
 */
export interface TextChunk {
    id: string;
    refId: string;
    content: string;
    page: number;
    bbox: BoundingBox;
    source: 'native' | 'ocr';
}

/**
 * 文档信息
 */
export interface Document {
    id: string;
    name: string;
    totalPages: number;
    ocrRequiredPages: number[];
    thumbnails: string[];
}

/**
 * 对话消息
 */
export interface ChatMessage {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    references: TextChunk[];
    activeRefs: string[];
    timestamp: Date;
    isStreaming?: boolean;
}

/**
 * 应用配置
 */
export interface AppConfig {
    zhipuApiKey: string;
    deepseekApiKey: string;
    ocrModel: string;  // 智谱OCR模型名称，如 glm-4v-flash
    ocrProvider: 'zhipu' | 'baidu';  // OCR提供商
    baiduOcrUrl: string;  // 百度PP-OCR API地址
    baiduOcrToken: string;  // 百度PP-OCR Token
    theme: 'light' | 'dark';
    pdfScale: number;
    // 提示词相关
    selectedPromptId: string;      // 当前选中的提示词ID
    customPrompts: PromptTemplate[]; // 用户自定义提示词列表
}

/**
 * Store状态
 */
interface DocumentState {
    // 当前文档
    currentDocument: Document | null;
    pdfUrl: string | null;

    // PDF渲染状态
    scale: number;
    currentPage: number;
    highlights: TextChunk[];

    // 对话状态
    messages: ChatMessage[];
    isLoading: boolean;

    // 配置
    config: AppConfig;

    // Actions
    setDocument: (doc: Document, pdfUrl: string) => void;
    clearDocument: () => void;
    setScale: (scale: number) => void;
    setCurrentPage: (page: number) => void;
    setHighlights: (chunks: TextChunk[]) => void;
    addHighlight: (chunk: TextChunk) => void;
    clearHighlights: () => void;

    // 消息
    addMessage: (message: ChatMessage) => void;
    updateMessage: (id: string, updates: Partial<ChatMessage>) => void;
    appendToMessage: (id: string, text: string, refs?: string[]) => void;
    clearMessages: () => void;
    setLoading: (loading: boolean) => void;

    // 配置
    updateConfig: (config: Partial<AppConfig>) => void;
}

// 初始化配置（首次启动或兼容旧数据）
const initializeConfig = (): AppConfig => {
    const stored = localStorage.getItem('pdf-qa-storage');

    if (!stored) {
        // 首次启动，创建示例提示词
        const examplePrompts = createExamplePrompts();
        return {
            zhipuApiKey: '',
            deepseekApiKey: '',
            ocrModel: 'glm-4v-flash',
            ocrProvider: 'baidu',
            baiduOcrUrl: '',
            baiduOcrToken: '',
            theme: 'light',
            pdfScale: 1.0,
            selectedPromptId: examplePrompts[0].id,
            customPrompts: examplePrompts,
        };
    }

    try {
        const parsed = JSON.parse(stored);
        const config = parsed.state?.config || parsed.config;

        // 兼容旧版本：移除 isBuiltin 字段
        if (config?.customPrompts) {
            config.customPrompts = config.customPrompts.map((p: any) => ({
                id: p.id,
                name: p.name,
                description: p.description,
                content: p.content,
                createdAt: p.createdAt ? new Date(p.createdAt) : new Date(),
                updatedAt: p.updatedAt ? new Date(p.updatedAt) : new Date(),
            }));
        }

        // 如果没有提示词，初始化为示例
        if (!config?.customPrompts || config.customPrompts.length === 0) {
            const examplePrompts = createExamplePrompts();
            config.customPrompts = examplePrompts;
            config.selectedPromptId = examplePrompts[0].id;
        }

        return config;
    } catch (error) {
        console.error('Failed to parse stored config:', error);
        // 解析失败，返回默认配置
        const examplePrompts = createExamplePrompts();
        return {
            zhipuApiKey: '',
            deepseekApiKey: '',
            ocrModel: 'glm-4v-flash',
            ocrProvider: 'baidu',
            baiduOcrUrl: '',
            baiduOcrToken: '',
            theme: 'light',
            pdfScale: 1.0,
            selectedPromptId: examplePrompts[0].id,
            customPrompts: examplePrompts,
        };
    }
};

export const useDocumentStore = create<DocumentState>()(
    persist(
        immer((set) => ({
            // 初始状态
            currentDocument: null,
            pdfUrl: null,
            scale: 1.0,
            currentPage: 1,
            highlights: [],
            messages: [],
            isLoading: false,
            config: initializeConfig(),

            // 文档操作
            setDocument: (doc, pdfUrl) => set((state) => {
                state.currentDocument = doc;
                state.pdfUrl = pdfUrl;
                state.currentPage = 1;
                state.highlights = [];
                state.messages = [];
            }),

            clearDocument: () => set((state) => {
                state.currentDocument = null;
                state.pdfUrl = null;
                state.highlights = [];
                state.messages = [];
            }),

            // 渲染状态
            setScale: (scale) => set((state) => {
                state.scale = scale;
            }),

            setCurrentPage: (page) => set((state) => {
                state.currentPage = page;
            }),

            setHighlights: (chunks) => set((state) => {
                state.highlights = chunks;
            }),

            addHighlight: (chunk) => set((state) => {
                const exists = state.highlights.some(h => h.id === chunk.id);
                if (!exists) {
                    state.highlights.push(chunk);
                }
            }),

            clearHighlights: () => set((state) => {
                state.highlights = [];
            }),

            // 消息操作
            addMessage: (message) => set((state) => {
                state.messages.push(message);
            }),

            updateMessage: (id, updates) => set((state) => {
                const idx = state.messages.findIndex(m => m.id === id);
                if (idx !== -1) {
                    Object.assign(state.messages[idx], updates);
                }
            }),

            appendToMessage: (id, text, refs) => set((state) => {
                const idx = state.messages.findIndex(m => m.id === id);
                if (idx !== -1) {
                    state.messages[idx].content += text;
                    if (refs) {
                        state.messages[idx].activeRefs = [
                            ...new Set([...state.messages[idx].activeRefs, ...refs])
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

            // 配置
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
