import React from 'react';
import type { PageOcrStatus } from '../../stores/documentStore';

interface PageGridItemProps {
    pageNumber: number;
    thumbnail?: string;
    status: PageOcrStatus;
    selected: boolean;
    onClick: (event: React.MouseEvent<HTMLDivElement>, pageNumber: number) => void;
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
    onClick,
}) => {
    const selectable = status !== 'recognized' && status !== 'processing';

    return (
        <div
            className={`page-grid-item ${selectable ? 'selectable' : 'non-selectable'} ${selected ? 'selected' : ''}`}
            data-page-number={pageNumber}
            onClick={(event) => onClick(event, pageNumber)}
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
                <span className={`page-grid-status status-${status}`}>{STATUS_LABEL[status]}</span>
            </div>
        </div>
    );
};
