"""大模型调用服务 - 兼容OpenAI格式的API"""
import re
from typing import List, Dict, Optional
from openai import OpenAI
from app.config import LLM_API_KEY, LLM_API_BASE, LLM_MODEL


class LLMService:
    """大模型服务类"""

    # 用于匹配答案中的引用标记，如 [ref:p1_para2]
    REF_PATTERN = re.compile(r'\[ref:([^\]]+)\]')

    def __init__(self):
        """初始化大模型客户端"""
        if not LLM_API_KEY:
            raise ValueError("LLM_API_KEY 未配置，请检查.env文件")

        self.client = OpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_API_BASE
        )
        self.model = LLM_MODEL

    def _build_prompt(self, question: str, context_paragraphs: List[Dict]) -> str:
        """
        构建带上下文的Prompt

        Args:
            question: 用户问题
            context_paragraphs: 相关段落列表

        Returns:
            完整的Prompt字符串
        """
        # 构建上下文部分
        context_parts = []
        for para in context_paragraphs:
            context_parts.append(
                f"[段落ID: {para['id']}] {para['text']}"
            )

        context = "\n".join(context_parts)

        # 完整Prompt
        prompt = f"""你是一个专业的文档分析助手。请严格基于以下PDF文档内容回答用户问题。

【文档内容】
{context}

【回答要求】
1. 只能使用上述文档内容回答，不要编造信息
2. 在回答中使用 [ref:段落ID] 格式标注信息来源
3. 如果文档中没有相关信息，请明确告知用户
4. 每个关键论点都要标注来源

【用户问题】
{question}"""

        return prompt

    def ask(
        self,
        question: str,
        context_paragraphs: List[Dict],
        temperature: float = 0.3,
        max_tokens: int = 2000
    ) -> Dict:
        """
        调用大模型生成答案

        Args:
            question: 用户问题
            context_paragraphs: 相关段落列表
            temperature: 温度参数（0-1，越低越确定性）
            max_tokens: 最大输出token数

        Returns:
            {
                "answer": "答案文本",
                "references": ["段落ID列表"]
            }
        """
        if not context_paragraphs:
            return {
                "answer": "抱歉，文档中没有找到与您的问题相关的内容。",
                "references": []
            }

        # 构建Prompt
        prompt = self._build_prompt(question, context_paragraphs)

        try:
            # 调用大模型API
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "你是一个专业的文档分析助手，基于提供的文档内容回答问题。"
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=temperature,
                max_tokens=max_tokens
            )

            # 提取答案
            answer = response.choices[0].message.content

            # 解析答案中的引用
            references = self._extract_references(answer)

            # 可选：清理答案中的引用标记（根据需求决定是否保留）
            # clean_answer = self.REF_PATTERN.sub('', answer).strip()

            return {
                "answer": answer,
                "references": references
            }

        except Exception as e:
            return {
                "answer": f"生成答案时出错: {str(e)}",
                "references": []
            }

    def _extract_references(self, answer: str) -> List[str]:
        """
        从答案中提取所有引用的段落ID

        Args:
            answer: 大模型返回的答案

        Returns:
            段落ID列表（去重）
        """
        matches = self.REF_PATTERN.findall(answer)
        # 去重并保持顺序
        seen = set()
        unique_refs = []
        for ref in matches:
            if ref not in seen:
                seen.add(ref)
                unique_refs.append(ref)
        return unique_refs

    def get_reference_details(
        self,
        reference_ids: List[str],
        all_paragraphs: List[Dict]
    ) -> List[Dict]:
        """
        获取引用的详细信息

        Args:
            reference_ids: 引用ID列表
            all_paragraphs: 所有相关段落的详细信息

        Returns:
            引用详情列表
        """
        # 构建ID到段落的映射
        para_map = {para["id"]: para for para in all_paragraphs}

        references = []
        for ref_id in reference_ids:
            if ref_id in para_map:
                para = para_map[ref_id]
                references.append({
                    "id": para["id"],
                    "page": para["page"],
                    "text": para["text"][:200] + "..." if len(para["text"]) > 200 else para["text"],
                    "bbox": para["bbox"]
                })

        return references


# 全局服务实例（单例模式）
_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    """获取LLM服务实例（单例）"""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
