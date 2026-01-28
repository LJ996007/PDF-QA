/** PDF查看器组件 - 显示PDF并支持高亮 */
import React, { useState, useCallback, useRef, useEffect } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import type { Highlight } from '../types';

// 配置PDF.js worker
pdfjs.GlobalWorkerOptions.workerSrc = `//cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjs.version}/pdf.worker.min.js`;

interface PDFViewerProps {
  fileUrl: string | null;
  highlights: Highlight[];
  onHighlightRemove?: (page: number) => void;
}

export function PDFViewer({ fileUrl, highlights, onHighlightRemove }: PDFViewerProps) {
  const [numPages, setNumPages] = useState<number>(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [scale, setScale] = useState(1.2);
  const canvasRefs = useRef<Map<number, HTMLCanvasElement>>(new Map());

  const onDocumentLoadSuccess = useCallback(({ numPages }: { numPages: number }) => {
    setNumPages(numPages);
    setCurrentPage(1);
  }, []);

  const changePage = useCallback((offset: number) => {
    setCurrentPage((prev) => Math.min(Math.max(1, prev + offset), numPages));
  }, [numPages]);

  const zoomIn = useCallback(() => {
    setScale((prev) => Math.min(prev + 0.2, 3));
  }, []);

  const zoomOut = useCallback(() => {
    setScale((prev) => Math.max(prev - 0.2, 0.5));
  }, []);

  // 渲染高亮层
  const renderHighlight = useCallback((pageNumber: number, pageWidth: number, pageHeight: number) => {
    const highlight = highlights.find((h) => h.page === pageNumber);
    if (!highlight) return null;

    const { bbox } = highlight;
    const scaleX = pageWidth / 612; // 假设PDF标准宽度为612
    const scaleY = pageHeight / 792; // 假设PDF标准高度为792

    return (
      <div
        className="absolute pointer-events-none"
        style={{
          left: `${bbox.x0 * scaleX}px`,
          top: `${bbox.y0 * scaleY}px`,
          width: `${(bbox.x1 - bbox.x0) * scaleX}px`,
          height: `${(bbox.y1 - bbox.y0) * scaleY}px`,
          backgroundColor: 'rgba(255, 255, 0, 0.3)',
          border: '2px solid #facc15',
        }}
      />
    );
  }, [highlights]);

  return (
    <div className="flex flex-col h-full bg-gray-100">
      {/* 工具栏 */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-white">
        <h2 className="text-lg font-semibold text-gray-800">PDF 查看</h2>
        {fileUrl && (
          <div className="flex items-center space-x-2">
            {/* 缩放控制 */}
            <button
              onClick={zoomOut}
              className="p-1.5 hover:bg-gray-100 rounded transition-colors"
              title="缩小"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
              </svg>
            </button>
            <span className="text-sm text-gray-600 w-12 text-center">{Math.round(scale * 100)}%</span>
            <button
              onClick={zoomIn}
              className="p-1.5 hover:bg-gray-100 rounded transition-colors"
              title="放大"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
            </button>

            <div className="w-px h-6 bg-gray-300 mx-2" />

            {/* 翻页控制 */}
            <button
              onClick={() => changePage(-1)}
              disabled={currentPage <= 1}
              className="p-1.5 hover:bg-gray-100 rounded disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              title="上一页"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
              </svg>
            </button>
            <span className="text-sm text-gray-600 min-w-[60px] text-center">
              {currentPage} / {numPages || 0}
            </span>
            <button
              onClick={() => changePage(1)}
              disabled={currentPage >= numPages}
              className="p-1.5 hover:bg-gray-100 rounded disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              title="下一页"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
            </button>
          </div>
        )}
      </div>

      {/* PDF内容区 */}
      <div className="flex-1 overflow-auto flex items-start justify-center p-4">
        {!fileUrl ? (
          <div className="flex items-center justify-center w-full h-full text-gray-400">
            <div className="text-center">
              <svg className="mx-auto h-16 w-16 mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
              </svg>
              <p className="text-lg font-medium">暂无PDF文档</p>
              <p className="text-sm mt-2">请上传PDF文件开始使用</p>
            </div>
          </div>
        ) : (
          <div className="relative">
            <Document
              file={fileUrl}
              onLoadSuccess={onDocumentLoadSuccess}
              loading={
                <div className="flex items-center justify-center py-8">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
                </div>
              }
              error={
                <div className="text-red-500 text-center py-8">
                  加载PDF失败，请检查文件格式
                </div>
              }
            >
              <Page
                pageNumber={currentPage}
                scale={scale}
                renderTextLayer={false}
                renderAnnotationLayer={false}
                className="shadow-lg"
              />
            </Document>
            {/* 高亮层 - 简化实现，实际需要更精确的位置计算 */}
            {renderHighlight(currentPage, 612 * scale, 792 * scale)}
          </div>
        )}
      </div>
    </div>
  );
}
