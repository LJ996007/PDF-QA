import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { Virtuoso } from 'react-virtuoso';
import type { VirtuosoHandle } from 'react-virtuoso';
import { PageLayer } from './PageLayer';
import { PageGridItem } from './PageGridItem';
import { usePdfLoader } from '../../hooks/usePdfLoader';
import type { PDFLoadResult } from '../../hooks/usePdfLoader';
import { useDocumentStore, type PageOcrStatus, type TextChunk } from '../../stores/documentStore';
import './PDFViewer.css';

interface PDFViewerProps {
    pdfUrl?: string;
    pdfFile?: File;
}

const API_BASE = 'http://localhost:8000/api';
const EMPTY_HIGHLIGHTS: TextChunk[] = [];

export const PDFViewer: React.FC<PDFViewerProps> = ({ pdfUrl, pdfFile }) => {
    const { loadingState, loadFromUrl, loadFromFile, cleanup } = usePdfLoader();

    const scale = useDocumentStore((state) => state.scale);
    const setScale = useDocumentStore((state) => state.setScale);
    const currentPage = useDocumentStore((state) => state.currentPage);
    const setCurrentPage = useDocumentStore((state) => state.setCurrentPage);
    const highlights = useDocumentStore((state) => state.highlights);
    const viewMode = useDocumentStore((state) => state.viewMode);
    const setViewMode = useDocumentStore((state) => state.setViewMode);
    const currentDocument = useDocumentStore((state) => state.currentDocument);
    const updateDocumentOcrStatus = useDocumentStore((state) => state.updateDocumentOcrStatus);

    const [pdfResult, setPdfResult] = useState<PDFLoadResult | null>(null);
    const [selectedPages, setSelectedPages] = useState<Set<number>>(new Set());
    const [isRecognizing, setIsRecognizing] = useState(false);
    const [pageStatuses, setPageStatuses] = useState<Record<number, PageOcrStatus>>({});

    const virtuosoRef = useRef<VirtuosoHandle>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const scrollerRef = useRef<HTMLElement | Window | null>(null);
    const prevScaleRef = useRef(scale);

    useEffect(() => {
        if (currentDocument?.pageOcrStatus) {
            setPageStatuses(currentDocument.pageOcrStatus);
        }
    }, [currentDocument]);

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

        setSelectedPages((prev) => {
            const next = new Set(prev);
            if (next.has(pageNumber)) {
                next.delete(pageNumber);
            } else {
                next.add(pageNumber);
            }
            return next;
        });
    }, [pageStatuses]);

    const handleRecognize = useCallback(async () => {
        if (!currentDocument || selectedPages.size === 0) return;

        setIsRecognizing(true);
        const pagesToRecognize = Array.from(selectedPages);

        try {
            const response = await fetch(`${API_BASE}/documents/${currentDocument.id}/recognize`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    pages: pagesToRecognize,
                }),
            });

            if (!response.ok) {
                throw new Error('识别请求失败');
            }

            setSelectedPages(new Set());

            const pollInterval = setInterval(async () => {
                try {
                    const docResponse = await fetch(`${API_BASE}/documents/${currentDocument.id}`);
                    if (!docResponse.ok) {
                        clearInterval(pollInterval);
                        setIsRecognizing(false);
                        return;
                    }

                    const docData = await docResponse.json();
                    const ocrStatus: Record<number, string> = docData.page_ocr_status || {};
                    const recognizedPages: number[] = docData.recognized_pages || [];

                    setPageStatuses(ocrStatus as Record<number, PageOcrStatus>);
                    updateDocumentOcrStatus(recognizedPages, ocrStatus as Record<number, PageOcrStatus>);

                    const allDone = pagesToRecognize.every(
                        (p) => ocrStatus[p] === 'recognized' || ocrStatus[p] === 'failed'
                    );
                    if (allDone) {
                        clearInterval(pollInterval);
                        setIsRecognizing(false);
                    }
                } catch {
                    clearInterval(pollInterval);
                    setIsRecognizing(false);
                }
            }, 2000);
        } catch (error) {
            console.error('识别失败:', error);
            setIsRecognizing(false);
        }
    }, [currentDocument, selectedPages, updateDocumentOcrStatus]);

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
                setViewMode('list');
                return;
            }

            const firstHighlight = highlights[0];
            scrollToHighlight(firstHighlight.page, firstHighlight.bbox, true);
        }
    }, [highlights, pdfResult, scrollToHighlight, viewMode, setViewMode]);

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
        setViewMode(mode);
    }, [setViewMode, viewMode]);

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
                        已选 {selectedPages.size} 页
                    </span>
                    <button
                        className="ocr-run-btn"
                        onClick={handleRecognize}
                        disabled={isRecognizing || selectedPages.size === 0}
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
                                    selected={selectedPages.has(pageNum)}
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
                <button onClick={() => handleZoom(0.8)} title="缩小">
                    <span>-</span>
                </button>
                <span className="zoom-level">{Math.round(scale * 100)}%</span>
                <button onClick={() => handleZoom(1.25)} title="放大">
                    <span>+</span>
                </button>
                {renderViewModeToggle()}
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
