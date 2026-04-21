import React, { startTransition, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Virtuoso } from 'react-virtuoso';
import type { VirtuosoHandle } from 'react-virtuoso';

import { PageGridItem } from './PageGridItem';
import { PageLayer } from './PageLayer';
import type { SearchHighlightItem } from './PageLayer';
import { usePdfLoader } from '../../hooks/usePdfLoader';
import type { PDFLoadResult } from '../../hooks/usePdfLoader';
import { useVectorSearch } from '../../hooks/useVectorSearch';
import { useDocumentStore, type PageOcrStatus, type TextChunk } from '../../stores/documentStore';
import './PDFViewer.css';

interface PDFViewerProps {
    pdfUrl?: string;
    pdfFile?: File;
    onRecognizeQueued?: (docId: string) => void;
}

const EMPTY_HIGHLIGHTS: TextChunk[] = [];
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

export const PDFViewer: React.FC<PDFViewerProps> = ({ pdfUrl, pdfFile, onRecognizeQueued }) => {
    const { loadingState, loadFromUrl, loadFromFile, cleanup } = usePdfLoader();
    const { recognizePages } = useVectorSearch();

    const scale = useDocumentStore((state) => state.scale);
    const setScale = useDocumentStore((state) => state.setScale);
    const thumbnailScale = useDocumentStore((state) => state.thumbnailScale);
    const setThumbnailScale = useDocumentStore((state) => state.setThumbnailScale);
    const currentPage = useDocumentStore((state) => state.currentPage);
    const setCurrentPage = useDocumentStore((state) => state.setCurrentPage);
    const highlights = useDocumentStore((state) => state.highlights);
    const currentDocument = useDocumentStore((state) => state.currentDocument);
    const updateDocumentOcrStatus = useDocumentStore((state) => state.updateDocumentOcrStatus);
    const selectedPages = useDocumentStore((state) => state.selectedPages);
    const setSelectedPages = useDocumentStore((state) => state.setSelectedPages);
    const activeProgress = useDocumentStore((state) => state.activeProgress);
    const setTabProgress = useDocumentStore((state) => state.setTabProgress);

    const [pdfResult, setPdfResult] = useState<PDFLoadResult | null>(null);
    const [pageStatuses, setPageStatuses] = useState<Record<number, PageOcrStatus>>({});
    const [searchQuery, setSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
    const [searchIdx, setSearchIdx] = useState(-1);
    const [isSearching, setIsSearching] = useState(false);
    const [thumbnailPaneWidth, setThumbnailPaneWidth] = useState(320);
    const [isSplitResizing, setIsSplitResizing] = useState(false);
    const [isStackedLayout, setIsStackedLayout] = useState<boolean>(() => (
        typeof window !== 'undefined' ? window.innerWidth <= 1180 : false
    ));

    const viewerRef = useRef<HTMLDivElement | null>(null);
    const virtuosoRef = useRef<VirtuosoHandle>(null);
    const rightScrollerRef = useRef<HTMLElement | Window | null>(null);
    const thumbnailScrollRef = useRef<HTMLDivElement | null>(null);
    const visibleRangeRef = useRef({ startIndex: 0, endIndex: 0 });
    const pendingNavigationPageRef = useRef<number | null>(null);
    const prevScaleRef = useRef(scale);
    const splitResizeStateRef = useRef<{ pointerId: number; startX: number; startWidth: number } | null>(null);

    const activeDocId = currentDocument?.id ?? '';
    const isRecognizing = activeProgress?.stage === 'ocr';
    const thumbnails = currentDocument?.thumbnails || [];

    useEffect(() => {
        startTransition(() => {
            setPageStatuses(currentDocument?.pageOcrStatus ?? {});
        });
    }, [currentDocument?.pageOcrStatus]);

    useEffect(() => {
        startTransition(() => {
            setSearchQuery('');
            setSearchResults([]);
            setSearchIdx(-1);
        });
    }, [activeDocId]);

    useEffect(() => {
        const handleWindowResize = () => {
            const stacked = window.innerWidth <= 1180;
            setIsStackedLayout(stacked);
            if (stacked) {
                setIsSplitResizing(false);
                splitResizeStateRef.current = null;
            }
        };

        handleWindowResize();
        window.addEventListener('resize', handleWindowResize);
        return () => window.removeEventListener('resize', handleWindowResize);
    }, []);

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
    }, [cleanup, loadFromFile, loadFromUrl, pdfFile, pdfUrl]);

    const selectedPagesSorted = useMemo(
        () => [...selectedPages].sort((a, b) => a - b),
        [selectedPages]
    );

    const selectedPagesForOcr = useMemo(
        () => selectedPagesSorted.filter((pageNumber) => {
            const status = pageStatuses[pageNumber];
            return status !== 'recognized' && status !== 'processing';
        }),
        [pageStatuses, selectedPagesSorted]
    );

    const previousSelectedPage = useMemo(() => {
        for (let index = selectedPagesSorted.length - 1; index >= 0; index -= 1) {
            if (selectedPagesSorted[index] < currentPage) {
                return selectedPagesSorted[index];
            }
        }
        return null;
    }, [currentPage, selectedPagesSorted]);

    const nextSelectedPage = useMemo(() => {
        for (const pageNumber of selectedPagesSorted) {
            if (pageNumber > currentPage) {
                return pageNumber;
            }
        }
        return null;
    }, [currentPage, selectedPagesSorted]);

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

    const searchByPage = useMemo(() => {
        const map = new Map<number, SearchHighlightItem[]>();
        searchResults.forEach((result, index) => {
            const list = map.get(result.page) ?? [];
            list.push({
                bbox: result.bbox,
                text: result.text,
                isCurrent: index === searchIdx,
            });
            map.set(result.page, list);
        });
        return map;
    }, [searchIdx, searchResults]);

    const scrollThumbnailIntoView = useCallback((pageNumber: number) => {
        const container = thumbnailScrollRef.current;
        if (!container) return;

        const target = container.querySelector<HTMLElement>(`.page-grid-item[data-page-number="${pageNumber}"]`);
        if (!target) return;

        target.scrollIntoView({
            block: 'nearest',
            inline: 'nearest',
            behavior: 'auto',
        });
    }, []);

    const navigateToPage = useCallback((pageNumber: number) => {
        pendingNavigationPageRef.current = pageNumber;
        setCurrentPage(pageNumber);
        virtuosoRef.current?.scrollToIndex({
            index: Math.max(0, pageNumber - 1),
            align: 'start',
            behavior: 'auto',
        });
    }, [setCurrentPage]);

    const scrollToHighlight = useCallback((
        pageNumber: number,
        bbox: { x: number; y: number; w: number; h: number }
    ) => {
        pendingNavigationPageRef.current = pageNumber;
        setCurrentPage(pageNumber);
        virtuosoRef.current?.scrollToIndex({
            index: Math.max(0, pageNumber - 1),
            align: 'start',
            behavior: 'auto',
        });

        const attemptScroll = (attemptsLeft: number) => {
            if (attemptsLeft <= 0) {
                return;
            }

            const scroller = rightScrollerRef.current;
            if (!(scroller instanceof HTMLElement)) {
                window.setTimeout(() => attemptScroll(attemptsLeft - 1), 32);
                return;
            }

            const pageEl = scroller.querySelector<HTMLElement>(`.page-wrapper[data-page-number="${pageNumber}"]`);
            if (!pageEl) {
                window.setTimeout(() => attemptScroll(attemptsLeft - 1), 48);
                return;
            }

            const highlightCenterInPage = (bbox.y + bbox.h / 2) * scale;
            const highlightCenterInContainer = pageEl.offsetTop + highlightCenterInPage;
            const targetScrollTop = highlightCenterInContainer - scroller.clientHeight / 2;

            scroller.scrollTo({
                top: Math.max(0, targetScrollTop),
                behavior: 'auto',
            });
        };

        window.requestAnimationFrame(() => attemptScroll(6));
    }, [scale, setCurrentPage]);

    const handleTogglePageSelection = useCallback((pageNumber: number) => {
        const next = new Set(selectedPages);
        if (next.has(pageNumber)) {
            next.delete(pageNumber);
        } else {
            next.add(pageNumber);
        }
        setSelectedPages(Array.from(next));
    }, [selectedPages, setSelectedPages]);

    const handleRecognize = useCallback(async () => {
        if (!currentDocument || selectedPagesForOcr.length === 0) return;

        const pagesToRecognize = [...selectedPagesForOcr];
        const response = await recognizePages(currentDocument.id, pagesToRecognize);
        if (!response) {
            return;
        }

        const queued = Array.isArray(response.pages) ? response.pages : pagesToRecognize;
        if (queued.length === 0) {
            return;
        }

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
        onRecognizeQueued,
        pageStatuses,
        recognizePages,
        selectedPagesForOcr,
        setTabProgress,
        updateDocumentOcrStatus,
    ]);

    const goToSearchResult = useCallback((results: SearchResult[], index: number) => {
        if (index < 0 || index >= results.length) return;
        const result = results[index];
        scrollToHighlight(result.page, result.bbox);
    }, [scrollToHighlight]);

    const handleSearch = useCallback(async () => {
        if (!pdfResult || !searchQuery.trim()) {
            setSearchResults([]);
            setSearchIdx(-1);
            return;
        }

        setIsSearching(true);
        const nextResults: SearchResult[] = [];
        const query = searchQuery.toLowerCase();

        for (let pageNumber = 1; pageNumber <= pdfResult.numPages; pageNumber += 1) {
            const page = await pdfResult.getPage(pageNumber);
            const viewport = page.getViewport({ scale: 1 });
            const textContent = await page.getTextContent();

            for (const item of textContent.items) {
                if (!('str' in item) || !item.str.toLowerCase().includes(query)) continue;
                const textItem = item as { str: string; transform: number[]; width: number; height: number };
                const [vpX, vpBaseline] = viewport.convertToViewportPoint(textItem.transform[4], textItem.transform[5]);
                const height = Math.abs(textItem.height) || 12;
                const width = Math.abs(textItem.width) || 50;
                nextResults.push({
                    page: pageNumber,
                    bbox: { x: vpX, y: vpBaseline - height, w: width, h: height },
                    text: textItem.str,
                });
            }
        }

        setIsSearching(false);
        setSearchResults(nextResults);
        if (nextResults.length > 0) {
            setSearchIdx(0);
            goToSearchResult(nextResults, 0);
        } else {
            setSearchIdx(-1);
        }
    }, [goToSearchResult, pdfResult, searchQuery]);

    const handleSearchNext = useCallback(() => {
        if (!searchResults.length) return;
        const nextIndex = (searchIdx + 1) % searchResults.length;
        setSearchIdx(nextIndex);
        goToSearchResult(searchResults, nextIndex);
    }, [goToSearchResult, searchIdx, searchResults]);

    const handleSearchPrev = useCallback(() => {
        if (!searchResults.length) return;
        const prevIndex = (searchIdx - 1 + searchResults.length) % searchResults.length;
        setSearchIdx(prevIndex);
        goToSearchResult(searchResults, prevIndex);
    }, [goToSearchResult, searchIdx, searchResults]);

    const handleSearchKeyDown = useCallback((event: React.KeyboardEvent<HTMLInputElement>) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            if (event.shiftKey) {
                handleSearchPrev();
            } else if (searchResults.length > 0) {
                handleSearchNext();
            } else {
                handleSearch();
            }
        }
        if (event.key === 'Escape') {
            setSearchQuery('');
            setSearchResults([]);
            setSearchIdx(-1);
        }
    }, [handleSearch, handleSearchNext, handleSearchPrev, searchResults.length]);

    useEffect(() => {
        if (!pdfResult || highlights.length === 0) {
            return;
        }

        const firstHighlight = highlights[0];
        scrollToHighlight(firstHighlight.page, firstHighlight.bbox);
    }, [highlights, pdfResult, scrollToHighlight]);

    useEffect(() => {
        const scaleChanged = prevScaleRef.current !== scale;
        prevScaleRef.current = scale;

        if (!scaleChanged || !pdfResult || highlights.length === 0) {
            return;
        }

        const firstHighlight = highlights[0];
        const timer = window.setTimeout(() => {
            scrollToHighlight(firstHighlight.page, firstHighlight.bbox);
        }, 120);

        return () => window.clearTimeout(timer);
    }, [highlights, pdfResult, scale, scrollToHighlight]);

    useEffect(() => {
        if (!pdfResult || currentPage < 1) {
            return;
        }

        const visible = currentPage >= visibleRangeRef.current.startIndex + 1
            && currentPage <= visibleRangeRef.current.endIndex + 1;

        if (pendingNavigationPageRef.current === currentPage || !visible) {
            virtuosoRef.current?.scrollToIndex({
                index: Math.max(0, currentPage - 1),
                align: 'start',
                behavior: 'auto',
            });
            pendingNavigationPageRef.current = null;
        }

        scrollThumbnailIntoView(currentPage);
    }, [currentPage, pdfResult, scrollThumbnailIntoView]);

    const thumbnailCardWidth = useMemo(
        () => clamp(Math.round(148 * thumbnailScale), 44, 280),
        [thumbnailScale]
    );

    const thumbnailZoomLabel = `${Math.round(thumbnailScale * 100)}%`;

    const handleMainZoom = useCallback((factor: number) => {
        setScale(clamp(scale * factor, 0.5, 3));
    }, [scale, setScale]);

    const handleThumbnailZoom = useCallback((factor: number) => {
        setThumbnailScale(clamp(Number((thumbnailScale * factor).toFixed(2)), 0.3, 2));
    }, [setThumbnailScale, thumbnailScale]);

    const stopSplitResize = useCallback(() => {
        splitResizeStateRef.current = null;
        setIsSplitResizing(false);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
    }, []);

    const handleSplitPointerDown = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
        if (isStackedLayout) {
            return;
        }

        event.preventDefault();
        splitResizeStateRef.current = {
            pointerId: event.pointerId,
            startX: event.clientX,
            startWidth: thumbnailPaneWidth,
        };
        setIsSplitResizing(true);
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    }, [isStackedLayout, thumbnailPaneWidth]);

    useEffect(() => {
        if (!isSplitResizing) {
            return undefined;
        }

        const handlePointerMove = (event: PointerEvent) => {
            const resizeState = splitResizeStateRef.current;
            const viewer = viewerRef.current;
            if (!resizeState || !viewer) {
                return;
            }

            const bounds = viewer.getBoundingClientRect();
            const maxWidth = Math.max(280, bounds.width - 420);
            const nextWidth = clamp(
                resizeState.startWidth + (event.clientX - resizeState.startX),
                240,
                maxWidth
            );
            setThumbnailPaneWidth(nextWidth);
        };

        const handlePointerUp = (event: PointerEvent) => {
            if (splitResizeStateRef.current?.pointerId !== event.pointerId) {
                return;
            }
            stopSplitResize();
        };

        window.addEventListener('pointermove', handlePointerMove);
        window.addEventListener('pointerup', handlePointerUp);
        window.addEventListener('pointercancel', handlePointerUp);

        return () => {
            window.removeEventListener('pointermove', handlePointerMove);
            window.removeEventListener('pointerup', handlePointerUp);
            window.removeEventListener('pointercancel', handlePointerUp);
            stopSplitResize();
        };
    }, [isSplitResizing, stopSplitResize]);

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

    return (
        <div className="pdf-viewer">
            <div
                ref={viewerRef}
                className={`pdf-dual-pane ${isSplitResizing ? 'is-resizing' : ''} ${isStackedLayout ? 'is-stacked' : ''}`}
                style={isStackedLayout ? undefined : {
                    gridTemplateColumns: `${thumbnailPaneWidth}px 10px minmax(0, 1fr)`,
                }}
            >
                <aside className="thumbnail-pane">
                    <div className="thumbnail-toolbar">
                        <div className="thumbnail-toolbar-strip">
                            <div className="thumbnail-toolbar-group thumbnail-toolbar-group--compact">
                                <button type="button" className="panel-icon-btn panel-icon-btn--compact" onClick={() => handleThumbnailZoom(0.9)} title="缩小缩略图">
                                    <span>-</span>
                                </button>
                                <span className="zoom-level thumbnail-zoom-level">{thumbnailZoomLabel}</span>
                                <button type="button" className="panel-icon-btn panel-icon-btn--compact" onClick={() => handleThumbnailZoom(1.1)} title="放大缩略图">
                                    <span>+</span>
                                </button>
                            </div>

                            <span className="selection-indicator selection-indicator--chip">已选 {selectedPagesSorted.length}</span>

                            <button
                                type="button"
                                className="panel-icon-btn panel-icon-btn--compact"
                                onClick={() => previousSelectedPage && navigateToPage(previousSelectedPage)}
                                disabled={!previousSelectedPage}
                                title="跳转到上一已选页"
                                aria-label="跳转到上一已选页"
                            >
                                ↑
                            </button>
                            <button
                                type="button"
                                className="panel-icon-btn panel-icon-btn--compact"
                                onClick={() => nextSelectedPage && navigateToPage(nextSelectedPage)}
                                disabled={!nextSelectedPage}
                                title="跳转到下一已选页"
                                aria-label="跳转到下一已选页"
                            >
                                ↓
                            </button>
                            <button
                                type="button"
                                className="panel-action-btn panel-action-btn--primary panel-action-btn--compact thumbnail-ocr-btn"
                                onClick={handleRecognize}
                                disabled={isRecognizing || selectedPagesForOcr.length === 0}
                                title={isRecognizing ? '正在识别选中未识别页' : '识别选中未识别页'}
                            >
                                {isRecognizing ? '识别中' : '识别'}
                            </button>
                        </div>
                    </div>

                    <div className="thumbnail-pane-status">
                        <span>当前页 {currentPage}</span>
                        <span>共 {pdfResult.numPages} 页</span>
                    </div>

                    <div className="thumbnail-grid-scroll" ref={thumbnailScrollRef}>
                        <div
                            className="thumbnail-grid"
                            style={{ '--thumbnail-card-width': `${thumbnailCardWidth}px` } as React.CSSProperties}
                        >
                            {Array.from({ length: pdfResult.numPages }, (_, index) => {
                                const pageNumber = index + 1;
                                return (
                                    <PageGridItem
                                        key={pageNumber}
                                        pageNumber={pageNumber}
                                        thumbnail={thumbnails[pageNumber - 1]}
                                        status={pageStatuses[pageNumber] || 'unrecognized'}
                                        selected={selectedPagesSorted.includes(pageNumber)}
                                        active={currentPage === pageNumber}
                                        thumbnailWidth={thumbnailCardWidth}
                                        onNavigate={navigateToPage}
                                        onToggleSelect={handleTogglePageSelection}
                                    />
                                );
                            })}
                        </div>
                    </div>
                </aside>

                <div
                    className={`pdf-pane-divider ${isSplitResizing ? 'is-active' : ''}`}
                    onPointerDown={handleSplitPointerDown}
                    role="separator"
                    aria-orientation="vertical"
                    aria-label="调整左右预览宽度"
                />

                <section className="pdf-main-pane">
                    <div className="pdf-toolbar">
                        <div className="toolbar-group">
                            <button type="button" onClick={() => handleMainZoom(0.8)} title="缩小">
                                <span>-</span>
                            </button>
                            <span className="zoom-level">{Math.round(scale * 100)}%</span>
                            <button type="button" onClick={() => handleMainZoom(1.25)} title="放大">
                                <span>+</span>
                            </button>
                        </div>

                        <div className="search-section">
                            <div className="search-input-wrap">
                                <svg className="search-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <circle cx="11" cy="11" r="8" />
                                    <path d="m21 21-4.35-4.35" />
                                </svg>
                                <input
                                    type="text"
                                    className="search-input"
                                    placeholder="搜索文字..."
                                    value={searchQuery}
                                    onChange={(event) => {
                                        setSearchQuery(event.target.value);
                                        if (!event.target.value.trim()) {
                                            setSearchResults([]);
                                            setSearchIdx(-1);
                                        }
                                    }}
                                    onKeyDown={handleSearchKeyDown}
                                />
                            </div>
                            {searchQuery.trim() && (
                                <>
                                    <button className="search-btn" onClick={handleSearch} disabled={isSearching}>
                                        {isSearching ? '…' : '搜索'}
                                    </button>
                                    {searchResults.length > 0 && (
                                        <>
                                            <button onClick={handleSearchPrev} className="search-nav-btn" title="上一个 (Shift+Enter)">↑</button>
                                            <button onClick={handleSearchNext} className="search-nav-btn" title="下一个 (Enter)">↓</button>
                                            <span className="search-count">{searchIdx + 1} / {searchResults.length}</span>
                                        </>
                                    )}
                                    {!isSearching && searchResults.length === 0 && searchQuery.trim() && (
                                        <span className="search-no-result">无结果</span>
                                    )}
                                </>
                            )}
                        </div>

                        <span className="page-indicator">
                            第 {currentPage} / {pdfResult.numPages} 页
                        </span>
                    </div>

                    <div className="pdf-main-scroll-shell">
                        <Virtuoso
                            ref={virtuosoRef}
                            scrollerRef={(ref) => { rightScrollerRef.current = ref; }}
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
                                    searchHighlights={searchByPage.get(index + 1)}
                                />
                            )}
                            rangeChanged={(range) => {
                                visibleRangeRef.current = range;
                                setCurrentPage(range.startIndex + 1);
                            }}
                            overscan={2}
                        />
                    </div>
                </section>
            </div>
        </div>
    );
};

interface SearchResult {
    page: number;
    bbox: { x: number; y: number; w: number; h: number };
    text: string;
}
