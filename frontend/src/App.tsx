import { useState, useCallback, useEffect } from 'react';
import { PDFViewer } from './components/PDFViewer';
import { ChatPanel } from './components/ChatPanel';
import { ResizableSplit } from './components/ResizableSplit';
import { useChat } from './hooks/useChat';
import { usePdfHighlight } from './hooks/usePdfHighlight';
import { uploadPDF, checkLLMStatus, getLLMConfig, updateLLMConfig } from './services/api';
import type { Highlight } from './types';

function App() {
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [llmStatus, setLlmStatus] = useState<{ configured: boolean; provider: string; message: string } | null>(null);
  const [llmStatusChecked, setLlmStatusChecked] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [configLoading, setConfigLoading] = useState(false);
  const [configSaving, setConfigSaving] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);
  const [configSuccess, setConfigSuccess] = useState<string | null>(null);
  const [apiBase, setApiBase] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [apiKeySet, setApiKeySet] = useState(false);

  const { messages, isLoading, sendMessage, clearMessages } = useChat();
  const { highlights, addHighlight, clearHighlights } = usePdfHighlight();

  // 检查 LLM 状态
  useEffect(() => {
    const checkStatus = async () => {
      try {
        const status = await checkLLMStatus();
        setLlmStatus(status);
      } catch (err) {
        setLlmStatus({ configured: false, provider: 'error', message: '无法连接到后端服务' });
      } finally {
        setLlmStatusChecked(true);
      }
    };
    checkStatus();
  }, []);

  const loadConfig = useCallback(async () => {
    setConfigLoading(true);
    setConfigError(null);
    try {
      const cfg = await getLLMConfig();
      setApiBase(cfg.api_base || '');
      setModel(cfg.model || '');
      setApiKey('');
      setApiKeySet(cfg.api_key_set);
    } catch (err) {
      setConfigError('无法获取当前配置，请检查后端服务');
    } finally {
      setConfigLoading(false);
    }
  }, []);

  const openSettings = useCallback(async () => {
    setSettingsOpen(true);
    setConfigSuccess(null);
    await loadConfig();
  }, [loadConfig]);

  const handleSaveConfig = useCallback(async () => {
    setConfigError(null);
    setConfigSuccess(null);

    if (!apiBase.trim()) {
      setConfigError('API Base 不能为空');
      return;
    }
    if (!model.trim()) {
      setConfigError('模型名称不能为空');
      return;
    }

    setConfigSaving(true);
    try {
      const payload: { api_key?: string; api_base?: string; model?: string } = {
        api_base: apiBase.trim(),
        model: model.trim(),
      };
      if (apiKey.trim()) {
        payload.api_key = apiKey.trim();
      }
      const resp = await updateLLMConfig(payload);
      setApiKey('');
      setApiKeySet(resp.api_key_set);
      setConfigSuccess('配置已更新');
      const status = await checkLLMStatus();
      setLlmStatus(status);
    } catch (err) {
      setConfigError('配置更新失败，请检查后端日志');
    } finally {
      setConfigSaving(false);
    }
  }, [apiBase, apiKey, model]);

  const applyPreset = useCallback((preset: { api_base: string; model: string }) => {
    setApiBase(preset.api_base);
    setModel(preset.model);
  }, []);

  useEffect(() => {
    return () => {
      if (pdfUrl) {
        URL.revokeObjectURL(pdfUrl);
      }
    };
  }, [pdfUrl]);

  const handleFileUpload = useCallback(async (file: File) => {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setUploadError('请上传PDF文件');
      return;
    }

    setIsUploading(true);
    setUploadError(null);

    // 清理之前的PDF URL
    if (pdfUrl) {
      URL.revokeObjectURL(pdfUrl);
    }

    // 创建新的PDF URL
    const url = URL.createObjectURL(file);
    setPdfFile(file);
    setPdfUrl(url);
    setCurrentPage(1);
    clearMessages();
    clearHighlights();

    try {
      const response = await uploadPDF(file);
      setDocumentId(response.document_id);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : '上传失败');
      if (url) {
        URL.revokeObjectURL(url);
      }
      setPdfUrl(null);
      setPdfFile(null);
    } finally {
      setIsUploading(false);
    }
  }, [pdfUrl, clearMessages, clearHighlights]);

  const handleSendMessage = useCallback((question: string) => {
    if (documentId) {
      sendMessage(documentId, question);
    }
  }, [documentId, sendMessage]);

  const handleReferenceClick = useCallback(
    (
      refId: string,
      page: number,
      bbox: { x0: number; y0: number; x1: number; y1: number },
      pageWidth?: number,
      pageHeight?: number,
    ) => {
    const highlight: Highlight = {
      id: refId,
      page,
      bbox,
      color: 'rgba(255, 255, 0, 0.3)',
      page_width: pageWidth,
      page_height: pageHeight,
    };
    addHighlight(highlight);
    setCurrentPage(page);
  }, [addHighlight]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) {
      handleFileUpload(file);
    }
  }, [handleFileUpload]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
  }, []);

  return (
    <div className="h-screen flex flex-col">
      {/* 顶部栏 */}
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <svg className="w-8 h-8 text-blue-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <h1 className="text-xl font-bold text-gray-800">PDF 智能问答系统</h1>
        </div>
        <div className="flex items-center space-x-4">
          {/* LLM 状态提示 */}
          {llmStatusChecked && llmStatus && (
            <div className={`flex items-center px-3 py-1.5 rounded-lg text-sm ${
              llmStatus.configured
                ? 'bg-green-50 text-green-700 border border-green-200'
                : 'bg-yellow-50 text-yellow-700 border border-yellow-200'
            }`}>
              <svg className={`w-4 h-4 mr-2 ${llmStatus.configured ? 'text-green-500' : 'text-yellow-500'}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={
                  llmStatus.configured
                    ? "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
                    : "M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                } />
              </svg>
              <span>{llmStatus.message}</span>
            </div>
          )}
          <label className="inline-flex items-center px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 cursor-pointer transition-colors disabled:bg-gray-300 disabled:cursor-not-allowed">
            <svg className="w-5 h-5 mr-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
            </svg>
            {isUploading ? '上传中...' : '上传PDF'}
            <input
              type="file"
              accept=".pdf"
              onChange={(e) => e.target.files?.[0] && handleFileUpload(e.target.files[0])}
              disabled={isUploading}
              className="hidden"
            />
          </label>
          <button
            type="button"
            onClick={openSettings}
            className="inline-flex items-center px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
          >
            设置
          </button>
        </div>
      </header>

      {/* 主内容区 */}
      <main className="flex-1 overflow-hidden">
        {!pdfFile ? (
          <div
            className="h-full flex items-center justify-center bg-gray-50"
            onDrop={handleDrop}
            onDragOver={handleDragOver}
          >
            <div className="text-center">
              <div className="mx-auto h-24 w-24 mb-6 rounded-full bg-blue-50 flex items-center justify-center">
                <svg className="h-12 w-12 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                </svg>
              </div>
              <h2 className="text-2xl font-semibold text-gray-700 mb-2">上传PDF文档开始使用</h2>
              <p className="text-gray-500 mb-6">点击上方按钮或拖拽PDF文件到此处</p>
              {uploadError && (
                <p className="text-red-500 text-sm">{uploadError}</p>
              )}
            </div>
          </div>
        ) : (
          <ResizableSplit
            left={
              <PDFViewer
                fileUrl={pdfUrl}
                highlights={highlights}
                currentPage={currentPage}
                onPageChange={setCurrentPage}
              />
            }
            right={
              <ChatPanel
                documentId={documentId}
                messages={messages}
                isLoading={isLoading}
                onSendMessage={handleSendMessage}
                onReferenceClick={handleReferenceClick}
              />
            }
          />
        )}
      </main>

      {settingsOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="w-full max-w-xl rounded-xl bg-white p-6 shadow-xl">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-gray-800">大模型设置</h3>
              <button
                type="button"
                onClick={() => setSettingsOpen(false)}
                className="text-gray-400 hover:text-gray-600"
              >
                ×
              </button>
            </div>

            <div className="mb-4 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => applyPreset({ api_base: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4' })}
                className="px-3 py-1.5 text-sm bg-gray-100 rounded hover:bg-gray-200"
              >
                智谱 GLM-4
              </button>
              <button
                type="button"
                onClick={() => applyPreset({ api_base: 'https://api.deepseek.com/v1', model: 'deepseek-chat' })}
                className="px-3 py-1.5 text-sm bg-gray-100 rounded hover:bg-gray-200"
              >
                DeepSeek
              </button>
              <button
                type="button"
                onClick={() => applyPreset({ api_base: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' })}
                className="px-3 py-1.5 text-sm bg-gray-100 rounded hover:bg-gray-200"
              >
                通义千问
              </button>
              <button
                type="button"
                onClick={() => applyPreset({ api_base: 'https://api.openai.com/v1', model: 'gpt-4o-mini' })}
                className="px-3 py-1.5 text-sm bg-gray-100 rounded hover:bg-gray-200"
              >
                OpenAI
              </button>
            </div>

            {configLoading ? (
              <div className="py-6 text-center text-gray-500">加载配置中...</div>
            ) : (
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">API Base</label>
                  <input
                    type="text"
                    value={apiBase}
                    onChange={(e) => setApiBase(e.target.value)}
                    placeholder="https://api.example.com/v1"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">模型名称</label>
                  <input
                    type="text"
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    placeholder="例如 glm-4 / deepseek-chat / gpt-4o-mini"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">API Key</label>
                  <input
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder={apiKeySet ? '已设置（留空不修改）' : '请输入 API Key'}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              </div>
            )}

            {configError && (
              <div className="mt-4 text-sm text-red-600">{configError}</div>
            )}
            {configSuccess && (
              <div className="mt-4 text-sm text-green-600">{configSuccess}</div>
            )}

            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setSettingsOpen(false)}
                className="px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleSaveConfig}
                disabled={configSaving || configLoading}
                className="px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:bg-gray-300 disabled:cursor-not-allowed"
              >
                {configSaving ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
