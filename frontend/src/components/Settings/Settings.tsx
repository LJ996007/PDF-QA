import React, { useEffect, useState } from 'react';
import {
    MULTIMODAL_PROVIDER_DEFAULTS,
    getMultimodalDefaults,
    type MultimodalProvider,
} from '../../constants/multimodal';
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
    const [mimoKey, setMimoKey] = useState(config.mimoApiKey || '');
    const [llmProvider, setLlmProvider] = useState<'auto' | 'deepseek' | 'zhipu' | 'mimo'>(config.llmProvider || 'auto');
    const [multimodalProvider, setMultimodalProvider] = useState<MultimodalProvider>(config.multimodalProvider);
    const [multimodalApiKey, setMultimodalApiKey] = useState(config.multimodalApiKey || '');
    const [multimodalBaseUrl, setMultimodalBaseUrl] = useState(config.multimodalBaseUrl || getMultimodalDefaults(config.multimodalProvider).baseUrl);
    const [multimodalModel, setMultimodalModel] = useState(config.multimodalModel || getMultimodalDefaults(config.multimodalProvider).model);
    const [ocrProvider] = useState<'baidu'>('baidu');
    const [baiduOcrUrl, setBaiduOcrUrl] = useState(config.baiduOcrUrl || '');
    const [baiduOcrToken, setBaiduOcrToken] = useState(config.baiduOcrToken || '');

    // 折叠状态
    const [isTextLlmExpanded, setIsTextLlmExpanded] = useState(true);
    const [isOcrExpanded, setIsOcrExpanded] = useState(false);
    const [isMultimodalExpanded, setIsMultimodalExpanded] = useState(true);

    useEffect(() => {
        if (!isOpen) return;
        setZhipuKey(config.zhipuApiKey);
        setDeepseekKey(config.deepseekApiKey);
        setMimoKey(config.mimoApiKey || '');
        setLlmProvider(config.llmProvider || 'auto');
        setMultimodalProvider(config.multimodalProvider);
        setMultimodalApiKey(config.multimodalApiKey || '');
        setMultimodalBaseUrl(config.multimodalBaseUrl || getMultimodalDefaults(config.multimodalProvider).baseUrl);
        setMultimodalModel(config.multimodalModel || getMultimodalDefaults(config.multimodalProvider).model);
        setBaiduOcrUrl(config.baiduOcrUrl || '');
        setBaiduOcrToken(config.baiduOcrToken || '');
    }, [config, isOpen]);

    const applyProviderTemplate = (provider: MultimodalProvider) => {
        const defaults = getMultimodalDefaults(provider);
        setMultimodalProvider(provider);
        setMultimodalBaseUrl(defaults.baseUrl);
        setMultimodalModel(defaults.model);
    };

    const handleSave = () => {
        updateConfig({
            zhipuApiKey: zhipuKey,
            deepseekApiKey: deepseekKey,
            mimoApiKey: mimoKey,
            llmProvider,
            multimodalProvider,
            multimodalApiKey: multimodalApiKey,
            multimodalBaseUrl: multimodalBaseUrl,
            multimodalModel: multimodalModel,
            ocrProvider: 'baidu',
            baiduOcrUrl: baiduOcrUrl,
            baiduOcrToken: baiduOcrToken,
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
                                {config.llmProvider === 'mimo' && config.mimoApiKey
                                    ? '🟣 MiMo'
                                    : config.llmProvider === 'zhipu'
                                    ? '🟢 智谱 GLM'
                                    : config.llmProvider === 'deepseek' && config.deepseekApiKey
                                    ? '🔵 DeepSeek'
                                    : config.deepseekApiKey
                                    ? '🔵 DeepSeek'
                                    : config.mimoApiKey
                                    ? '🟣 MiMo'
                                    : '🟢 智谱 GLM'}
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
                        <div className="config-item">
                            <span className="config-label">多模态</span>
                            <span className="config-value">
                                {MULTIMODAL_PROVIDER_DEFAULTS[config.multimodalProvider].label} / {config.multimodalModel || getMultimodalDefaults(config.multimodalProvider).model}
                            </span>
                        </div>
                    </div>

                    {/* 文本模型设置（可折叠） */}
                    <div className="setting-section">
                        <button
                            className="section-toggle"
                            onClick={() => setIsTextLlmExpanded(!isTextLlmExpanded)}
                        >
                            <span className="section-title">
                                📝 文本模型
                                <span className="section-badge">
                                    {llmProvider === 'auto' ? '自动' : llmProvider === 'deepseek' ? 'DeepSeek' : llmProvider === 'mimo' ? 'MiMo' : '智谱 GLM'}
                                </span>
                            </span>
                            <span className={`toggle-icon ${isTextLlmExpanded ? 'expanded' : ''}`}>
                                ▶
                            </span>
                        </button>

                        {isTextLlmExpanded && (
                            <div className="section-content">
                                <div className="setting-group">
                                    <label className="setting-label">
                                        智谱API Key
                                        <span className="setting-hint">用于 Embedding、文本问答；智谱多模态留空时也会复用这里的 Key</span>
                                    </label>
                                    <input
                                        type="password"
                                        className="setting-input"
                                        value={zhipuKey}
                                        onChange={(e) => setZhipuKey(e.target.value)}
                                        placeholder="sk-xxxxxxxx"
                                    />
                                </div>

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

                                <div className="setting-group">
                                    <label className="setting-label">
                                        MiMo API Key
                                        <span className="setting-hint">小米 MiMo 大模型，用于LLM推理和多模态分析</span>
                                    </label>
                                    <input
                                        type="password"
                                        className="setting-input"
                                        value={mimoKey}
                                        onChange={(e) => setMimoKey(e.target.value)}
                                        placeholder="sk-xxxxxxxx（可选）"
                                    />
                                </div>

                                <div className="setting-group">
                                    <label className="setting-label">
                                        LLM 文本推理
                                        <span className="setting-hint">选择用于智能问答的文本模型</span>
                                    </label>
                                    <div className="provider-selector">
                                        {([
                                            { key: 'auto', label: '自动（按优先级）' },
                                            { key: 'deepseek', label: 'DeepSeek' },
                                            { key: 'mimo', label: 'MiMo' },
                                            { key: 'zhipu', label: '智谱 GLM' },
                                        ] as Array<{ key: typeof llmProvider; label: string }>).map((opt) => (
                                            <button
                                                key={opt.key}
                                                type="button"
                                                className={`provider-btn ${llmProvider === opt.key ? 'active' : ''}`}
                                                onClick={() => setLlmProvider(opt.key)}
                                            >
                                                {opt.label}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>

                    <div className="setting-section">
                        <button
                            className="section-toggle"
                            onClick={() => setIsMultimodalExpanded(!isMultimodalExpanded)}
                        >
                            <span className="section-title">
                                🖼 多模态模型
                                <span className="section-badge">
                                    {MULTIMODAL_PROVIDER_DEFAULTS[multimodalProvider].label}
                                </span>
                            </span>
                            <span className={`toggle-icon ${isMultimodalExpanded ? 'expanded' : ''}`}>
                                ▶
                            </span>
                        </button>

                        {isMultimodalExpanded && (
                            <div className="section-content">
                                <div className="setting-group">
                                    <label className="setting-label">
                                        供应商
                                        <span className="setting-hint">智能问答和专项审查共用这套多模态配置</span>
                                    </label>
                                    <div className="provider-selector">

                                        {(['zhipu', 'qwen', 'siliconflow', 'mimo'] as MultimodalProvider[]).map((provider) => (

                                            <button

                                                key={provider}

                                                type="button"

                                                className={`provider-btn ${multimodalProvider === provider ? 'active' : ''}`}

                                                onClick={() => applyProviderTemplate(provider)}

                                            >

                                                {MULTIMODAL_PROVIDER_DEFAULTS[provider].label}

                                            </button>

                                        ))}

                                    </div>
                                </div>

                                <div className="setting-group">
                                    <label className="setting-label">
                                        多模态 API Key
                                        <span className="setting-hint">
                                            {multimodalProvider === 'zhipu'
                                                ? '留空时将自动复用上方智谱 API Key'
                                                : '请输入当前供应商可用的多模态 API Key'}
                                        </span>
                                    </label>
                                    <input
                                        type="password"
                                        className="setting-input"
                                        value={multimodalApiKey}
                                        onChange={(e) => setMultimodalApiKey(e.target.value)}
                                        placeholder={multimodalProvider === 'zhipu' ? 'sk-xxxxxxxx（可留空复用智谱 Key）' : 'sk-xxxxxxxx'}
                                    />
                                </div>

                                <div className="setting-group">
                                    <label className="setting-label">
                                        Base URL
                                        <span className="setting-hint">切换供应商时会自动填入默认端点，也可手动覆盖</span>
                                    </label>
                                    <input
                                        type="text"
                                        className="setting-input"
                                        value={multimodalBaseUrl}
                                        onChange={(e) => setMultimodalBaseUrl(e.target.value)}
                                        placeholder={getMultimodalDefaults(multimodalProvider).baseUrl}
                                    />
                                </div>

                                <div className="setting-group">
                                    <label className="setting-label">
                                        模型名称
                                        <span className="setting-hint">可手动填写；默认会跟随供应商模板填入推荐模型</span>
                                    </label>
                                    <input
                                        type="text"
                                        className="setting-input"
                                        value={multimodalModel}
                                        onChange={(e) => setMultimodalModel(e.target.value)}
                                        placeholder={getMultimodalDefaults(multimodalProvider).model}
                                    />
                                </div>
                            </div>
                        )}
                    </div>

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
