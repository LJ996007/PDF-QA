import { useCallback, useRef, useState } from 'react';
import * as pdfjsLib from 'pdfjs-dist';

// 配置 PDF.js worker
pdfjsLib.GlobalWorkerOptions.workerSrc = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjsLib.version}/pdf.worker.min.mjs`;

export interface PDFLoadResult {
    numPages: number;
    getPage: (pageNum: number) => Promise<pdfjsLib.PDFPageProxy>;
}

export interface LoadingState {
    isLoading: boolean;
    progress: number;
    error: string | null;
}

export function usePdfLoader() {
    const [loadingState, setLoadingState] = useState<LoadingState>({
        isLoading: false,
        progress: 0,
        error: null,
    });

    const pdfDocRef = useRef<pdfjsLib.PDFDocumentProxy | null>(null);
    const pagesCacheRef = useRef<Map<number, pdfjsLib.PDFPageProxy>>(new Map());

    /**
     * 加载 PDF 文档。
     */
    const loadDocument = useCallback(async (source: string | ArrayBuffer): Promise<PDFLoadResult | null> => {
        setLoadingState({ isLoading: true, progress: 0, error: null });

        try {
            // 清理之前的文档
            if (pdfDocRef.current) {
                await pdfDocRef.current.destroy();
                pdfDocRef.current = null;
                pagesCacheRef.current.clear();
            }

            // 加载新文档
            const loadingTask = pdfjsLib.getDocument(source);

            loadingTask.onProgress = (progress: { loaded: number; total: number }) => {
                if (progress.total > 0) {
                    setLoadingState((prev) => ({
                        ...prev,
                        progress: Math.round((progress.loaded / progress.total) * 100),
                    }));
                }
            };

            const pdfDoc = await loadingTask.promise;
            pdfDocRef.current = pdfDoc;

            setLoadingState({ isLoading: false, progress: 100, error: null });

            return {
                numPages: pdfDoc.numPages,
                getPage: async (pageNum: number) => {
                    // 优先复用缓存页，避免重复解码
                    if (pagesCacheRef.current.has(pageNum)) {
                        return pagesCacheRef.current.get(pageNum)!;
                    }

                    const page = await pdfDoc.getPage(pageNum);
                    pagesCacheRef.current.set(pageNum, page);
                    return page;
                },
            };
        } catch (error) {
            const errorMessage = error instanceof Error ? error.message : '加载 PDF 失败';
            setLoadingState({ isLoading: false, progress: 0, error: errorMessage });
            return null;
        }
    }, []);

    /**
     * 从 URL 加载 PDF。
     */
    const loadFromUrl = useCallback(async (url: string): Promise<PDFLoadResult | null> => {
        return loadDocument(url);
    }, [loadDocument]);

    /**
     * 从 File 对象加载 PDF。
     */
    const loadFromFile = useCallback(async (file: File): Promise<PDFLoadResult | null> => {
        const arrayBuffer = await file.arrayBuffer();
        return loadDocument(arrayBuffer);
    }, [loadDocument]);

    /**
     * 释放 PDF 相关资源。
     */
    const cleanup = useCallback(async () => {
        if (pdfDocRef.current) {
            await pdfDocRef.current.destroy();
            pdfDocRef.current = null;
            pagesCacheRef.current.clear();
        }
    }, []);

    return {
        loadingState,
        loadFromUrl,
        loadFromFile,
        cleanup,
    };
}
