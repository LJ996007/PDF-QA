import React, { useState } from 'react';
import { useDocumentStore } from '../../stores/documentStore';
import './Settings.css';

interface SettingsProps {
    isOpen: boolean;
    onClose: () => void;
}

export const Settings: React.FC<SettingsProps> = ({ isOpen, onClose }) => {
    const { config, updateConfig } = useDocumentStore();

    const [zhipuKey, setZhipuKey] = useState(config.zhipuApiKey);
    const [deepseekKey, setDeepseekKey] = useState(config.deepseekApiKey);
    const [ocrProvider] = useState<'baidu'>('baidu');
    const [baiduOcrUrl, setBaiduOcrUrl] = useState(config.baiduOcrUrl || '');
    const [baiduOcrToken, setBaiduOcrToken] = useState(config.baiduOcrToken || '');

    // Vision (image understanding) settings
    const [visionEnabled, setVisionEnabled] = useState(!!config.visionEnabled);
    const [visionBaseUrl, setVisionBaseUrl] = useState(config.visionBaseUrl || '');
    const [visionApiKey, setVisionApiKey] = useState(config.visionApiKey || '');
    const [visionModel, setVisionModel] = useState(config.visionModel || '');
    const [visionMaxPages, setVisionMaxPages] = useState<number>(config.visionMaxPages || 2);

    // 折叠状态
    const [isVisionExpanded, setIsVisionExpanded] = useState(false);
    const [isOcrExpanded, setIsOcrExpanded] = useState(false);

    const handleSave = () => {
        updateConfig({
            zhipuApiKey: zhipuKey,
            deepseekApiKey: deepseekKey,
            ocrProvider: 'baidu',
            baiduOcrUrl: baiduOcrUrl,
            baiduOcrToken: baiduOcrToken,
            visionEnabled: visionEnabled,
            visionBaseUrl: visionBaseUrl,
            visionApiKey: visionApiKey,
            visionModel: visionModel,
            visionMaxPages: Math.max(1, Number(visionMaxPages) || 2),
        });
        onClose();
    };

    if (!isOpen) return null;

    return (
        <div className="settings-overlay" onClick={onClose}>
            <div className="settings-modal" onClick={(e) => e.stopPropagation()}>
                <div className="settings-header">
                    <h2>⚙️ 设置</h2>
                    <button className="close-btn" onClick={onClose}>×</button>
                </div>

                <div className="settings-content">
                    {/* 当前模型状态 */}
                    <div className="current-config">
                        <div className="config-item">
                            <span className="config-label">LLM 模型</span>
                            <span className="config-value">
                                {config.deepseekApiKey ? '🔵 DeepSeek' : '🟢 智谱 GLM'}
                            </span>
                        </div>
                        <div className="config-item">
                            <span className="config-label">Embedding</span>
                            <span className="config-value">🟢 智谱 embedding-3</span>
                        </div>
                        <div className="config-item">
                            <span className="config-label">OCR 服务</span>
                            <span className="config-value">
                                {ocrProvider === 'baidu' ? '🟠 百度 PP-OCR' : '🟢 智谱 GLM-4V'}
                            </span>
                        </div>
                    </div>

                    {/* 智谱API Key */}
                    <div className="setting-group">
                        <label className="setting-label">
                            智谱API Key
                            <span className="setting-hint">用于Embedding和LLM推理</span>
                        </label>
                        <input
                            type="password"
                            className="setting-input"
                            value={zhipuKey}
                            onChange={(e) => setZhipuKey(e.target.value)}
                            placeholder="sk-xxxxxxxx"
                        />
                    </div>

                    {/* DeepSeek API Key */}
                    <div className="setting-group">
                        <label className="setting-label">
                            DeepSeek API Key
                            <span className="setting-hint">用于LLM推理（可选）</span>
                        </label>
                        <input
                            type="password"
                            className="setting-input"
                            value={deepseekKey}
                            onChange={(e) => setDeepseekKey(e.target.value)}
                            placeholder="sk-xxxxxxxx（可选）"
                        />
                    </div>

                    {/* Vision 设置 - 可折叠 */}
                    <div className="setting-section">
                        <button
                            className="section-toggle"
                            onClick={() => setIsVisionExpanded(!isVisionExpanded)}
                        >
                            <span className="section-title">
                                🖼️ 图片理解 (Vision)
                                <span className="section-badge">
                                    {visionEnabled ? '已启用' : '未启用'}
                                </span>
                            </span>
                            <span className={`toggle-icon ${isVisionExpanded ? 'expanded' : ''}`}>
                                ▶
                            </span>
                        </button>

                        {isVisionExpanded && (
                            <div className="section-content">
                                <div className="setting-group">
                                    <label className="setting-label">启用图片理解</label>
                                    <input
                                        type="checkbox"
                                        checked={visionEnabled}
                                        onChange={(e) => setVisionEnabled(e.target.checked)}
                                    />
                                    <span className="setting-hint" style={{ marginTop: '6px' }}>
                                        开启后，提问时后端会按需渲染页面截图并调用 OpenAI 兼容的视觉模型生成“视觉摘要”。
                                    </span>
                                </div>

                                <div className="setting-group">
                                    <label className="setting-label">Base URL</label>
                                    <input
                                        type="text"
                                        className="setting-input"
                                        value={visionBaseUrl}
                                        onChange={(e) => setVisionBaseUrl(e.target.value)}
                                        placeholder="https://your-host (可含 /v1)"
                                    />
                                </div>

                                <div className="setting-group">
                                    <label className="setting-label">API Key</label>
                                    <input
                                        type="password"
                                        className="setting-input"
                                        value={visionApiKey}
                                        onChange={(e) => setVisionApiKey(e.target.value)}
                                        placeholder="sk-..."
                                    />
                                </div>

                                <div className="setting-group">
                                    <label className="setting-label">Model</label>
                                    <input
                                        type="text"
                                        className="setting-input"
                                        value={visionModel}
                                        onChange={(e) => setVisionModel(e.target.value)}
                                        placeholder="gpt-4o-mini / qwen2-vl-..."
                                    />
                                </div>

                                <div className="setting-group">
                                    <label className="setting-label">每次最多分析页数</label>
                                    <input
                                        type="number"
                                        className="setting-input"
                                        value={visionMaxPages}
                                        min={1}
                                        max={10}
                                        onChange={(e) => {
                                            const v = parseInt(e.target.value || '2', 10);
                                            setVisionMaxPages(Number.isFinite(v) ? Math.max(1, v) : 2);
                                        }}
                                    />
                                </div>
                            </div>
                        )}
                    </div>

                    {/* OCR 设置 - 可折叠 */}
                    <div className="setting-section">
                        <button
                            className="section-toggle"
                            onClick={() => setIsOcrExpanded(!isOcrExpanded)}
                        >
                            <span className="section-title">
                                🔍 OCR 设置
                                <span className="section-badge">
                                    {ocrProvider === 'baidu' ? '百度 PP-OCR' : '智谱 GLM-4V'}
                                </span>
                            </span>
                            <span className={`toggle-icon ${isOcrExpanded ? 'expanded' : ''}`}>
                                ▶
                            </span>
                        </button>

                        {isOcrExpanded && (
                            <div className="section-content">
                                {/* OCR 提供商说明 */}
                                <div className="setting-group">
                                    <label className="setting-label">OCR 服务提供商</label>
                                    <div className="provider-selector">
                                        <button className="provider-btn active">
                                            百度 PP-OCR
                                        </button>
                                    </div>
                                    <p className="setting-hint" style={{ marginTop: '8px' }}>
                                        目前仅支持部署在AI Studio的PP-OCR服务
                                    </p>
                                </div>

                                {/* 百度OCR配置 */}
                                <div className="setting-group">
                                    <label className="setting-label">API地址</label>
                                    <input
                                        type="text"
                                        className="setting-input"
                                        value={baiduOcrUrl}
                                        onChange={(e) => setBaiduOcrUrl(e.target.value)}
                                        placeholder="https://xxx.aistudio-app.com/ocr"
                                    />
                                </div>
                                <div className="setting-group">
                                    <label className="setting-label">Token</label>
                                    <input
                                        type="password"
                                        className="setting-input"
                                        value={baiduOcrToken}
                                        onChange={(e) => setBaiduOcrToken(e.target.value)}
                                        placeholder="your-access-token"
                                    />
                                </div>
                            </div>
                        )}
                    </div>

                    <div className="settings-info">
                        <p>💡 API Key存储在本地浏览器中</p>
                    </div>
                </div>

                <div className="settings-footer">
                    <button className="cancel-btn" onClick={onClose}>取消</button>
                    <button className="save-btn" onClick={handleSave}>保存</button>
                </div>
            </div>
        </div>
    );
};
