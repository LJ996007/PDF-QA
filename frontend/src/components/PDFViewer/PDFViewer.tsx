import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { Virtuoso } from 'react-virtuoso';
import type { VirtuosoHandle } from 'react-virtuoso';
import { PageLayer } from './PageLayer';
import { PageGridItem } from './PageGridItem';
import { usePdfLoader } from '../../hooks/usePdfLoader';
import type { PDFLoadResult } from '../../hooks/usePdfLoader';
import { useDocumentStore, type PageOcrStatus, type TextChunk } from '../../stores/documentStore';
import { useVectorSearch } from '../../hooks/useVectorSearch';
import './PDFViewer.css';

interface PDFViewerProps {
    pdfUrl?: string;
    pdfFile?: File;
    onRecognizeQueued?: (docId: string) => void;
}

const EMPTY_HIGHLIGHTS: TextChunk[] = [];

export const PDFViewer: React.FC<PDFViewerProps> = ({ pdfUrl, pdfFile, onRecognizeQueued }) => {
    const { loadingState, loadFromUrl, loadFromFile, cleanup } = usePdfLoader();
    const { recognizePages } = useVectorSearch();

    const scale = useDocumentStore((state) => state.scale);
    const setScale = useDocumentStore((state) => state.setScale);
    const currentPage = useDocumentStore((state) => state.currentPage);
    const setCurrentPage = useDocumentStore((state) => state.setCurrentPage);
    const highlights = useDocumentStore((state) => state.highlights);
    const viewMode = useDocumentStore((state) => state.viewMode);
    const setViewMode = useDocumentStore((state) => state.setViewMode);
    const currentDocument = useDocumentStore((state) => state.currentDocument);
    const updateDocumentOcrStatus = useDocumentStore((state) => state.updateDocumentOcrStatus);
    const selectedPages = useDocumentStore((state) => state.selectedPages);
    const setSelectedPages = useDocumentStore((state) => state.setSelectedPages);
    const activeProgress = useDocumentStore((state) => state.activeProgress);
    const setTabProgress = useDocumentStore((state) => state.setTabProgress);
    const activeDocId = currentDocument?.id ?? '';

    const [pdfResult, setPdfResult] = useState<PDFLoadResult | null>(null);
    const [pageStatuses, setPageStatuses] = useState<Record<number, PageOcrStatus>>({});

    const virtuosoRef = useRef<VirtuosoHandle>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const scrollerRef = useRef<HTMLElement | Window | null>(null);
    const prevScaleRef = useRef(scale);
    const highlightsRevisionRef = useRef<Record<string, { revision: number; signature: string }>>({});
    const manualGridRevisionRef = useRef<Record<string, number | null>>({});

    const isRecognizing = activeProgress?.stage === 'ocr';
    const highlightSignature = useMemo(
        () => highlights
            .map((chunk) => `${chunk.refId}:${chunk.page}:${chunk.bbox.x},${chunk.bbox.y},${chunk.bbox.w},${chunk.bbox.h}`)
            .join('|'),
        [highlights]
    );

    useEffect(() => {
        if (currentDocument?.pageOcrStatus) {
            setPageStatuses(currentDocument.pageOcrStatus);
        }
    }, [currentDocument]);

    useEffect(() => {
        if (!activeDocId) {
            return;
        }

        const revisions = highlightsRevisionRef.current;
        const current = revisions[activeDocId];
        if (!current) {
            revisions[activeDocId] = { revision: 1, signature: highlightSignature };
            manualGridRevisionRef.current[activeDocId] = null;
            return;
        }

        if (current.signature === highlightSignature) {
            return;
        }

        current.revision += 1;
        current.signature = highlightSignature;
        manualGridRevisionRef.current[activeDocId] = null;
    }, [activeDocId, highlightSignature]);

    useEffect(() => {
        const load = async () => {
            let result: PDFLoadResult | null = null;

            if (pdfUrl) {
                result = await loadFromUrl(pdfUrl);
            } else if (pdfFile) {
                result = await loadFromFile(pdfFile);
            }

            setPdfResult(result);
        };

        load();

        return () => {
            cleanup();
        };
    }, [pdfUrl, pdfFile, loadFromUrl, loadFromFile, cleanup]);

    const handlePageClick = useCallback((_event: React.MouseEvent, pageNumber: number) => {
        const status = pageStatuses[pageNumber];
        if (status === 'recognized' || status === 'processing') {
            return;
        }

        const next = new Set(selectedPages);
        if (next.has(pageNumber)) {
            next.delete(pageNumber);
        } else {
            next.add(pageNumber);
        }
        setSelectedPages(Array.from(next));
    }, [pageStatuses, selectedPages, setSelectedPages]);

    const handleRecognize = useCallback(async () => {
        if (!currentDocument || selectedPages.length === 0) return;

        const pagesToRecognize = [...selectedPages].sort((a, b) => a - b);
        const response = await recognizePages(currentDocument.id, pagesToRecognize);
        if (!response) {
            return;
        }

        const queued = Array.isArray(response.pages) ? response.pages : pagesToRecognize;
        if (queued.length === 0) {
            setSelectedPages([]);
            return;
        }

        setSelectedPages([]);
        const nextStatusMap = { ...pageStatuses };
        queued.forEach((pageNum: number) => {
            if (nextStatusMap[pageNum] !== 'recognized') {
                nextStatusMap[pageNum] = 'processing';
            }
        });
        setPageStatuses(nextStatusMap);
        updateDocumentOcrStatus(
            currentDocument.recognizedPages || [],
            nextStatusMap as Record<number, PageOcrStatus>
        );
        setTabProgress(currentDocument.id, {
            stage: 'ocr',
            current: 0,
            total: 100,
            message: typeof response.message === 'string' ? response.message : '已加入后台 OCR 队列',
            document_id: currentDocument.id,
        });
        onRecognizeQueued?.(currentDocument.id);
    }, [
        currentDocument,
        selectedPages,
        recognizePages,
        setSelectedPages,
        pageStatuses,
        updateDocumentOcrStatus,
        setTabProgress,
        onRecognizeQueued,
    ]);

    const highlightsByPage = useMemo(() => {
        const grouped = new Map<number, TextChunk[]>();
        for (const chunk of highlights) {
            const pageHighlights = grouped.get(chunk.page);
            if (pageHighlights) {
                pageHighlights.push(chunk);
            } else {
                grouped.set(chunk.page, [chunk]);
            }
        }
        return grouped;
    }, [highlights]);

    const scrollToHighlight = useCallback((
        pageNum: number,
        bbox: { x: number; y: number; w: number; h: number },
        smooth = true
    ) => {
        virtuosoRef.current?.scrollToIndex({
            index: pageNum - 1,
            align: 'start',
            behavior: 'auto',
        });

        const attemptScroll = (attemptsLeft: number) => {
            if (attemptsLeft <= 0) {
                return;
            }

            const scroller = scrollerRef.current;
            if (!(scroller instanceof HTMLElement)) {
                window.setTimeout(() => attemptScroll(attemptsLeft - 1), 32);
                return;
            }

            const pageEl = scroller.querySelector<HTMLElement>(`.page-wrapper[data-page-number="${pageNum}"]`);
            if (!pageEl) {
                window.setTimeout(() => attemptScroll(attemptsLeft - 1), 48);
                return;
            }

            const highlightCenterInPage = (bbox.y + bbox.h / 2) * scale;
            const highlightCenterInContainer = pageEl.offsetTop + highlightCenterInPage;
            const targetScrollTop = highlightCenterInContainer - scroller.clientHeight / 2;

            scroller.scrollTo({
                top: Math.max(0, targetScrollTop),
                behavior: smooth ? 'smooth' : 'auto',
            });
        };

        window.requestAnimationFrame(() => attemptScroll(6));
    }, [scale]);

    useEffect(() => {
        if (highlights.length > 0 && pdfResult) {
            if (viewMode === 'grid') {
                const currentRevision = highlightsRevisionRef.current[activeDocId]?.revision ?? 0;
                const manualRevision = manualGridRevisionRef.current[activeDocId] ?? null;
                if (manualRevision === currentRevision) {
                    return;
                }
                setViewMode('list');
                return;
            }

            const firstHighlight = highlights[0];
            scrollToHighlight(firstHighlight.page, firstHighlight.bbox, true);
        }
    }, [activeDocId, highlights, pdfResult, scrollToHighlight, viewMode, setViewMode]);

    useEffect(() => {
        const scaleChanged = prevScaleRef.current !== scale;
        prevScaleRef.current = scale;

        if (!scaleChanged) {
            return;
        }

        if (highlights.length > 0 && pdfResult && viewMode === 'list') {
            const firstHighlight = highlights[0];
            const timer = window.setTimeout(() => {
                scrollToHighlight(firstHighlight.page, firstHighlight.bbox, false);
            }, 120);
            return () => window.clearTimeout(timer);
        }
    }, [scale, highlights, pdfResult, scrollToHighlight, viewMode]);

    const handleZoom = useCallback((factor: number) => {
        const newScale = Math.max(0.5, Math.min(3, scale * factor));
        setScale(newScale);
    }, [scale, setScale]);

    const handleViewModeChange = useCallback((mode: 'list' | 'grid') => {
        if (mode === viewMode) {
            return;
        }

        if (mode === 'grid' && highlights.length > 0 && activeDocId) {
            const currentRevision = highlightsRevisionRef.current[activeDocId]?.revision ?? 0;
            manualGridRevisionRef.current[activeDocId] = currentRevision;
        }

        setViewMode(mode);
    }, [activeDocId, highlights.length, setViewMode, viewMode]);

    const renderViewModeToggle = useCallback(() => (
        <div className="view-mode-toggle" role="group" aria-label="页面浏览模式">
            <button
                type="button"
                className={`view-mode-btn ${viewMode === 'list' ? 'is-active' : ''}`}
                title="单页浏览"
                aria-label="单页浏览"
                aria-pressed={viewMode === 'list'}
                onClick={() => handleViewModeChange('list')}
            >
                <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <rect x="5" y="3.5" width="14" height="17" rx="1.8" stroke="currentColor" strokeWidth="1.8" />
                    <path d="M8 8h8M8 12h8M8 16h5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                </svg>
            </button>
            <button
                type="button"
                className={`view-mode-btn ${viewMode === 'grid' ? 'is-active' : ''}`}
                title="网格浏览"
                aria-label="网格浏览"
                aria-pressed={viewMode === 'grid'}
                onClick={() => handleViewModeChange('grid')}
            >
                <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <rect x="4" y="4" width="7" height="7" rx="1.2" stroke="currentColor" strokeWidth="1.8" />
                    <rect x="13" y="4" width="7" height="7" rx="1.2" stroke="currentColor" strokeWidth="1.8" />
                    <rect x="4" y="13" width="7" height="7" rx="1.2" stroke="currentColor" strokeWidth="1.8" />
                    <rect x="13" y="13" width="7" height="7" rx="1.2" stroke="currentColor" strokeWidth="1.8" />
                </svg>
            </button>
        </div>
    ), [handleViewModeChange, viewMode]);

    if (loadingState.isLoading) {
        return (
            <div className="pdf-loading">
                <div className="loading-spinner" />
                <p>加载中... {loadingState.progress}%</p>
            </div>
        );
    }

    if (loadingState.error) {
        return (
            <div className="pdf-error">
                <p>加载失败: {loadingState.error}</p>
            </div>
        );
    }

    if (!pdfResult) {
        return (
            <div className="pdf-placeholder">
                <p>请上传 PDF 文件</p>
            </div>
        );
    }

    if (viewMode === 'grid') {
        const totalPages = pdfResult.numPages;
        const thumbnails = currentDocument?.thumbnails || [];

        return (
            <div className="pdf-viewer" ref={containerRef}>
                <div className="pdf-toolbar">
                    {renderViewModeToggle()}
                    <span className="selection-indicator">
                        已选 {selectedPages.length} 页
                    </span>
                    <button
                        className="ocr-run-btn"
                        onClick={handleRecognize}
                        disabled={isRecognizing || selectedPages.length === 0}
                    >
                        {isRecognizing ? '识别中...' : '识别选中页面'}
                    </button>
                    <span className="page-indicator">
                        共 {totalPages} 页
                    </span>
                </div>

                <div className="pdf-scroll-container pdf-grid-container">
                    <div className="pdf-grid-list">
                        {Array.from({ length: totalPages }, (_, i) => i + 1).map((pageNum) => (
                            <div key={pageNum} className="pdf-grid-item-shell">
                                <PageGridItem
                                    pageNumber={pageNum}
                                    thumbnail={thumbnails[pageNum - 1]}
                                    status={pageStatuses[pageNum] || 'unrecognized'}
                                    selected={selectedPages.includes(pageNum)}
                                    onClick={handlePageClick}
                                />
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="pdf-viewer" ref={containerRef}>
            <div className="pdf-toolbar">
                {renderViewModeToggle()}
                <button onClick={() => handleZoom(0.8)} title="缩小">
                    <span>-</span>
                </button>
                <span className="zoom-level">{Math.round(scale * 100)}%</span>
                <button onClick={() => handleZoom(1.25)} title="放大">
                    <span>+</span>
                </button>
                <span className="page-indicator">
                    第 {currentPage} / {pdfResult.numPages} 页
                </span>
            </div>

            <div className="pdf-scroll-container" style={{ flex: 1, overflow: 'hidden' }}>
                <Virtuoso
                    ref={virtuosoRef}
                    scrollerRef={(ref) => { scrollerRef.current = ref; }}
                    style={{ height: '100%' }}
                    totalCount={pdfResult.numPages}
                    initialTopMostItemIndex={Math.max(0, currentPage - 1)}
                    itemContent={(index) => (
                        <PageLayer
                            key={index}
                            pageNumber={index + 1}
                            scale={scale}
                            pdfResult={pdfResult}
                            highlights={highlightsByPage.get(index + 1) || EMPTY_HIGHLIGHTS}
                        />
                    )}
                    rangeChanged={(range) => {
                        setCurrentPage(range.startIndex + 1);
                    }}
                    overscan={2}
                />
            </div>
        </div>
    );
};
