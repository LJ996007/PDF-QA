import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Selecto from 'react-selecto';
import type { OnSelectEnd } from 'react-selecto';
import { Virtuoso, VirtuosoGrid } from 'react-virtuoso';
import type { VirtuosoHandle } from 'react-virtuoso';
import { PageLayer } from './PageLayer';
import { PageGridItem } from './PageGridItem';
import { usePdfLoader } from '../../hooks/usePdfLoader';
import type { PDFLoadResult } from '../../hooks/usePdfLoader';
import { useVectorSearch } from '../../hooks/useVectorSearch';
import { useDocumentStore } from '../../stores/documentStore';
import type { PageOcrStatus } from '../../stores/documentStore';
import './PDFViewer.css';

interface PDFViewerProps {
    pdfUrl?: string;
    pdfFile?: File;
}

export const PDFViewer: React.FC<PDFViewerProps> = ({ pdfUrl, pdfFile }) => {
    const { loadingState, loadFromUrl, loadFromFile, cleanup } = usePdfLoader();
    const { ocrPagesBatch } = useVectorSearch();
    const {
        scale,
        setScale,
        currentPage,
        setCurrentPage,
        highlights,
        viewerFocusRequest,
        currentDocument,
        selectedPages,
        setSelectedPages,
        ocrQueueProgress,
    } = useDocumentStore();

    const [pdfResult, setPdfResult] = useState<PDFLoadResult | null>(null);
    const [gridContainerEl, setGridContainerEl] = useState<HTMLDivElement | null>(null);
    const virtuosoRef = useRef<VirtuosoHandle>(null);
    const scrollerRef = useRef<HTMLElement | Window | null>(null);
    const anchorPageRef = useRef<number | null>(null);
    const activeRequestIdRef = useRef<number>(0);
    const focusTimerRef = useRef<number | null>(null);
    const scaleRef = useRef<number>(scale);

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

    const getPageStatus = useCallback((pageNumber: number): PageOcrStatus => {
        if (!currentDocument) {
            return 'unrecognized';
        }

        if (currentDocument.pageOcrStatus[pageNumber]) {
            return currentDocument.pageOcrStatus[pageNumber];
        }

        if (currentDocument.recognizedPages.includes(pageNumber)) {
            return 'recognized';
        }

        return 'unrecognized';
    }, [currentDocument]);

    const totalPages = useMemo(() => {
        return currentDocument?.totalPages || pdfResult?.numPages || 0;
    }, [currentDocument, pdfResult]);

    const isGridMode = scale <= 0.7 && totalPages > 0;

    useEffect(() => {
        scaleRef.current = scale;
    }, [scale]);

    const selectablePages = useMemo(() => {
        const pages: number[] = [];
        for (let page = 1; page <= totalPages; page += 1) {
            const status = getPageStatus(page);
            if (status === 'unrecognized' || status === 'failed') {
                pages.push(page);
            }
        }
        return pages;
    }, [totalPages, getPageStatus]);

    const clearFocusTimer = useCallback(() => {
        if (focusTimerRef.current !== null) {
            window.clearTimeout(focusTimerRef.current);
            focusTimerRef.current = null;
        }
    }, []);

    useEffect(() => {
        return () => {
            clearFocusTimer();
        };
    }, [clearFocusTimer]);

    useEffect(() => {
        if (!pdfResult || !viewerFocusRequest) {
            return;
        }

        if (isGridMode) {
            return;
        }

        const request = viewerFocusRequest;
        const requestId = request.requestId;
        activeRequestIdRef.current = requestId;
        clearFocusTimer();

        virtuosoRef.current?.scrollToIndex({
            index: Math.max(0, request.page - 1),
            align: 'start',
            behavior: 'auto',
        });

        const alignToTarget = (attempts: number) => {
            if (activeRequestIdRef.current !== requestId) {
                return;
            }

            const scroller = scrollerRef.current;
            if (!(scroller instanceof HTMLElement)) {
                if (attempts <= 0) {
                    return;
                }
                focusTimerRef.current = window.setTimeout(() => alignToTarget(attempts - 1), 80);
                return;
            }

            const foundPage = scroller.querySelector(`.page-wrapper[data-page-number="${request.page}"]`) as HTMLElement | null;
            if (!foundPage) {
                if (attempts <= 0) {
                    return;
                }
                focusTimerRef.current = window.setTimeout(() => alignToTarget(attempts - 1), 120);
                return;
            }

            const effectiveScale = scaleRef.current;
            const highlightTopInPage = request.bbox.y * effectiveScale;
            const highlightHeight = request.bbox.h * effectiveScale;
            const highlightCenterInPage = highlightTopInPage + highlightHeight / 2;
            const highlightCenterInContainer = foundPage.offsetTop + highlightCenterInPage;
            const targetScrollTop = highlightCenterInContainer - scroller.clientHeight / 2;

            scroller.scrollTo({
                top: Math.max(0, targetScrollTop),
                behavior: 'smooth',
            });
            focusTimerRef.current = null;
        };

        focusTimerRef.current = window.setTimeout(() => alignToTarget(8), 80);

        return () => {
            clearFocusTimer();
        };
    }, [clearFocusTimer, isGridMode, pdfResult, viewerFocusRequest]);

    useEffect(() => {
        if (!isGridMode) {
            return;
        }

        const onKeyDown = (event: KeyboardEvent) => {
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
                event.preventDefault();
                setSelectedPages(selectablePages);
            }
        };

        window.addEventListener('keydown', onKeyDown);
        return () => {
            window.removeEventListener('keydown', onKeyDown);
        };
    }, [isGridMode, selectablePages, setSelectedPages]);

    const handleZoom = useCallback((factor: number) => {
        const nextScale = Math.max(0.4, Math.min(3, scale * factor));
        setScale(nextScale);
    }, [scale, setScale]);

    const handleGridItemClick = useCallback((event: React.MouseEvent<HTMLDivElement>, pageNumber: number) => {
        setCurrentPage(pageNumber);
        const status = getPageStatus(pageNumber);
        if (status === 'recognized' || status === 'processing') {
            anchorPageRef.current = pageNumber;
            return;
        }

        if (event.shiftKey && anchorPageRef.current) {
            const [start, end] = [anchorPageRef.current, pageNumber].sort((a, b) => a - b);
            const range: number[] = [];
            for (let page = start; page <= end; page += 1) {
                const pageStatus = getPageStatus(page);
                if (pageStatus === 'unrecognized' || pageStatus === 'failed') {
                    range.push(page);
                }
            }
            setSelectedPages(range);
            return;
        }

        if (event.ctrlKey || event.metaKey) {
            if (selectedPages.includes(pageNumber)) {
                setSelectedPages(selectedPages.filter((p) => p !== pageNumber));
            } else {
                setSelectedPages([...selectedPages, pageNumber]);
            }
            anchorPageRef.current = pageNumber;
            return;
        }

        setSelectedPages([pageNumber]);
        anchorPageRef.current = pageNumber;
    }, [getPageStatus, selectedPages, setCurrentPage, setSelectedPages]);

    const handleSelectoEnd = useCallback((event: OnSelectEnd) => {
        const pages = (event.selected || [])
            .map((el) => (el instanceof HTMLElement ? Number(el.getAttribute('data-page-number')) : NaN))
            .filter((num: number) => !Number.isNaN(num));

        if (pages.length === 0) {
            return;
        }

        const inputEvent = event.inputEvent as MouseEvent | KeyboardEvent | undefined;
        const additive = !!inputEvent && (inputEvent.shiftKey || inputEvent.ctrlKey || inputEvent.metaKey);

        if (additive) {
            setSelectedPages(Array.from(new Set<number>([...selectedPages, ...pages])));
        } else {
            setSelectedPages(Array.from(new Set<number>(pages)));
        }

        anchorPageRef.current = pages[pages.length - 1] || null;
        if (anchorPageRef.current) {
            setCurrentPage(anchorPageRef.current);
        }
    }, [selectedPages, setCurrentPage, setSelectedPages]);

    const handleRunSelectedOCR = useCallback(async () => {
        if (ocrQueueProgress.isRunning) {
            return;
        }

        const targets = selectedPages.filter((page) => {
            const status = getPageStatus(page);
            return status === 'unrecognized' || status === 'failed';
        });

        if (targets.length === 0) {
            return;
        }

        await ocrPagesBatch(targets);
    }, [getPageStatus, ocrPagesBatch, ocrQueueProgress.isRunning, selectedPages]);

    if (loadingState.isLoading) {
        return (
            <div className="pdf-loading">
                <div className="loading-spinner" />
                <p>{`\u52A0\u8F7D\u4E2D... ${loadingState.progress}%`}</p>
            </div>
        );
    }

    if (loadingState.error) {
        return (
            <div className="pdf-error">
                <p>{`\u52A0\u8F7D\u5931\u8D25: ${loadingState.error}`}</p>
            </div>
        );
    }

    if (!pdfResult) {
        return (
            <div className="pdf-placeholder">
                <p>{'\u8BF7\u4E0A\u4F20 PDF \u6587\u6863'}</p>
            </div>
        );
    }

    return (
        <div className="pdf-viewer">
            <div className="pdf-toolbar">
                <button onClick={() => handleZoom(0.8)} title={'\u7F29\u5C0F'}>
                    <span>-</span>
                </button>
                <span className="zoom-level">{Math.round(scale * 100)}%</span>
                <button onClick={() => handleZoom(1.25)} title={'\u653E\u5927'}>
                    <span>+</span>
                </button>

                <span className="viewer-mode-tag">
                    {isGridMode ? '\u7F51\u683C\u9009\u62E9\u6A21\u5F0F' : '\u9605\u8BFB\u6A21\u5F0F'}
                </span>

                {isGridMode && (
                    <>
                        <span className="selection-indicator">{`\u5DF2\u9009 ${selectedPages.length} \u9875`}</span>
                        <button
                            className="ocr-run-btn"
                            disabled={selectedPages.length === 0 || ocrQueueProgress.isRunning}
                            onClick={handleRunSelectedOCR}
                        >
                            {ocrQueueProgress.isRunning ? '\u8BC6\u522B\u4E2D...' : '\u8BC6\u522B\u6240\u9009\u9875'}
                        </button>
                    </>
                )}

                <span className="page-indicator">{`\u7B2C ${currentPage} / ${pdfResult.numPages} \u9875`}</span>
            </div>

            {ocrQueueProgress.message && (
                <div className="ocr-progress-bar">
                    <span>{ocrQueueProgress.message}</span>
                </div>
            )}

            <div className="pdf-scroll-container" style={{ flex: 1, overflow: 'hidden' }}>
                {isGridMode ? (
                    <div className="pdf-grid-container" ref={setGridContainerEl}>
                        <Selecto
                            container={gridContainerEl || undefined}
                            dragContainer={'.pdf-grid-container'}
                            selectableTargets={['.page-grid-item.selectable']}
                            selectByClick={false}
                            selectFromInside={false}
                            hitRate={40}
                            toggleContinueSelect="shift"
                            onSelectEnd={handleSelectoEnd}
                        />
                        <VirtuosoGrid
                            style={{ height: '100%' }}
                            totalCount={totalPages}
                            listClassName="pdf-grid-list"
                            itemClassName="pdf-grid-item-shell"
                            itemContent={(index) => {
                                const pageNumber = index + 1;
                                return (
                                    <PageGridItem
                                        pageNumber={pageNumber}
                                        thumbnail={currentDocument?.thumbnails?.[index]}
                                        status={getPageStatus(pageNumber)}
                                        selected={selectedPages.includes(pageNumber)}
                                        onClick={handleGridItemClick}
                                    />
                                );
                            }}
                        />
                    </div>
                ) : (
                    <Virtuoso
                        ref={virtuosoRef}
                        scrollerRef={(ref) => {
                            scrollerRef.current = ref;
                        }}
                        style={{ height: '100%' }}
                        totalCount={pdfResult.numPages}
                        itemContent={(index) => (
                            <PageLayer
                                key={index}
                                pageNumber={index + 1}
                                scale={scale}
                                pdfResult={pdfResult}
                                highlights={highlights.filter((h) => h.page === index + 1)}
                            />
                        )}
                        rangeChanged={(range) => {
                            setCurrentPage(range.startIndex + 1);
                        }}
                        overscan={2}
                    />
                )}
            </div>
        </div>
    );
};
