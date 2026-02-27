import React, { useCallback, useEffect, useRef, useState } from 'react';
import { PDFViewer } from './components/PDFViewer';
import { ChatPanel } from './components/Chat';
import { CompliancePanel } from './components/Compliance/CompliancePanel';
import { MultimodalAuditPanel } from './components/Audit/MultimodalAuditPanel';
import { Settings } from './components/Settings';
import { useDocumentStore } from './stores/documentStore';
import type { Document, PageOcrStatus, TabProgress } from './stores/documentStore';
import { useVectorSearch } from './hooks/useVectorSearch';
import type { HistoryDocumentItem } from './hooks/useVectorSearch';
import { sha256File } from './utils/hash';
import './App.css';

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

const isPdfFilename = (name: string): boolean => name.toLowerCase().endsWith('.pdf');
const isSupportedUploadFilename = (name: string): boolean => /\.(pdf|doc|docx)$/i.test(name);

const mapBackendDocument = (doc: any, fallbackName = ''): Document => ({
  id: String(doc?.id || ''),
  name: String(doc?.name || fallbackName || doc?.id || '未命名文档'),
  totalPages: Number(doc?.total_pages || 0),
  initialOcrRequiredPages: Array.isArray(doc?.initial_ocr_required_pages) ? doc.initial_ocr_required_pages : [],
  ocrRequiredPages: Array.isArray(doc?.ocr_required_pages) ? doc.ocr_required_pages : [],
  recognizedPages: Array.isArray(doc?.recognized_pages) ? doc.recognized_pages : [],
  pageOcrStatus: mapPageStatus(doc?.page_ocr_status),
  ocrMode: doc?.ocr_mode === 'full' ? 'full' : 'manual',
  thumbnails: Array.isArray(doc?.thumbnails) ? doc.thumbnails : [],
  sourceFormat: (doc?.source_format || 'pdf') as Document['sourceFormat'],
  convertedFrom: (doc?.converted_from || null) as Document['convertedFrom'],
  conversionStatus: (doc?.conversion_status || 'ok') as Document['conversionStatus'],
  conversionMs: typeof doc?.conversion_ms === 'number' ? doc.conversion_ms : null,
  conversionFailCount: typeof doc?.conversion_fail_count === 'number' ? doc.conversion_fail_count : 0,
  ocrTriggeredPages: typeof doc?.ocr_triggered_pages === 'number' ? doc.ocr_triggered_pages : 0,
  indexedChunks: typeof doc?.indexed_chunks === 'number' ? doc.indexed_chunks : 0,
  avgContextTokens: typeof doc?.avg_context_tokens === 'number' ? doc.avg_context_tokens : null,
  contextQueryCount: typeof doc?.context_query_count === 'number' ? doc.context_query_count : 0,
  textFallbackUsed: Boolean(doc?.text_fallback_used),
});

const progressLabel = (progress: TabProgress | null): string => {
  if (!progress) return '';
  if (progress.stage === 'extracting') return '解析中';
  if (progress.stage === 'embedding') return '索引中';
  if (progress.stage === 'ocr') return 'OCR';
  if (progress.stage === 'failed') return '失败';
  return '已完成';
};

type DocTabStatusKey =
  | 'extracting'
  | 'embedding'
  | 'ocr'
  | 'failed'
  | 'completed'
  | 'partial'
  | 'unrecognized'
  | 'no-ocr';

const countUniquePositivePages = (pages: number[] | undefined): number => {
  const uniq = new Set<number>();
  (pages || []).forEach((page) => {
    if (Number.isInteger(page) && page > 0) {
      uniq.add(page);
    }
  });
  return uniq.size;
};

const getDocTabStatus = (
  doc: Document,
  progress: TabProgress | null
): { key: DocTabStatusKey; label: string } => {
  if (progress?.stage === 'extracting' || progress?.stage === 'embedding' || progress?.stage === 'ocr' || progress?.stage === 'failed') {
    return {
      key: progress.stage,
      label: progressLabel(progress),
    };
  }

  const baseline = countUniquePositivePages(doc.initialOcrRequiredPages);
  const pending = countUniquePositivePages(doc.ocrRequiredPages);
  const totalTarget = Math.max(baseline, pending);

  if (totalTarget === 0) {
    return { key: 'no-ocr', label: '无需 OCR' };
  }
  if (pending === totalTarget) {
    return { key: 'unrecognized', label: '未识别' };
  }
  if (pending === 0) {
    return { key: 'completed', label: '已完成' };
  }
  return { key: 'partial', label: '部分完成' };
};

const getBackgroundOcrPages = (doc: Document): number[] => {
  if (Array.isArray(doc.ocrRequiredPages) && doc.ocrRequiredPages.length > 0) {
    return [...new Set(doc.ocrRequiredPages)].sort((a, b) => a - b);
  }

  const statusMap = doc.pageOcrStatus || {};
  return Object.entries(statusMap)
    .filter(([, status]) => status === 'unrecognized' || status === 'failed')
    .map(([page]) => Number(page))
    .filter((page) => Number.isInteger(page) && page > 0)
    .sort((a, b) => a - b);
};

interface UploadOneOptions {
  silent?: boolean;
}

interface OpenHistoryOptions {
  suppressMissingPdfAlert?: boolean;
}

function App() {
  const {
    tabsOrder,
    tabsByDocId,
    activeDocId,
    currentDocument,
    pdfUrl,
    rightPanelMode,
    activeProgress,
    openOrFocusTab,
    activateTab,
    closeTab,
    updateTabDocument,
    setTabPdfUrl,
    setTabMessages,
    setTabCompliance,
    setTabAudit,
    setTabProgress,
    setRightPanelMode,
  } = useDocumentStore();

  const {
    uploadDocument,
    getDocument,
    getPdfUrl,
    watchProgress,
    lookupDocument,
    listHistory,
    deleteDocument,
    attachPdf,
    getChatHistory,
    getComplianceHistory,
    getMultimodalAuditHistory,
    recognizePages,
    cancelOcr,
  } = useVectorSearch();

  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [historyDocs, setHistoryDocs] = useState<HistoryDocumentItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [pendingUploadFile, setPendingUploadFile] = useState<File | null>(null);
  const [batchUploadHint, setBatchUploadHint] = useState<string | null>(null);
  const [isAddMenuOpen, setIsAddMenuOpen] = useState(false);
  const [isHistoryModalOpen, setIsHistoryModalOpen] = useState(false);

  const uploadInputRef = useRef<HTMLInputElement>(null);
  const attachInputRef = useRef<HTMLInputElement>(null);
  const attachTargetRef = useRef<{ docId: string; sha256: string } | null>(null);
  const addMenuRef = useRef<HTMLDivElement>(null);

  const mainRef = useRef<HTMLElement>(null);
  const [leftWidth, setLeftWidth] = useState(60);
  const [isResizing, setIsResizing] = useState(false);

  const watchersRef = useRef<Map<string, () => void>>(new Map());
  const hydratedTabRef = useRef<Set<string>>(new Set());
  const urlDocHandledRef = useRef(false);

  const stopProgressWatch = useCallback((docId: string) => {
    const stop = watchersRef.current.get(docId);
    if (stop) {
      stop();
      watchersRef.current.delete(docId);
    }
  }, []);

  const refreshHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const items = await listHistory();
      setHistoryDocs(items);
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : '加载历史记录失败');
    } finally {
      setHistoryLoading(false);
    }
  }, [listHistory]);

  const ensureProgressWatch = useCallback((docId: string) => {
    if (watchersRef.current.has(docId)) {
      return;
    }

    const unwatch = watchProgress(docId, (progress) => {
      setTabProgress(docId, progress as TabProgress);

      if (progress.stage === 'completed' || progress.stage === 'failed') {
        stopProgressWatch(docId);
        void (async () => {
          const latestDoc = await getDocument(docId);
          if (latestDoc) {
            updateTabDocument(docId, mapBackendDocument(latestDoc));
          }
          const tab = useDocumentStore.getState().tabsByDocId[docId];
          if (!tab?.pdfUrl) {
            const candidate = getPdfUrl(docId);
            try {
              const resp = await fetch(candidate, { method: 'HEAD' });
              if (resp.ok) {
                setTabPdfUrl(docId, candidate);
              }
            } catch {
              // ignore
            }
          }
          await refreshHistory();
        })();
      }
    });

    watchersRef.current.set(docId, () => {
      unwatch();
      watchersRef.current.delete(docId);
    });
  }, [watchProgress, setTabProgress, stopProgressWatch, getDocument, updateTabDocument, getPdfUrl, setTabPdfUrl, refreshHistory]);

  useEffect(() => {
    void refreshHistory();
  }, [refreshHistory]);

  useEffect(() => {
    return () => {
      Array.from(watchersRef.current.values()).forEach((stop) => stop());
      watchersRef.current.clear();
    };
  }, []);

  useEffect(() => {
    if (!isAddMenuOpen) {
      return;
    }

    const handlePointerDown = (event: MouseEvent) => {
      if (addMenuRef.current && !addMenuRef.current.contains(event.target as Node)) {
        setIsAddMenuOpen(false);
      }
    };

    const handleEsc = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsAddMenuOpen(false);
      }
    };

    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleEsc);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleEsc);
    };
  }, [isAddMenuOpen]);

  useEffect(() => {
    if (!isHistoryModalOpen) {
      return;
    }

    const handleEsc = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsHistoryModalOpen(false);
      }
    };

    document.addEventListener('keydown', handleEsc);
    return () => {
      document.removeEventListener('keydown', handleEsc);
    };
  }, [isHistoryModalOpen]);

  useEffect(() => {
    tabsOrder.forEach((docId) => {
      if (hydratedTabRef.current.has(docId)) {
        return;
      }
      hydratedTabRef.current.add(docId);

      ensureProgressWatch(docId);

      void (async () => {
        const [doc, history, compliance, auditHistory] = await Promise.all([
          getDocument(docId),
          getChatHistory(docId),
          getComplianceHistory(docId),
          getMultimodalAuditHistory(docId),
        ]);

        if (doc) {
          updateTabDocument(docId, mapBackendDocument(doc));
        }
        setTabMessages(docId, history);
        if (compliance) {
          setTabCompliance(docId, {
            requirements: compliance.requirementsText,
            results: compliance.results,
            markdown: compliance.markdown,
          });
        }
        if (auditHistory) {
          setTabAudit(docId, {
            lastJobId: auditHistory.jobId,
            auditType: auditHistory.auditType,
            items: auditHistory.items,
            summary: auditHistory.summary,
            generatedAt: auditHistory.generatedAt,
            progress: {
              jobId: auditHistory.jobId,
              status: auditHistory.status,
              stage: auditHistory.status,
              current: 100,
              total: 100,
              message: '已加载审查历史。',
            },
          });
        }

        const tab = useDocumentStore.getState().tabsByDocId[docId];
        if (!tab?.pdfUrl) {
          const candidate = getPdfUrl(docId);
          try {
            const resp = await fetch(candidate, { method: 'HEAD' });
            if (resp.ok) {
              setTabPdfUrl(docId, candidate);
            }
          } catch {
            // ignore
          }
        }
      })();
    });

    if (!activeDocId && tabsOrder.length > 0) {
      activateTab(tabsOrder[0]);
    }
  }, [
    tabsOrder,
    activeDocId,
    activateTab,
    ensureProgressWatch,
    getDocument,
    getChatHistory,
    getComplianceHistory,
    getMultimodalAuditHistory,
    updateTabDocument,
    setTabMessages,
    setTabCompliance,
    setTabAudit,
    getPdfUrl,
    setTabPdfUrl,
  ]);

  const uploadOneFile = useCallback(async (file: File, ocrMode: 'manual' | 'full', options?: UploadOneOptions): Promise<boolean> => {
    if (!isSupportedUploadFilename(file.name)) {
      if (!options?.silent) {
        alert('仅支持 .pdf、.doc、.docx 文件');
      }
      return false;
    }

    try {
      const sha = await sha256File(file);
      const lookup = await lookupDocument(sha);

      if (lookup.exists && lookup.doc_id) {
        let url: string | null = null;
        if (isPdfFilename(file.name)) {
          url = URL.createObjectURL(file);
        } else {
          const candidate = getPdfUrl(lookup.doc_id);
          try {
            const resp = await fetch(candidate, { method: 'HEAD' });
            if (resp.ok) {
              url = candidate;
            }
          } catch {
            // ignore
          }
        }
        const doc = await getDocument(lookup.doc_id);
        if (doc) {
          openOrFocusTab(mapBackendDocument(doc, file.name), url);
          const [history, compliance, auditHistory] = await Promise.all([
            getChatHistory(lookup.doc_id),
            getComplianceHistory(lookup.doc_id),
            getMultimodalAuditHistory(lookup.doc_id),
          ]);
          setTabMessages(lookup.doc_id, history);
          if (compliance) {
            setTabCompliance(lookup.doc_id, {
              requirements: compliance.requirementsText,
              results: compliance.results,
              markdown: compliance.markdown,
            });
          }
          if (auditHistory) {
            setTabAudit(lookup.doc_id, {
              lastJobId: auditHistory.jobId,
              auditType: auditHistory.auditType,
              items: auditHistory.items,
              summary: auditHistory.summary,
              generatedAt: auditHistory.generatedAt,
              progress: {
                jobId: auditHistory.jobId,
                status: auditHistory.status,
                stage: auditHistory.status,
                current: 100,
                total: 100,
                message: '已加载审查历史。',
              },
            });
          }
        }
        ensureProgressWatch(lookup.doc_id);
        await refreshHistory();
        return true;
      }

      const docId = await uploadDocument(file, ocrMode);
      if (!docId) {
        throw new Error('上传失败');
      }

      const url = isPdfFilename(file.name) ? URL.createObjectURL(file) : null;
      openOrFocusTab(
        {
          id: docId,
          name: file.name,
          totalPages: 0,
          initialOcrRequiredPages: [],
          ocrRequiredPages: [],
          recognizedPages: [],
          pageOcrStatus: {},
          ocrMode,
          thumbnails: [],
          sourceFormat: file.name.toLowerCase().endsWith('.docx')
            ? 'docx'
            : file.name.toLowerCase().endsWith('.doc')
              ? 'doc'
              : 'pdf',
          convertedFrom: file.name.toLowerCase().endsWith('.pdf') ? null : (file.name.toLowerCase().endsWith('.docx') ? 'docx' : 'doc'),
          conversionStatus: file.name.toLowerCase().endsWith('.pdf') ? 'ok' : 'pending',
          conversionMs: null,
          conversionFailCount: 0,
          ocrTriggeredPages: 0,
          indexedChunks: 0,
          avgContextTokens: null,
          contextQueryCount: 0,
          textFallbackUsed: false,
        },
        url
      );
      setTabProgress(docId, {
        stage: 'extracting',
        current: 0,
        total: 100,
        message: '开始处理文档...',
        document_id: docId,
      });
      ensureProgressWatch(docId);
      await refreshHistory();
      return true;
    } catch (error) {
      if (!options?.silent) {
        alert(`上传失败：${error instanceof Error ? error.message : '未知错误'}`);
      }
      return false;
    }
  }, [
    lookupDocument,
    getDocument,
    openOrFocusTab,
    getChatHistory,
    getComplianceHistory,
    getMultimodalAuditHistory,
    setTabMessages,
    setTabCompliance,
    setTabAudit,
    ensureProgressWatch,
    refreshHistory,
    uploadDocument,
    getPdfUrl,
    setTabProgress,
  ]);

  const uploadFilesSerial = useCallback(async (files: File[]) => {
    if (files.length === 0) {
      return;
    }

    let successCount = 0;
    const failedFiles: string[] = [];

    for (let i = 0; i < files.length; i += 1) {
      const file = files[i];
      setBatchUploadHint(`正在上传 ${i + 1}/${files.length}：${file.name}`);
      const ok = await uploadOneFile(file, 'manual', { silent: true });
      if (ok) {
        successCount += 1;
      } else {
        failedFiles.push(file.name);
      }
    }

    setBatchUploadHint(null);

    if (failedFiles.length === 0) {
      alert(`批量上传完成：${successCount}/${files.length}`);
      return;
    }

    const preview = failedFiles.slice(0, 5).join(', ');
    const suffix = failedFiles.length > 5 ? ' ...' : '';
    alert(
      `批量上传完成：${successCount}/${files.length}，失败 ${failedFiles.length} 个\n失败文件：${preview}${suffix}`
    );
  }, [uploadOneFile]);

  const beginUploadChoice = useCallback((file: File) => {
    if (!isSupportedUploadFilename(file.name)) {
      alert('仅支持 .pdf、.doc、.docx 文件');
      return;
    }
    setPendingUploadFile(file);
  }, []);

  const handleUploadModeConfirm = useCallback((ocrMode: 'manual' | 'full') => {
    const file = pendingUploadFile;
    setPendingUploadFile(null);
    if (file) {
      void uploadOneFile(file, ocrMode);
    }
  }, [pendingUploadFile, uploadOneFile]);

  const openUploadPicker = useCallback(() => {
    setIsAddMenuOpen(false);
    uploadInputRef.current?.click();
  }, []);

  const toggleAddMenu = useCallback(() => {
    setIsAddMenuOpen((prev) => !prev);
  }, []);

  const openHistoryModal = useCallback(() => {
    setIsAddMenuOpen(false);
    setIsHistoryModalOpen(true);
    void refreshHistory();
  }, [refreshHistory]);

  const closeHistoryModal = useCallback(() => {
    setIsHistoryModalOpen(false);
  }, []);

  const handleFilesPicked = useCallback(async (input: File[]) => {
    const source = input;
    if (source.length === 0) {
      return;
    }

    const acceptedFiles = source.filter((file) => isSupportedUploadFilename(file.name));
    const ignoredCount = source.length - acceptedFiles.length;

    if (ignoredCount > 0) {
      alert(`已忽略 ${ignoredCount} 个不支持的文件`);
    }

    if (acceptedFiles.length === 0) {
      return;
    }

    if (acceptedFiles.length === 1) {
      beginUploadChoice(acceptedFiles[0]);
      return;
    }

    await uploadFilesSerial(acceptedFiles);
  }, [beginUploadChoice, uploadFilesSerial]);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    if (files.length === 0) {
      return;
    }
    void handleFilesPicked(files);
  }, [handleFilesPicked]);

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
    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      void handleFilesPicked(Array.from(files));
    }
  }, [handleFilesPicked]);

  const openHistoryInCurrentTab = useCallback(async (docId: string, options?: OpenHistoryOptions) => {
    const existing = tabsByDocId[docId];
    if (existing) {
      activateTab(docId);
    }

    let pdf: string | null = existing?.pdfUrl || null;
    if (!pdf) {
      const pdfCandidate = getPdfUrl(docId);
      try {
        const resp = await fetch(pdfCandidate, { method: 'HEAD' });
        if (resp.ok) {
          pdf = pdfCandidate;
        }
      } catch {
        // ignore
      }
    }

    try {
      const [doc, history, compliance, auditHistory] = await Promise.all([
        getDocument(docId),
        getChatHistory(docId),
        getComplianceHistory(docId),
        getMultimodalAuditHistory(docId),
      ]);

      if (doc) {
        openOrFocusTab(mapBackendDocument(doc), pdf);
      }
      setTabMessages(docId, history);
      if (compliance) {
        setTabCompliance(docId, {
          requirements: compliance.requirementsText,
          results: compliance.results,
          markdown: compliance.markdown,
        });
      }
      if (auditHistory) {
        setTabAudit(docId, {
          lastJobId: auditHistory.jobId,
          auditType: auditHistory.auditType,
          items: auditHistory.items,
          summary: auditHistory.summary,
          generatedAt: auditHistory.generatedAt,
          progress: {
            jobId: auditHistory.jobId,
            status: auditHistory.status,
            stage: auditHistory.status,
            current: 100,
            total: 100,
            message: '已加载审查历史。',
          },
        });
      }

      ensureProgressWatch(docId);

      if (!pdf && !options?.suppressMissingPdfAlert) {
        alert('该历史记录未保存 PDF。你可以继续问答，但网格预览需要先补传 PDF。');
      }
    } catch (error) {
      alert(error instanceof Error ? error.message : '打开历史记录失败');
    }
  }, [
    tabsByDocId,
    activateTab,
    getPdfUrl,
    getDocument,
    getChatHistory,
    getComplianceHistory,
    getMultimodalAuditHistory,
    openOrFocusTab,
    setTabMessages,
    setTabCompliance,
    setTabAudit,
    ensureProgressWatch,
  ]);

  useEffect(() => {
    if (urlDocHandledRef.current) {
      return;
    }

    urlDocHandledRef.current = true;
    const docId = new URLSearchParams(window.location.search).get('doc');
    if (!docId) {
      return;
    }

    void openHistoryInCurrentTab(docId, { suppressMissingPdfAlert: true });
  }, [openHistoryInCurrentTab]);

  const handleHistoryOpen = useCallback((docId: string) => {
    void openHistoryInCurrentTab(docId);
    setIsHistoryModalOpen(false);
  }, [
    openHistoryInCurrentTab,
  ]);

  const handleAttachPdfClick = useCallback((docId: string, sha256: string) => {
    attachTargetRef.current = { docId, sha256 };
    attachInputRef.current?.click();
  }, []);

  const handleAttachPdfSelect = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files ? e.target.files[0] : null;
    e.target.value = '';
    if (!file) return;
    const target = attachTargetRef.current;
    if (!target) return;

    try {
      const sha = await sha256File(file);
      if (sha.toLowerCase() !== target.sha256?.toLowerCase()) {
        alert('所选 PDF 与当前历史记录不匹配（SHA256 不一致）。');
        return;
      }

      const url = URL.createObjectURL(file);
      const doc = await getDocument(target.docId);
      if (doc) {
        openOrFocusTab(mapBackendDocument(doc), url);
      } else {
        setTabPdfUrl(target.docId, url);
        activateTab(target.docId);
      }

      const attached = await attachPdf(target.docId, file);
      if (!attached) {
        alert('本地 PDF 已加载，但保存到后端失败。');
        return;
      }

      const latestDoc = await getDocument(target.docId);
      if (latestDoc) {
        updateTabDocument(target.docId, mapBackendDocument(latestDoc));
      }
      await refreshHistory();
    } catch (error) {
      alert(error instanceof Error ? error.message : '补传 PDF 失败');
    }
  }, [
    getDocument,
    openOrFocusTab,
    setTabPdfUrl,
    activateTab,
    attachPdf,
    updateTabDocument,
    refreshHistory,
  ]);

  const handleDeleteHistoryDoc = useCallback(async (docId: string) => {
    const ok = window.confirm('确定删除该记录吗？这会删除后端保存的 PDF/OCR/向量索引/对话历史。');
    if (!ok) return;

    const deleted = await deleteDocument(docId);
    if (!deleted) {
      alert('删除失败');
      return;
    }

    stopProgressWatch(docId);
    if (tabsByDocId[docId]) {
      const closedUrl = closeTab(docId);
      if (closedUrl && closedUrl.startsWith('blob:')) {
        URL.revokeObjectURL(closedUrl);
      }
    }

    await refreshHistory();
  }, [deleteDocument, stopProgressWatch, tabsByDocId, closeTab, refreshHistory]);

  const handleCloseTab = useCallback(async (docId: string) => {
    await cancelOcr(docId);
    stopProgressWatch(docId);

    const closedUrl = closeTab(docId);
    if (closedUrl && closedUrl.startsWith('blob:')) {
      URL.revokeObjectURL(closedUrl);
    }
  }, [cancelOcr, stopProgressWatch, closeTab]);

  const handleRunBackgroundOcr = useCallback(async (docId: string) => {
    const tab = tabsByDocId[docId];
    if (!tab?.document) return;

    const pages = getBackgroundOcrPages(tab.document);
    if (pages.length === 0) {
      alert('当前文档没有待 OCR 页');
      return;
    }

    const result = await recognizePages(docId, pages);
    if (!result) {
      alert('提交后台 OCR 失败');
      return;
    }

    ensureProgressWatch(docId);
    setTabProgress(docId, {
      stage: 'ocr',
      current: 0,
      total: 100,
      message: typeof result.message === 'string' ? result.message : '已加入后台 OCR 队列',
      document_id: docId,
    });
  }, [tabsByDocId, recognizePages, ensureProgressWatch, setTabProgress]);

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
  }, []);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing || !mainRef.current) return;
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

  const renderHistoryList = () => {
    if (historyLoading) {
      return <div className="history-empty">加载中...</div>;
    }
    if (historyError) {
      return <div className="history-empty">{historyError}</div>;
    }
    if (historyDocs.length === 0) {
      return <div className="history-empty">暂无历史记录</div>;
    }

    return (
      <div className="history-list">
        {historyDocs.map((d) => (
          <div className="history-item" key={d.doc_id}>
            <div className="history-meta">
              <div className="history-name">{d.filename || d.doc_id}</div>
              <div className="history-sub">
                {d.created_at ? new Date(d.created_at).toLocaleString() : ''}
                {' | '}
                {d.total_pages || 0} 页
                {' | '}
                OCR:{d.ocr_required_pages?.length || 0}
                {' | '}
                {d.status}
              </div>
            </div>
            <div className="history-actions">
              <button
                className="history-btn"
                onClick={() => handleHistoryOpen(d.doc_id)}
              >
                打开
              </button>
              {d.has_pdf === false && (
                <button
                  className="history-btn secondary"
                  onClick={() => handleAttachPdfClick(d.doc_id, d.sha256)}
                >
                  补传 PDF
                </button>
              )}
              <button
                className="history-btn danger"
                onClick={() => {
                  void handleDeleteHistoryDoc(d.doc_id);
                }}
              >
                删除
              </button>
            </div>
          </div>
        ))}
      </div>
    );
  };

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-left">
          <h1>PDF 智能问答系统</h1>
          <span className="version">V6.0</span>
        </div>

        <div className="header-right">
          <button className="header-btn add-file-btn" onClick={openUploadPicker}>
            新增文件
          </button>
          {activeDocId && (
            <button
              className="header-btn close-doc-btn"
              onClick={() => {
                void handleCloseTab(activeDocId);
              }}
            >
              关闭当前标签
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
          {tabsOrder.length > 0 && (
            <div className="doc-tabs-bar">
              <div className="doc-tabs-scroll">
                {tabsOrder.map((docId) => {
                const tab = tabsByDocId[docId];
                if (!tab) return null;
                const active = docId === activeDocId;
                const pendingPages = getBackgroundOcrPages(tab.document).length;
                const tabStatus = getDocTabStatus(tab.document, tab.progress);

                return (
                  <div
                    key={docId}
                    className={`doc-tab-item ${active ? 'active' : ''}`}
                    onClick={() => activateTab(docId)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        activateTab(docId);
                      }
                    }}
                  >
                    <div className="doc-tab-main">
                      <span className="doc-tab-name" title={tab.document.name}>{tab.document.name}</span>
                      <span className={`doc-tab-status status-${tabStatus.key}`}>
                        {tabStatus.label}
                      </span>
                    </div>
                    <div className="doc-tab-actions">
                      <button
                        className="doc-tab-action"
                        title="后台 OCR"
                        disabled={pendingPages === 0}
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleRunBackgroundOcr(docId);
                        }}
                      >
                        OCR
                      </button>
                      <button
                        className="doc-tab-action close"
                        title="关闭标签"
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleCloseTab(docId);
                        }}
                      >
                        x
                      </button>
                    </div>
                  </div>
                );
                })}
              </div>

              <div className="doc-tab-add-wrap" ref={addMenuRef}>
                <button
                  className={`doc-tab-add-btn ${isAddMenuOpen ? 'open' : ''}`}
                  onClick={toggleAddMenu}
                  aria-haspopup="menu"
                  aria-expanded={isAddMenuOpen}
                  title="添加"
                >
                  +
                </button>
                {isAddMenuOpen && (
                  <div className="doc-tab-add-menu">
                    <button className="doc-tab-add-menu-item" onClick={openUploadPicker}>
                      上传文档
                    </button>
                    <button className="doc-tab-add-menu-item" onClick={openHistoryModal}>
                      历史记录
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}

          {batchUploadHint && <div className="batch-upload-hint">{batchUploadHint}</div>}

          {!currentDocument ? (
            <div className="upload-area">
              <div className="upload-icon">PDF</div>
              <h2>上传文档</h2>
              <p>拖拽文件到此处，或点击选择文件。</p>

              <button className="upload-btn" onClick={openUploadPicker}>
                选择文件
              </button>

              <div className="history-panel">
                <div className="history-title">历史记录</div>
                {renderHistoryList()}
              </div>
            </div>
          ) : !pdfUrl ? (
            <div className="pdf-placeholder missing-pdf-state">
              <p>该文档当前没有可预览的 PDF。</p>
              <p>请从历史记录补传匹配的 PDF，以启用网格预览和手动 OCR 选页。</p>
            </div>
          ) : (
            <PDFViewer pdfUrl={pdfUrl || undefined} onRecognizeQueued={ensureProgressWatch} />
          )}

          {activeProgress && activeProgress.stage !== 'completed' && activeProgress.stage !== 'failed' && (
            <div className="process-overlay">
              <div className="process-card">
                <div className="progress-spinner" />
                <div className="process-info">
                  <h3>正在处理文档...</h3>
                  <p>{activeProgress.message || '处理中...'}</p>
                </div>
              </div>
            </div>
          )}
        </div>

        <div
          className={`resizer ${isResizing ? 'resizing' : ''}`}
          onMouseDown={handleResizeStart}
        >
          <div className="resizer-handle" />
        </div>

        <div className="chat-section" style={{ width: `${100 - leftWidth}%` }}>
          <div className="right-panel-tabs">
            <button
              className={`tab-btn ${rightPanelMode === 'chat' ? 'active' : ''}`}
              onClick={() => setRightPanelMode('chat')}
            >
              智能问答
            </button>
            <button
              className={`tab-btn ${rightPanelMode === 'compliance' ? 'active' : ''}`}
              onClick={() => setRightPanelMode('compliance')}
            >
              合规检查
            </button>
            <button
              className={`tab-btn ${rightPanelMode === 'audit' ? 'active' : ''}`}
              onClick={() => setRightPanelMode('audit')}
            >
              专项审查
            </button>
          </div>

          <div className="right-panel-content">
            {rightPanelMode === 'chat'
              ? <ChatPanel />
              : rightPanelMode === 'compliance'
                ? <CompliancePanel />
                : <MultimodalAuditPanel />}
          </div>
        </div>
      </main>

      <input
        ref={uploadInputRef}
        type="file"
        accept=".pdf,.doc,.docx"
        multiple
        onChange={handleFileSelect}
        style={{ display: 'none' }}
      />

      <input
        ref={attachInputRef}
        type="file"
        accept=".pdf"
        onChange={handleAttachPdfSelect}
        style={{ display: 'none' }}
      />

      {pendingUploadFile && (
        <div className="upload-choice-overlay">
          <div className="upload-choice-modal">
            <h3>上传模式</h3>
            <p>上传后立即对全部页面执行 OCR 吗？</p>
            <div className="upload-choice-actions">
              <button autoFocus onClick={() => handleUploadModeConfirm('manual')}>
                否，仅上传
              </button>
              <button onClick={() => handleUploadModeConfirm('full')}>
                是，全部识别
              </button>
            </div>
          </div>
        </div>
      )}

      {isHistoryModalOpen && (
        <div
          className="history-modal-overlay"
          onClick={closeHistoryModal}
        >
          <div
            className="history-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="history-modal-header">
              <h3>历史记录</h3>
              <button
                className="history-modal-close"
                onClick={closeHistoryModal}
                aria-label="关闭历史弹窗"
                title="关闭"
              >
                x
              </button>
            </div>
            <div className="history-panel history-panel-modal">
              {renderHistoryList()}
            </div>
          </div>
        </div>
      )}

      <Settings isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />
    </div>
  );
}

export default App;






