"""
技术合规性检查服务
"""
from typing import List, Dict, Any
import asyncio
from app.services.rag_engine import rag_engine
from app.services.llm_router import llm_router
from app.models.schemas import TextChunk

class ComplianceService:
    async def verify_requirements(self, doc_id: str, requirements: List[str], api_key: str = None) -> List[Dict[str, Any]]:
        """
        验证多条技术要求
        """
        results = []
        
        # 并发处理每一条要求
        tasks = [self._verify_single_requirement(doc_id, req, api_key) for req in requirements]
        results = await asyncio.gather(*tasks)
        
        # 添加ID
        for idx, item in enumerate(results):
            item['id'] = idx + 1
            
        return results

    async def _verify_single_requirement(self, doc_id: str, requirement: str, api_key: str = None) -> Dict[str, Any]:
        """验证单条要求"""
        try:
            # 1. 检索相关文段
            chunks = await rag_engine.retrieve(requirement, doc_id, top_k=5, api_key=api_key)
            
            if not chunks:
                return {
                    "requirement": requirement,
                    "status": "unknown",
                    "response": "在文档中未找到相关内容。",
                    "references": []
                }
            
            # 2. 构建验证Prompt
            context = "\n\n".join([f"[ref-{i+1}] {c.content}" for i, c in enumerate(chunks)])
            
            prompt = f"""你是一个技术合规性审核专家。请根据以下文档片段，判断技术要求是否满足。

技术要求：{requirement}

文档片段：
---
{context}
---

请JSON格式返回结果：
{{
    "status": "satisfied" | "unsatisfied" | "partial" | "unknown",
    "reason": "简要说明理由，引用支持的段落编号如[ref-1]"
}}
注意：状态必须是 strictly satisfied/unsatisfied/partial/unknown 之一。
"""
            
            messages = [{"role": "user", "content": prompt}]
            
            # 3. 调用LLM
            response = await llm_router.chat_completion(messages, api_key=api_key, json_mode=True)
            
            import json
            import re
            
            content = response.choices[0].message.content
            # 清理 Markdown 代码块
            if "```json" in content:
                content = content.replace("```json", "").replace("```", "")
            
            result_data = json.loads(content)
            
            status = result_data.get("status", "unknown")
            reason = result_data.get("reason", "无法判断")
            
            # 提取引用
            # 简单逻辑：如果reason包含了[ref-N]，则认为引用了该chunk
            active_refs = []
            
            # 提取reason中的引用标记
            ref_indices = [int(x) for x in re.findall(r'\[ref-(\d+)\]', reason)]
            unique_indices = sorted(list(set(ref_indices)))
            
            for idx in unique_indices:
                if 1 <= idx <= len(chunks):
                    # 转换 chunk -> 这里的ref-N是临时的5个，需要映射回chunk的信息
                    # 前端需要 standard references
                    chunk = chunks[idx-1]
                    # 注意：前端需要的ref格式在CompliancePanel里有定义，
                    # 但我们这里返回的是 TextChunk 对象 (在JSON里会被serialize)
                    # 我们直接返回TextChunk对象即可，FastAPI会自动序列化
                    active_refs.append(chunk)
            
            # 如果LLM没引用但确实satisfied，也许应该把top1 ref加上？
            # 暂时只信任LLM的引用
            
            return {
                "requirement": requirement,
                "status": status,
                "response": reason,
                "references": active_refs
            }
            
        except Exception as e:
            print(f"Error checking requirement '{requirement}': {e}")
            return {
                "requirement": requirement,
                "status": "error",
                "response": f"检查出错: {str(e)}",
                "references": []
            }

compliance_service = ComplianceService()
