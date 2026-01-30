import React, { useEffect, useRef, useState, memo } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import type { PDFLoadResult } from '../../hooks/usePdfLoader';
import type { TextChunk } from '../../stores/documentStore';
import { HighlightLayer } from './HighlightLayer';

interface PageLayerProps {
    pageNumber: number;
    scale: number;
    pdfResult: PDFLoadResult;
    highlights: TextChunk[];
}

export const PageLayer: React.FC<PageLayerProps> = memo(({
    pageNumber,
    scale,
    pdfResult,
    highlights,
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

                if (!isMounted) return;

                const viewport = page.getViewport({ scale });

                // 调试：打印页面尺寸和旋转
                const originalViewport = page.getViewport({ scale: 1 });
                console.log(`[PageLayer] Page ${pageNumber} dimensions:`, {
                    originalWidth: originalViewport.width,
                    originalHeight: originalViewport.height,
                    scaledWidth: viewport.width,
                    scaledHeight: viewport.height,
                    scale,
                    rotation: page.rotate,
                    view: page.view, // [x1, y1, x2, y2] MediaBox/CropBox
                });

                setPageSize({
                    width: viewport.width,
                    height: viewport.height,
                });

                // 渲染Canvas - 使用高分辨率渲染以保证清晰度
                const canvas = canvasRef.current;
                if (!canvas) return;

                const context = canvas.getContext('2d');
                if (!context) return;

                // 使用 devicePixelRatio 或最小 2x 来确保清晰度
                const pixelRatio = Math.max(window.devicePixelRatio || 1, 2);

                // 设置canvas实际像素尺寸（更高分辨率）
                canvas.width = viewport.width * pixelRatio;
                canvas.height = viewport.height * pixelRatio;

                // 设置canvas CSS显示尺寸（正常尺寸）
                canvas.style.width = `${viewport.width}px`;
                canvas.style.height = `${viewport.height}px`;

                // 缩放context以匹配高分辨率
                context.scale(pixelRatio, pixelRatio);

                // 取消之前的渲染任务
                if (renderTaskRef.current) {
                    renderTaskRef.current.cancel();
                }

                renderTaskRef.current = page.render({
                    canvasContext: context,
                    viewport,
                });

                await renderTaskRef.current.promise;

                if (!isMounted) return;

                // 渲染文本层
                const textContent = await page.getTextContent();
                const textLayer = textLayerRef.current;

                if (textLayer && isMounted) {
                    // 清空旧内容
                    textLayer.innerHTML = '';

                    // 使用PDF.js TextLayer类渲染文本层
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
                    console.error('渲染页面错误:', error);
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
            {/* Canvas层：视觉渲染 */}
            <canvas ref={canvasRef} className="canvas-layer" />

            {/* 文本层：支持选择复制 */}
            <div
                ref={textLayerRef}
                className="text-layer"
                style={{
                    width: pageSize.width,
                    height: pageSize.height,
                }}
            />

            {/* 高亮层 */}
            {isRendered && highlights.length > 0 && (
                <HighlightLayer
                    highlights={highlights}
                    pageWidth={pageSize.width}
                    pageHeight={pageSize.height}
                    scale={scale}
                />
            )}
        </div>
    );
});

PageLayer.displayName = 'PageLayer';
