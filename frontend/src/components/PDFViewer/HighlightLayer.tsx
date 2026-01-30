import React, { memo } from 'react';
import type { TextChunk } from '../../stores/documentStore';

interface HighlightLayerProps {
    highlights: TextChunk[];
    pageWidth: number;
    pageHeight: number;
    scale: number;
}

export const HighlightLayer: React.FC<HighlightLayerProps> = memo(({
    highlights,
    pageWidth,
    pageHeight,
    scale,
}) => {
    // 调试日志
    if (highlights.length > 0) {
        console.log('[HighlightLayer] Debug Info:', {
            pageWidth,
            pageHeight,
            scale,
            firstHighlight: highlights[0]?.bbox,
        });
    }

    return (
        <svg
            className="highlight-layer"
            width={pageWidth}
            height={pageHeight}
            viewBox={`0 0 ${pageWidth} ${pageHeight}`}
        >
            {highlights.map((chunk) => {
                // 验证bbox有效性
                if (!chunk.bbox ||
                    typeof chunk.bbox.x !== 'number' ||
                    typeof chunk.bbox.y !== 'number' ||
                    typeof chunk.bbox.w !== 'number' ||
                    typeof chunk.bbox.h !== 'number' ||
                    chunk.bbox.w <= 0 ||
                    chunk.bbox.h <= 0) {
                    return null;
                }

                // 后端返回的坐标已经是 72 DPI 的 PDF 坐标
                // 但 Y 坐标原点在图像左上角（与CSS相同），不需要翻转
                // 直接乘以当前缩放比例即可
                const rect = {
                    x: chunk.bbox.x * scale,
                    y: chunk.bbox.y * scale,
                    width: chunk.bbox.w * scale,
                    height: chunk.bbox.h * scale,
                };

                // 调试：打印坐标转换结果
                console.log('[HighlightLayer] Coordinate Transform:', {
                    input: { bbox: chunk.bbox, scale },
                    output: rect,
                });

                // 跳过超出页面范围的高亮
                if (rect.x < 0 || rect.y < 0 ||
                    rect.x > pageWidth || rect.y > pageHeight ||
                    rect.width <= 0 || rect.height <= 0) {
                    console.log('[HighlightLayer] Skipping out-of-bounds highlight');
                    return null;
                }

                // 提取引用数字
                const refNumber = chunk.refId?.replace('ref-', '') || '?';

                return (
                    <g key={chunk.id}>
                        {/* 高亮矩形 */}
                        <rect
                            className="highlight-rect"
                            x={rect.x}
                            y={rect.y}
                            width={Math.min(rect.width, pageWidth - rect.x)}
                            height={Math.min(rect.height, pageHeight - rect.y)}
                            rx={2}
                            ry={2}
                        >
                            <title>{chunk.content?.substring(0, 100) || ''}...</title>
                        </rect>

                        {/* 引用标签 */}
                        <g transform={`translate(${rect.x + rect.width - 20}, ${Math.max(0, rect.y - 8)})`}>
                            <rect
                                x={0}
                                y={0}
                                width={24}
                                height={18}
                                fill="#ff9800"
                                rx={4}
                                ry={4}
                            />
                            <text
                                x={12}
                                y={13}
                                textAnchor="middle"
                                fill="white"
                                fontSize={10}
                                fontWeight="bold"
                            >
                                {refNumber}
                            </text>
                        </g>
                    </g>
                );
            })}
        </svg>
    );
});

HighlightLayer.displayName = 'HighlightLayer';
