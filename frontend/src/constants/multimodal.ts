export type MultimodalProvider = 'zhipu' | 'qwen' | 'siliconflow' | 'mimo';

export interface MultimodalProviderDefaults {
    label: string;
    baseUrl: string;
    model: string;
}

export const MULTIMODAL_PROVIDER_DEFAULTS: Record<MultimodalProvider, MultimodalProviderDefaults> = {
    zhipu: {
        label: '智谱',
        baseUrl: 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
        model: 'GLM-4.6V-FlashX',
    },
    qwen: {
        label: 'Qwen',
        baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
        model: 'qwen3.5-flash',
    },
    siliconflow: {
        label: '硅基流动',
        baseUrl: 'https://api.siliconflow.cn/v1/chat/completions',
        model: 'Qwen/Qwen3.5-9B',
    },
    mimo: {
        label: 'MiMo',
        baseUrl: 'https://token-plan-sgp.xiaomimimo.com/v1/chat/completions',
        model: 'mimo-v2.5-pro',
    },
};

export const normalizeMultimodalProvider = (value: unknown): MultimodalProvider => {
    if (typeof value !== 'string') return 'zhipu';
    const normalized = value.trim().toLowerCase();
    if (normalized === 'qwen' || normalized === 'dashscope' || normalized === 'qwen_dashscope') {
        return 'qwen';
    }
    if (normalized === 'siliconflow' || normalized === 'silicon-flow' || normalized === 'silicon_flow') {
        return 'siliconflow';
    }
    if (normalized === 'mimo' || normalized === 'xiaomi' || normalized === 'xiaomi_mimo') {
        return 'mimo';
    }
    return 'zhipu';
};

export const getMultimodalDefaults = (provider: unknown): MultimodalProviderDefaults => {
    return MULTIMODAL_PROVIDER_DEFAULTS[normalizeMultimodalProvider(provider)];
};

export const resolveEffectiveMultimodalApiKey = (config: {
    multimodalProvider?: unknown;
    multimodalApiKey?: string;
    zhipuApiKey?: string;
    mimoApiKey?: string;
}): string => {
    const provider = normalizeMultimodalProvider(config.multimodalProvider);
    const multimodalApiKey = String(config.multimodalApiKey || '').trim();
    if (multimodalApiKey) {
        return multimodalApiKey;
    }
    if (provider === 'zhipu') {
        return String(config.zhipuApiKey || '').trim();
    }
    if (provider === 'mimo') {
        return String(config.mimoApiKey || '').trim();
    }
    return '';
};

export const isMultimodalConfigured = (config: {
    multimodalProvider?: unknown;
    multimodalApiKey?: string;
    multimodalBaseUrl?: string;
    multimodalModel?: string;
    zhipuApiKey?: string;
    mimoApiKey?: string;
}): boolean => {
    return Boolean(
        resolveEffectiveMultimodalApiKey(config)
        && String(config.multimodalBaseUrl || '').trim()
        && String(config.multimodalModel || '').trim()
    );
};
