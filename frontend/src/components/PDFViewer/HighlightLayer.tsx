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
    return (
        <svg
            className="highlight-layer"
            width={pageWidth}
            height={pageHeight}
            viewBox={`0 0 ${pageWidth} ${pageHeight}`}
        >
            {highlights.map((chunk) => {
                if (
                    !chunk.bbox ||
                    typeof chunk.bbox.x !== 'number' ||
                    typeof chunk.bbox.y !== 'number' ||
                    typeof chunk.bbox.w !== 'number' ||
                    typeof chunk.bbox.h !== 'number' ||
                    chunk.bbox.w <= 0 ||
                    chunk.bbox.h <= 0
                ) {
                    return null;
                }

                const rect = {
                    x: chunk.bbox.x * scale,
                    y: chunk.bbox.y * scale,
                    width: chunk.bbox.w * scale,
                    height: chunk.bbox.h * scale,
                };

                if (
                    rect.x < 0 ||
                    rect.y < 0 ||
                    rect.x > pageWidth ||
                    rect.y > pageHeight ||
                    rect.width <= 0 ||
                    rect.height <= 0
                ) {
                    return null;
                }

                const refNumStr = chunk.ref_id || chunk.refId || '';
                const refNumber = refNumStr.replace('ref-', '') || '?';

                return (
                    <g key={chunk.id}>
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

                        <g transform={`translate(${rect.x + rect.width + 6}, ${Math.max(0, rect.y - 8)})`}>
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
