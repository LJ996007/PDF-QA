/**
 * 提示词模板定义
 * 所有提示词都可由用户自定义编辑
 */

export interface PromptTemplate {
    id: string;
    name: string;              // 用户可编辑的标题
    description: string;       // 用户可编辑的描述
    content: string;           // 用户完全自定义的提示词内容
    createdAt: Date;
    updatedAt: Date;
}

// 示例模板定义（仅用于初始化和恢复）
export const EXAMPLE_PROMPTS: Omit<PromptTemplate, 'id' | 'createdAt' | 'updatedAt'>[] = [
    {
        name: '默认模式',
        description: '全面详细的回答方式',
        content: '', // 初始内容为空，由用户自己填写
    },
    {
        name: '简洁模式',
        description: '简短直接的回答',
        content: '',
    },
    {
        name: '详细模式',
        description: '深入分析，提供背景和细节',
        content: '',
    },
    {
        name: '结构化模式',
        description: '分点列举，条理清晰',
        content: '',
    },
    {
        name: '专业模式',
        description: '术语准确，适合技术文档',
        content: '',
    },
];

// 生成完整的示例提示词
export function createExamplePrompts(): PromptTemplate[] {
    return EXAMPLE_PROMPTS.map((example, index) => ({
        ...example,
        id: `example_${index + 1}`,
        createdAt: new Date(),
        updatedAt: new Date(),
    }));
}

// 获取提示词（按ID）
export function getPromptById(id: string, prompts: PromptTemplate[]): PromptTemplate | undefined {
    return prompts.find(p => p.id === id);
}

// 默认提示词模板（当用户选择空内容提示词时的fallback）
export const DEFAULT_PROMPT_TEMPLATE = `你是一个严谨的文档问答助手。请仅基于给定文档片段作答，不得臆测。

文档片段：
---
{context}
---

问题：{question}

请严格使用 Markdown 输出，并按以下四段结构回答（标题必须完全一致）：
### 结论
- 先给出一句最直接结论，并附引用 [ref-N]。

### 逐项核对
- 按“要点1 / 要点2 / 要点3 ...”逐条核对。
- 每条都要写清楚证据和对应引用 [ref-N]。
- 若问题包含多个对象（如多个证书/条款），必须逐一覆盖，不得遗漏。

### 风险提示
- 仅列出与证据相关的不确定性、时效性或缺失信息。
- 若无明显风险，写“未发现额外风险”，并附引用 [ref-N]。

### 引用说明
- 用 1-2 句话说明引用覆盖范围，不新增事实。
- 只使用上文出现过的 [ref-N]。

硬性规则：
1. 每句事实性陈述后都必须带 [ref-N]。
2. 只允许使用给定文档片段中的引用编号，不得虚构编号。
3. 若证据不足，明确写“根据现有片段无法确认”，并给出最接近的引用 [ref-N]。
4. 不要输出 JSON，不要输出额外标题。`;
