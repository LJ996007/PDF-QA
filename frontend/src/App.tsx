import React, { useState, useCallback, useRef, useEffect } from 'react';
import { PDFViewer } from './components/PDFViewer';
import { ChatPanel } from './components/Chat';
import { CompliancePanel } from './components/Compliance/CompliancePanel';
import { Settings } from './components/Settings';
import { useDocumentStore } from './stores/documentStore';
import { useVectorSearch } from './hooks/useVectorSearch';
import './App.css';

function App() {
  const { currentDocument, setDocument, clearDocument } = useDocumentStore();
  const { uploadDocument, getDocument, watchProgress } = useVectorSearch();

  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  // Tab状态
  const [activeTab, setActiveTab] = useState<'chat' | 'compliance'>('chat');

  // 分隔条状态
  const [leftWidth, setLeftWidth] = useState(60); // 左侧宽度百分比
  const [isResizing, setIsResizing] = useState(false);
  const mainRef = useRef<HTMLElement>(null);

  // 处理文件上传
  const handleFileUpload = useCallback(async (file: File) => {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      alert('请上传PDF文件');
      return;
    }

    setUploadProgress('正在上传...');

    try {
      // 上传文件
      const docId = await uploadDocument(file);
      if (!docId) {
        throw new Error('上传失败');
      }

      // 创建本地URL用于预览
      const url = URL.createObjectURL(file);
      setPdfUrl(url);

      // 监听处理进度
      const unwatch = watchProgress(docId, (progress) => {
        setUploadProgress(`${progress.message} (${progress.current}%)`);

        if (progress.stage === 'completed') {
          setUploadProgress(null);
          // 获取文档信息
          getDocument(docId).then((doc) => {
            if (doc) {
              setDocument(
                {
                  id: doc.id,
                  name: doc.name,
                  totalPages: doc.total_pages,
                  ocrRequiredPages: doc.ocr_required_pages,
                  thumbnails: doc.thumbnails,
                },
                url
              );
            }
          });
          unwatch();
        } else if (progress.stage === 'failed') {
          setUploadProgress(`处理失败: ${progress.message}`);
          unwatch();
        }
      });
    } catch (error) {
      setUploadProgress(`上传失败: ${error instanceof Error ? error.message : '未知错误'}`);
    }
  }, [uploadDocument, watchProgress, getDocument, setDocument]);

  // 处理拖放
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
      handleFileUpload(file);
    }
  }, [handleFileUpload]);

  // 处理文件选择
  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      handleFileUpload(file);
    }
  }, [handleFileUpload]);

  // 关闭文档
  const handleCloseDocument = useCallback(() => {
    if (pdfUrl) {
      URL.revokeObjectURL(pdfUrl);
    }
    setPdfUrl(null);
    clearDocument();
  }, [pdfUrl, clearDocument]);

  // 处理分隔条拖拽
  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
  }, []);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing || !mainRef.current) return;

      const rect = mainRef.current.getBoundingClientRect();
      const newLeftWidth = ((e.clientX - rect.left) / rect.width) * 100;

      // 限制最小和最大宽度
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
      {/* 头部 */}
      <header className="app-header">
        <div className="header-left">
          <h1>📄 PDF智能问答系统</h1>
          <span className="version">V6.0</span>
        </div>

        <div className="header-right">
          {currentDocument && (
            <button className="header-btn close-doc-btn" onClick={handleCloseDocument}>
              关闭文档
            </button>
          )}
          <button className="header-btn settings-btn" onClick={() => setIsSettingsOpen(true)}>
            ⚙️ 设置
          </button>
        </div>
      </header>

      {/* 主内容区 */}
      <main className="app-main" ref={mainRef}>
        {/* PDF查看器 */}
        <div
          className={`pdf-section ${isDragging ? 'dragging' : ''}`}
          style={{ width: `${leftWidth}%` }}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {!currentDocument && !pdfUrl ? (
            <div className="upload-area">
              <div className="upload-icon">📁</div>
              <h2>上传PDF文档</h2>
              <p>拖放文件到此处，或点击选择文件</p>

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

          {/* 全局进度提示 */}
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

        {/* 可拖拽分隔条 */}
        <div
          className={`resizer ${isResizing ? 'resizing' : ''}`}
          onMouseDown={handleResizeStart}
        >
          <div className="resizer-handle" />
        </div>

        {/* 右侧面板 (对话/合规) */}
        <div className="chat-section" style={{ width: `${100 - leftWidth}%` }}>
          <div className="right-panel-tabs">
            <button
              className={`tab-btn ${activeTab === 'chat' ? 'active' : ''}`}
              onClick={() => setActiveTab('chat')}
            >
              💬 智能问答
            </button>
            <button
              className={`tab-btn ${activeTab === 'compliance' ? 'active' : ''}`}
              onClick={() => setActiveTab('compliance')}
            >
              📋 技术合规检查
            </button>
          </div>

          <div className="right-panel-content">
            {activeTab === 'chat' ? <ChatPanel /> : <CompliancePanel />}
          </div>
        </div>
      </main>

      {/* 设置弹窗 */}
      <Settings isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />
    </div>
  );
}

export default App;
