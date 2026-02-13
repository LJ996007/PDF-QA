import React, { useCallback, useEffect, useRef, useState } from 'react';
import { PDFViewer } from './components/PDFViewer';
import { ChatPanel } from './components/Chat';
import { CompliancePanel } from './components/Compliance/CompliancePanel';
import { Settings } from './components/Settings';
import { useDocumentStore } from './stores/documentStore';
import type { PageOcrStatus } from './stores/documentStore';
import { useVectorSearch } from './hooks/useVectorSearch';
import './App.css';

function App() {
    const { currentDocument, setDocument, clearDocument } = useDocumentStore();
    const { uploadDocument, getDocument, watchProgress } = useVectorSearch();

    const [pdfUrl, setPdfUrl] = useState<string | null>(null);
    const [isSettingsOpen, setIsSettingsOpen] = useState(false);
    const [uploadProgress, setUploadProgress] = useState<string | null>(null);
    const [isDragging, setIsDragging] = useState(false);
    const [activeTab, setActiveTab] = useState<'chat' | 'compliance'>('chat');

    const [leftWidth, setLeftWidth] = useState(60);
    const [isResizing, setIsResizing] = useState(false);
    const mainRef = useRef<HTMLElement>(null);

    const [pendingUploadFile, setPendingUploadFile] = useState<File | null>(null);

    const mapPageStatus = (value: Record<string, PageOcrStatus> | Record<number, PageOcrStatus> | undefined) => {
        const output: Record<number, PageOcrStatus> = {};
        Object.entries(value || {}).forEach(([key, status]) => {
            const page = Number(key);
            if (!Number.isNaN(page)) {
                output[page] = status;
            }
        });
        return output;
    };

    const handleFileUpload = useCallback(async (file: File, ocrMode: 'manual' | 'full') => {
        if (!file.name.toLowerCase().endsWith('.pdf')) {
            alert('请上传 PDF 文件');
            return;
        }

        setUploadProgress('正在上传...');

        try {
            const docId = await uploadDocument(file, ocrMode);
            if (!docId) {
                throw new Error('上传失败');
            }

            const url = URL.createObjectURL(file);
            setPdfUrl(url);

            const unwatch = watchProgress(docId, (progress) => {
                setUploadProgress(`${progress.message || '处理中'} (${progress.current}%)`);

                if (progress.stage === 'completed') {
                    getDocument(docId).then((doc) => {
                        if (!doc) {
                            return;
                        }

                        setDocument(
                            {
                                id: doc.id,
                                name: doc.name,
                                totalPages: doc.total_pages,
                                ocrRequiredPages: doc.ocr_required_pages || [],
                                recognizedPages: doc.recognized_pages || [],
                                pageOcrStatus: mapPageStatus(doc.page_ocr_status),
                                ocrMode: doc.ocr_mode || 'manual',
                                thumbnails: doc.thumbnails || [],
                            },
                            url
                        );

                        if ((doc.ocr_mode || ocrMode) === 'manual') {
                            setUploadProgress('文档已加载，可缩小页面后多选识别。');
                            setTimeout(() => setUploadProgress(null), 2500);
                        } else {
                            setUploadProgress(null);
                        }
                    });
                    unwatch();
                } else if (progress.stage === 'failed') {
                    setUploadProgress(`处理失败: ${progress.message || '未知错误'}`);
                    unwatch();
                }
            });
        } catch (error) {
            setUploadProgress(`上传失败: ${error instanceof Error ? error.message : '未知错误'}`);
        }
    }, [uploadDocument, watchProgress, getDocument, setDocument]);

    const beginUploadChoice = useCallback((file: File) => {
        if (!file.name.toLowerCase().endsWith('.pdf')) {
            alert('请上传 PDF 文件');
            return;
        }
        setPendingUploadFile(file);
    }, []);

    const handleUploadModeConfirm = useCallback((ocrMode: 'manual' | 'full') => {
        const file = pendingUploadFile;
        setPendingUploadFile(null);
        if (file) {
            handleFileUpload(file, ocrMode);
        }
    }, [pendingUploadFile, handleFileUpload]);

    const handleDragOver = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(true);
    }, []);

    const handleDragLeave = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
    }, []);

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);

        const file = e.dataTransfer.files[0];
        if (file) {
            beginUploadChoice(file);
        }
    }, [beginUploadChoice]);

    const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (file) {
            beginUploadChoice(file);
        }
        e.target.value = '';
    }, [beginUploadChoice]);

    const handleCloseDocument = useCallback(() => {
        if (pdfUrl) {
            URL.revokeObjectURL(pdfUrl);
        }
        setPdfUrl(null);
        clearDocument();
    }, [pdfUrl, clearDocument]);

    const handleResizeStart = useCallback((e: React.MouseEvent) => {
        e.preventDefault();
        setIsResizing(true);
    }, []);

    useEffect(() => {
        const handleMouseMove = (e: MouseEvent) => {
            if (!isResizing || !mainRef.current) {
                return;
            }

            const rect = mainRef.current.getBoundingClientRect();
            const newLeftWidth = ((e.clientX - rect.left) / rect.width) * 100;

            if (newLeftWidth >= 30 && newLeftWidth <= 80) {
                setLeftWidth(newLeftWidth);
            }
        };

        const handleMouseUp = () => {
            setIsResizing(false);
        };

        if (isResizing) {
            document.addEventListener('mousemove', handleMouseMove);
            document.addEventListener('mouseup', handleMouseUp);
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
        }

        return () => {
            document.removeEventListener('mousemove', handleMouseMove);
            document.removeEventListener('mouseup', handleMouseUp);
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        };
    }, [isResizing]);

    return (
        <div className="app">
            <header className="app-header">
                <div className="header-left">
                    <h1>PDF 智能问答系统</h1>
                    <span className="version">V6.0</span>
                </div>

                <div className="header-right">
                    {currentDocument && (
                        <button className="header-btn close-doc-btn" onClick={handleCloseDocument}>
                            关闭文档
                        </button>
                    )}
                    <button className="header-btn settings-btn" onClick={() => setIsSettingsOpen(true)}>
                        设置
                    </button>
                </div>
            </header>

            <main className="app-main" ref={mainRef}>
                <div
                    className={`pdf-section ${isDragging ? 'dragging' : ''}`}
                    style={{ width: `${leftWidth}%` }}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                >
                    {!currentDocument && !pdfUrl ? (
                        <div className="upload-area">
                            <div className="upload-icon">📄</div>
                            <h2>上传 PDF 文档</h2>
                            <p>拖拽文件到此处，或点击选择文件</p>

                            <label className="upload-btn">
                                选择文件
                                <input
                                    type="file"
                                    accept=".pdf"
                                    onChange={handleFileSelect}
                                    style={{ display: 'none' }}
                                />
                            </label>
                        </div>
                    ) : (
                        <PDFViewer pdfUrl={pdfUrl || undefined} />
                    )}

                    {uploadProgress && (
                        <div className="process-overlay">
                            <div className="process-card">
                                <div className="progress-spinner" />
                                <div className="process-info">
                                    <h3>正在处理文档</h3>
                                    <p>{uploadProgress}</p>
                                </div>
                            </div>
                        </div>
                    )}
                </div>

                <div className={`resizer ${isResizing ? 'resizing' : ''}`} onMouseDown={handleResizeStart}>
                    <div className="resizer-handle" />
                </div>

                <div className="chat-section" style={{ width: `${100 - leftWidth}%` }}>
                    <div className="right-panel-tabs">
                        <button
                            className={`tab-btn ${activeTab === 'chat' ? 'active' : ''}`}
                            onClick={() => setActiveTab('chat')}
                        >
                            智能问答
                        </button>
                        <button
                            className={`tab-btn ${activeTab === 'compliance' ? 'active' : ''}`}
                            onClick={() => setActiveTab('compliance')}
                        >
                            技术合规检查
                        </button>
                    </div>

                    <div className="right-panel-content">
                        {activeTab === 'chat' ? <ChatPanel /> : <CompliancePanel />}
                    </div>
                </div>
            </main>

            {pendingUploadFile && (
                <div className="upload-choice-overlay">
                    <div className="upload-choice-modal">
                        <h3>上传模式</h3>
                        <p>是否在上传后对全部页面执行 OCR 识别？</p>
                        <div className="upload-choice-actions">
                            <button autoFocus onClick={() => handleUploadModeConfirm('manual')}>
                                否，仅加载
                            </button>
                            <button onClick={() => handleUploadModeConfirm('full')}>
                                是，全部识别
                            </button>
                        </div>
                    </div>
                </div>
            )}

            <Settings isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />
        </div>
    );
}

export default App;
