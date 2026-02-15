import React, { useEffect, useRef, useState, useCallback } from 'react';
import { Virtuoso } from 'react-virtuoso';
import type { VirtuosoHandle } from 'react-virtuoso';
import { PageLayer } from './PageLayer';
import { usePdfLoader } from '../../hooks/usePdfLoader';
import type { PDFLoadResult } from '../../hooks/usePdfLoader';
import { useDocumentStore } from '../../stores/documentStore';
import './PDFViewer.css';

interface PDFViewerProps {
    pdfUrl?: string;
    pdfFile?: File;
}

export const PDFViewer: React.FC<PDFViewerProps> = ({ pdfUrl, pdfFile }) => {
    const { loadingState, loadFromUrl, loadFromFile, cleanup } = usePdfLoader();
    const { scale, setScale, currentPage, setCurrentPage, highlights } = useDocumentStore();

    const [pdfResult, setPdfResult] = useState<PDFLoadResult | null>(null);
    const virtuosoRef = useRef<VirtuosoHandle>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    // Virtuoso 的实际滚动容器
    const scrollerRef = useRef<HTMLElement | Window | null>(null);

    // 加载PDF
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

    // 滚动到高亮位置并垂直居中
    const scrollToHighlight = useCallback((pageNum: number, bbox: { x: number; y: number; w: number; h: number }) => {
        // 先滚动到页面确保页面被渲染
        virtuosoRef.current?.scrollToIndex({
            index: pageNum - 1,
            align: 'start',
            behavior: 'auto',
        });

        // 使用多次尝试来确保能找到页面元素
        const attemptScroll = (attempts: number) => {
            if (attempts <= 0) return;

            requestAnimationFrame(() => {
                // 获取 Virtuoso 的滚动容器
                const scroller = scrollerRef.current;
                if (!scroller || !(scroller instanceof HTMLElement)) {
                    setTimeout(() => attemptScroll(attempts - 1), 50);
                    return;
                }

                // 查找目标页面
                const pageElements = scroller.querySelectorAll('.page-wrapper');
                let foundPage: HTMLElement | null = null;

                // 遍历查找正确的页面
                for (let i = 0; i < pageElements.length; i++) {
                    const el = pageElements[i] as HTMLElement;
                    const pageNumAttr = el.getAttribute('data-page-number');
                    if (pageNumAttr && parseInt(pageNumAttr) === pageNum) {
                        foundPage = el;
                        break;
                    }
                }

                if (!foundPage) {
                    // 页面还没渲染，稍后重试
                    setTimeout(() => attemptScroll(attempts - 1), 100);
                    return;
                }

                // 计算高亮区域在页面中的位置
                // 后端坐标是图像坐标系（原点在左上），与CSS坐标系相同
                // 直接使用，不需要Y轴翻转
                const highlightTopInPage = bbox.y * scale;
                const highlightHeight = bbox.h * scale;
                const highlightCenterInPage = highlightTopInPage + highlightHeight / 2;

                // 计算高亮中心在滚动容器中的绝对位置
                const pageTopInContainer = foundPage.offsetTop;
                const highlightCenterInContainer = pageTopInContainer + highlightCenterInPage;

                // 居中滚动
                const containerHeight = scroller.clientHeight;
                const targetScrollTop = highlightCenterInContainer - containerHeight / 2;

                console.log('[PDFViewer] Scrolling to highlight:', {
                    pageNum,
                    bbox,
                    highlightTopInPage,
                    highlightCenterInPage,
                    pageTopInContainer,
                    highlightCenterInContainer,
                    containerHeight,
                    targetScrollTop,
                });

                scroller.scrollTo({
                    top: Math.max(0, targetScrollTop),
                    behavior: 'smooth'
                });
            });
        };

        // 开始尝试滚动，最多尝试5次
        setTimeout(() => attemptScroll(5), 100);
    }, [scale]);

    // 处理高亮变化，自动滚动到高亮位置并居中
    useEffect(() => {
        if (highlights.length > 0 && pdfResult) {
            const firstHighlight = highlights[0];
            scrollToHighlight(firstHighlight.page, firstHighlight.bbox);
        }
    }, [highlights, pdfResult, scrollToHighlight]);

    // 缩放变化时，如果有高亮则重新居中
    useEffect(() => {
        if (highlights.length > 0 && pdfResult) {
            const firstHighlight = highlights[0];
            // 延迟稍长一些，等待缩放渲染完成
            const timer = setTimeout(() => {
                scrollToHighlight(firstHighlight.page, firstHighlight.bbox);
            }, 200);
            return () => clearTimeout(timer);
        }
    }, [scale, highlights, pdfResult, scrollToHighlight]);

    // 处理缩放
    const handleZoom = useCallback((factor: number) => {
        const newScale = Math.max(0.5, Math.min(3, scale * factor));
        setScale(newScale);
    }, [scale, setScale]);

    // 渲染加载状态
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
                <p>请上传PDF文件</p>
            </div>
        );
    }

    return (
        <div className="pdf-viewer" ref={containerRef}>
            {/* 工具栏 */}
            <div className="pdf-toolbar">
                <button onClick={() => handleZoom(0.8)} title="缩小">
                    <span>−</span>
                </button>
                <span className="zoom-level">{Math.round(scale * 100)}%</span>
                <button onClick={() => handleZoom(1.25)} title="放大">
                    <span>+</span>
                </button>
                <span className="page-indicator">
                    第 {currentPage} / {pdfResult.numPages} 页
                </span>
            </div>

            {/* 虚拟滚动列表 */}
            <div className="pdf-scroll-container" style={{ flex: 1, overflow: 'hidden' }}>
                <Virtuoso
                    ref={virtuosoRef}
                    scrollerRef={(ref) => { scrollerRef.current = ref; }}
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
                        // 更新当前页
                        setCurrentPage(range.startIndex + 1);
                    }}
                    overscan={2}
                />
            </div>
        </div>
    );
};
