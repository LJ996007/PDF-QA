import React from 'react';

import type { PageOcrStatus } from '../../stores/documentStore';

interface PageGridItemProps {
    pageNumber: number;
    thumbnail?: string;
    status: PageOcrStatus;
    selected: boolean;
    active: boolean;
    thumbnailWidth: number;
    onNavigate: (pageNumber: number) => void;
    onToggleSelect: (pageNumber: number) => void;
}

const STATUS_LABEL: Record<PageOcrStatus, string> = {
    unrecognized: '未识别',
    processing: '识别中',
    recognized: '已识别',
    failed: '失败',
};

export const PageGridItem: React.FC<PageGridItemProps> = ({
    pageNumber,
    thumbnail,
    status,
    selected,
    active,
    thumbnailWidth,
    onNavigate,
    onToggleSelect,
}) => {
    return (
        <div
            className={`page-grid-item ${selected ? 'selected' : ''} ${active ? 'active' : ''}`}
            data-page-number={pageNumber}
            style={{ '--thumbnail-width': `${thumbnailWidth}px` } as React.CSSProperties}
        >
            <div className="page-grid-item-toolbar">
                <button
                    type="button"
                    className={`page-grid-select-toggle ${selected ? 'selected' : ''}`}
                    onClick={(event) => {
                        event.stopPropagation();
                        onToggleSelect(pageNumber);
                    }}
                    aria-pressed={selected}
                    aria-label={`${selected ? '取消选择' : '选择'}第 ${pageNumber} 页`}
                    title={selected ? '取消选择' : '选择页面'}
                >
                    {selected ? '✓' : ''}
                </button>
                <span className={`page-grid-status status-${status}`}>{STATUS_LABEL[status]}</span>
            </div>

            <button
                type="button"
                className="page-grid-preview-btn"
                onClick={() => onNavigate(pageNumber)}
                title={`跳转到第 ${pageNumber} 页`}
            >
                <div className="page-grid-thumb-wrap">
                    {thumbnail ? (
                        <img src={thumbnail} alt={`Page ${pageNumber}`} className="page-grid-thumb" draggable={false} />
                    ) : (
                        <div className="page-grid-thumb placeholder">P{pageNumber}</div>
                    )}
                </div>

                <div className="page-grid-meta">
                    <span className="page-grid-page">第 {pageNumber} 页</span>
                </div>
            </button>
        </div>
    );
};
