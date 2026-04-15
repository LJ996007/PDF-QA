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

请直接回答用户问题，可自然分段或按需列点，但不要输出“逐项核对”“风险提示”“引用说明”等固定栏目。

硬性规则：
1. 每句事实性陈述后都必须带 [ref-N]。
2. 只允许使用给定文档片段中的引用编号，不得虚构编号。
3. 若证据不足，必须明确写“根据现有片段无法确认。”
4. 不要输出 JSON，不要追加与问题无关的模板化标题。`;
