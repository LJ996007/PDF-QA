import React, { useState, useCallback, useRef, useEffect } from 'react';
import { PDFViewer } from './components/PDFViewer';
import { ChatPanel } from './components/Chat';
import { CompliancePanel } from './components/Compliance/CompliancePanel';
import { Settings } from './components/Settings';
import { useDocumentStore } from './stores/documentStore';
import type { PageOcrStatus } from './stores/documentStore';
import { useVectorSearch } from './hooks/useVectorSearch';
import type { HistoryDocumentItem } from './hooks/useVectorSearch';
import { sha256File } from './utils/hash';
import './App.css';

function App() {
  const { currentDocument, setDocument, setMessages, clearDocument, setComplianceResults, setComplianceRequirements } = useDocumentStore();
  const { uploadDocument, getDocument, getPdfUrl, watchProgress, lookupDocument, listHistory, deleteDocument, attachPdf, getChatHistory, getComplianceHistory } = useVectorSearch();

  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [historyDocs, setHistoryDocs] = useState<HistoryDocumentItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const attachInputRef = useRef<HTMLInputElement>(null);
  const attachTargetRef = useRef<{ docId: string; sha256: string } | null>(null);
  const [pendingUploadFile, setPendingUploadFile] = useState<File | null>(null);

  // Tab 状态
  const [activeTab, setActiveTab] = useState<'chat' | 'compliance'>('chat');

  // 分隔条状态
  const [leftWidth, setLeftWidth] = useState(60); // 左侧宽度百分比
  const [isResizing, setIsResizing] = useState(false);
  const mainRef = useRef<HTMLElement>(null);

  const refreshHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const items = await listHistory();
      setHistoryDocs(items);
    } catch (e) {
      setHistoryError(e instanceof Error ? e.message : 'Failed to load history');
    } finally {
      setHistoryLoading(false);
    }
  }, [listHistory]);

  useEffect(() => {
    refreshHistory();
  }, [refreshHistory]);

  const mapPageStatus = (
    value: Record<string, PageOcrStatus> | Record<number, PageOcrStatus> | undefined
  ): Record<number, PageOcrStatus> => {
    const output: Record<number, PageOcrStatus> = {};
    Object.entries(value || {}).forEach(([key, status]) => {
      const page = Number(key);
      if (!Number.isNaN(page)) {
        output[page] = status;
      }
    });
    return output;
  };

  // 处理文件上传
  const handleFileUpload = useCallback(async (file: File, ocrMode: 'manual' | 'full') => {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      alert('请上传PDF文件');
      return;
    }

    setUploadProgress('正在检查历史缓存...');

    try {
      const sha = await sha256File(file);
      const lookup = await lookupDocument(sha);

      if (lookup.exists && lookup.doc_id && lookup.status === 'completed') {
        // Use cached index, no upload required.
        const url = URL.createObjectURL(file);
        if (pdfUrl && pdfUrl.startsWith('blob:')) URL.revokeObjectURL(pdfUrl);
        setPdfUrl(url);

        const doc = await getDocument(lookup.doc_id);
        if (doc) {
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
        }

        setUploadProgress(null);
        refreshHistory();
        return;
      }

      setUploadProgress('正在上传...');
      // 上传文件
      const docId = await uploadDocument(file, ocrMode);
      if (!docId) {
        throw new Error('上传失败');
      }

      // 创建本地URL用于预览
      const url = URL.createObjectURL(file);
      if (pdfUrl && pdfUrl.startsWith('blob:')) URL.revokeObjectURL(pdfUrl);
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
                  ocrRequiredPages: doc.ocr_required_pages || [],
                  recognizedPages: doc.recognized_pages || [],
                  pageOcrStatus: mapPageStatus(doc.page_ocr_status),
                  ocrMode: doc.ocr_mode || ocrMode,
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
  }, [uploadDocument, watchProgress, getDocument, setDocument, lookupDocument, pdfUrl, refreshHistory]);

  const beginUploadChoice = useCallback((file: File) => {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      alert('请上传PDF文件');
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
      beginUploadChoice(file);
    }
  }, [beginUploadChoice]);

  // 处理文件选择
  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      beginUploadChoice(file);
    }
    e.target.value = '';
  }, [beginUploadChoice]);

  // 关闭文档
  const handleCloseDocument = useCallback(() => {
    if (pdfUrl && pdfUrl.startsWith('blob:')) {
      URL.revokeObjectURL(pdfUrl);
    }
    setPdfUrl(null);
    clearDocument();
  }, [pdfUrl, clearDocument]);

  const handleOpenHistoryChat = useCallback(async (docId: string) => {
    try {
      if (pdfUrl && pdfUrl.startsWith('blob:')) URL.revokeObjectURL(pdfUrl);

      const pdfCandidate = getPdfUrl(docId);
      let pdf: string | null = null;
      try {
        const resp = await fetch(pdfCandidate, { method: 'HEAD' });
        if (resp.ok) {
          pdf = pdfCandidate;
        }
      } catch {
        // ignore
      }

      setPdfUrl(pdf);
      const doc = await getDocument(docId);
      if (doc) {
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
          pdf
        );
      }

      const history = await getChatHistory(docId);
      setMessages(history);

      const compliance = await getComplianceHistory(docId);
      if (compliance) {
        setComplianceRequirements(compliance.requirementsText);
        setComplianceResults(compliance.results, compliance.markdown);
      }

      if (!pdf) {
        alert('该历史记录未保存 PDF（可能是之前 KEEP_PDF=0 创建的）。如需后续自动加载 PDF，请点击该条目的“补齐PDF”。');
      }
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to open history');
    }
  }, [pdfUrl, getPdfUrl, getDocument, getChatHistory, getComplianceHistory, setDocument, setMessages, setComplianceRequirements, setComplianceResults]);


  const handleAttachPdfClick = useCallback((docId: string, sha256: string) => {
    attachTargetRef.current = { docId, sha256 };
    attachInputRef.current?.click();
  }, []);

  const handleDeleteHistoryDoc = useCallback(async (docId: string) => {
    const ok = window.confirm('确定要删除该记录吗？这会同时删除后台保存的PDF/OCR/向量索引/聊天历史。');
    if (!ok) return;

    const deleted = await deleteDocument(docId);
    if (!deleted) {
      alert('删除失败');
      return;
    }

    if (currentDocument?.id === docId) {
      handleCloseDocument();
    }

    refreshHistory();
  }, [deleteDocument, currentDocument, handleCloseDocument, refreshHistory]);

  const handleAttachPdfSelect = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    const target = attachTargetRef.current;
    if (!target) return;

    try {
      const sha = await sha256File(file);
      if (sha.toLowerCase() !== target.sha256?.toLowerCase()) {
        alert('选择的PDF与该历史记录不匹配（SHA256不同）。');
        return;
      }

      const url = URL.createObjectURL(file);
      if (pdfUrl && pdfUrl.startsWith('blob:')) URL.revokeObjectURL(pdfUrl);
      setPdfUrl(url);

      const doc = await getDocument(target.docId);
      if (doc) {
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
      }

      // Persist the PDF to backend so next time "打开(聊天)" can auto-load it.
      const attached = await attachPdf(target.docId, file);
      if (!attached) {
        alert('已加载本地PDF，但保存到后端失败。');
      } else {
        const latestDoc = await getDocument(target.docId);
        if (latestDoc) {
          setDocument(
            {
              id: latestDoc.id,
              name: latestDoc.name,
              totalPages: latestDoc.total_pages,
              ocrRequiredPages: latestDoc.ocr_required_pages || [],
              recognizedPages: latestDoc.recognized_pages || [],
              pageOcrStatus: mapPageStatus(latestDoc.page_ocr_status),
              ocrMode: latestDoc.ocr_mode || 'manual',
              thumbnails: latestDoc.thumbnails || [],
            },
            url
          );
        }
        refreshHistory();
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to attach PDF');
    }
  }, [pdfUrl, getDocument, setDocument, attachPdf, refreshHistory]);

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
          {!pdfUrl ? (
            <div className="upload-area">
              <div className="upload-icon">📤</div>
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

              <div className="history-panel">
                <div className="history-title">历史文档</div>
                {historyLoading ? (
                  <div className="history-empty">加载中...</div>
                ) : historyError ? (
                  <div className="history-empty">{historyError}</div>
                ) : historyDocs.length === 0 ? (
                  <div className="history-empty">暂无历史记录</div>
                ) : (
                  <div className="history-list">
                    {historyDocs.map((d) => (
                      <div className="history-item" key={d.doc_id}>
                        <div className="history-meta">
                          <div className="history-name">{d.filename || d.doc_id}</div>
                          <div className="history-sub">
                            {d.created_at ? new Date(d.created_at).toLocaleString() : ''}
                            {' · '}
                            {d.total_pages || 0}页
                            {' · '}
                            OCR:{d.ocr_required_pages?.length || 0}
                            {' · '}
                            {d.status}
                          </div>
                        </div>
                        <div className="history-actions">
                          <button
                            className="history-btn"
                            onClick={() => handleOpenHistoryChat(d.doc_id)}
                          >
                            打开(聊天)
                          </button>
                          {d.has_pdf === false && (
                            <button
                              className="history-btn secondary"
                              onClick={() => handleAttachPdfClick(d.doc_id, d.sha256)}
                            >
                              {'补齐PDF'}
                            </button>
                          )}
                          <button
                            className="history-btn danger"
                            onClick={() => handleDeleteHistoryDoc(d.doc_id)}
                          >
                            删除
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <input
                ref={attachInputRef}
                type="file"
                accept=".pdf"
                onChange={handleAttachPdfSelect}
                style={{ display: 'none' }}
              />
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

      {/* 上传模式弹窗 */}
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
