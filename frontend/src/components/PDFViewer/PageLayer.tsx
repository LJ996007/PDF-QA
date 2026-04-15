import React, { useEffect, useRef, useState, memo } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import type { PDFLoadResult } from '../../hooks/usePdfLoader';
import type { TextChunk } from '../../stores/documentStore';
import { HighlightLayer } from './HighlightLayer';

export interface SearchHighlightItem {
    bbox: { x: number; y: number; w: number; h: number };
    text: string;
    isCurrent: boolean;
}

interface PageLayerProps {
    pageNumber: number;
    scale: number;
    pdfResult: PDFLoadResult;
    highlights: TextChunk[];
    searchHighlights?: SearchHighlightItem[];
}

export const PageLayer: React.FC<PageLayerProps> = memo(({
    pageNumber,
    scale,
    pdfResult,
    highlights,
    searchHighlights,
}) => {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const textLayerRef = useRef<HTMLDivElement>(null);
    const [pageSize, setPageSize] = useState({ width: 0, height: 0 });
    const [isRendered, setIsRendered] = useState(false);
    const renderTaskRef = useRef<pdfjsLib.RenderTask | null>(null);

    useEffect(() => {
        let isMounted = true;

        const renderPage = async () => {
            try {
                const page = await pdfResult.getPage(pageNumber);
                if (!isMounted) {
                    return;
                }

                const viewport = page.getViewport({ scale });
                setPageSize({
                    width: viewport.width,
                    height: viewport.height,
                });

                const canvas = canvasRef.current;
                if (!canvas) {
                    return;
                }

                const context = canvas.getContext('2d');
                if (!context) {
                    return;
                }

                const pixelRatio = Math.max(window.devicePixelRatio || 1, 2);
                canvas.width = viewport.width * pixelRatio;
                canvas.height = viewport.height * pixelRatio;
                canvas.style.width = `${viewport.width}px`;
                canvas.style.height = `${viewport.height}px`;
                context.scale(pixelRatio, pixelRatio);

                if (renderTaskRef.current) {
                    renderTaskRef.current.cancel();
                }

                renderTaskRef.current = page.render({
                    canvasContext: context,
                    viewport,
                });

                await renderTaskRef.current.promise;
                if (!isMounted) {
                    return;
                }

                const textContent = await page.getTextContent();
                const textLayer = textLayerRef.current;

                if (textLayer && isMounted) {
                    textLayer.innerHTML = '';

                    const { TextLayer } = await import('pdfjs-dist');
                    const textLayerInstance = new TextLayer({
                        textContentSource: textContent,
                        container: textLayer,
                        viewport,
                    });
                    await textLayerInstance.render();
                }

                setIsRendered(true);
            } catch (error) {
                if ((error as Error).name !== 'RenderingCancelledException') {
                    console.error('Page render error:', error);
                }
            }
        };

        renderPage();

        return () => {
            isMounted = false;
            if (renderTaskRef.current) {
                renderTaskRef.current.cancel();
            }
        };
    }, [pageNumber, scale, pdfResult]);

    return (
        <div
            className="page-wrapper"
            data-page-number={pageNumber}
            style={{
                width: pageSize.width || 'auto',
                height: pageSize.height || 400,
            }}
        >
            <canvas ref={canvasRef} className="canvas-layer" />

            <div
                ref={textLayerRef}
                className="text-layer"
                style={{
                    width: pageSize.width,
                    height: pageSize.height,
                }}
            />

            {isRendered && highlights.length > 0 && (
                <HighlightLayer
                    highlights={highlights}
                    pageWidth={pageSize.width}
                    pageHeight={pageSize.height}
                    scale={scale}
                />
            )}

            {isRendered && searchHighlights && searchHighlights.length > 0 && (
                <svg
                    className="search-highlight-layer"
                    width={pageSize.width}
                    height={pageSize.height}
                    viewBox={`0 0 ${pageSize.width} ${pageSize.height}`}
                >
                    {searchHighlights.map((item, i) => {
                        const x = item.bbox.x * scale;
                        const y = item.bbox.y * scale;
                        const w = Math.max(item.bbox.w * scale, 20);
                        const h = Math.max(item.bbox.h * scale, 10);
                        return (
                            <rect
                                key={i}
                                x={x} y={y}
                                width={Math.min(w, pageSize.width - x)}
                                height={Math.min(h, pageSize.height - y)}
                                rx={2} ry={2}
                                fill={item.isCurrent ? 'rgba(37,99,235,0.45)' : 'rgba(99,179,237,0.25)'}
                                stroke="none"
                                strokeWidth={1.5}
                            />
                        );
                    })}
                </svg>
            )}
        </div>
    );
});

PageLayer.displayName = 'PageLayer';
