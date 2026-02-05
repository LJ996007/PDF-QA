"""
技术合规性检查服务
"""
from typing import List, Dict, Any
import asyncio
from app.services.rag_engine import rag_engine
from app.services.llm_router import llm_router
from app.models.schemas import TextChunk

class ComplianceService:
    async def verify_requirements(self, doc_id: str, requirements: List[str], api_key: str = None) -> Dict[str, Any]:
        """
        验证多条技术要求
        返回: { results: [...], markdown: "表格字符串" }
        """
        results = []
        
        # 并发处理每一条要求
        tasks = [self._verify_single_requirement(doc_id, req, api_key) for req in requirements]
        results = await asyncio.gather(*tasks)
        
        # 添加ID
        for idx, item in enumerate(results):
            item['id'] = idx + 1
        
        # 生成 Markdown 表格
        markdown = self._format_as_markdown(results)
        
        return {
            "results": results,
            "markdown": markdown
        }
    
    def _format_as_markdown(self, results: List[Dict[str, Any]]) -> str:
        """将结果格式化为 Markdown 表格"""
        import re
        
        status_map = {
            "satisfied": "✅ 符合",
            "unsatisfied": "❌ 不符合", 
            "partial": "⚠️ 部分符合",
            "unknown": "❓ 未知",
            "error": "🔴 错误"
        }
        
        lines = [
            "| 序号 | 技术要求 | 应答情况 | 状态 |",
            "|:---:|:---|:---|:---:|"
        ]
        
        global_ref_cursor = 0
        
        for item in results:
            req = item.get('requirement', '')
            response = item.get('response', '')
            status = status_map.get(item.get('status', 'unknown'), '❓ 未知')
            
            # 引用列表
            refs = item.get('references', [])
            
            # 构建 block_id/ref_id 到 全局引用序号 的映射
            # 全局序号从 1 开始累加
            current_ref_map = {}
            
            for idx, r in enumerate(refs):
                global_id = global_ref_cursor + idx + 1
                
                # 更新 ref_id 为全局唯一ID，以便前端 handleRefClick 能找到它
                # 注意：这会修改原始 TextChunk 对象
                r.ref_id = f"ref-{global_id}"
                
                # 优先使用 block_id
                if getattr(r, 'block_id', None):
                    current_ref_map[f"[{r.block_id}]"] = global_id
                    # 同时也映射裸ID
                    current_ref_map[r.block_id] = global_id
                
                # 兼容 ref-N (后端生成的临时ID是 ref-1, ref-2...)
                # 即使有 block_id，模型也可能偶尔用 ref-N，所以总是建立映射
                local_ref_tag = f"ref-{idx + 1}"
                current_ref_map[f"[{local_ref_tag}]"] = global_id
                current_ref_map[local_ref_tag] = global_id
            
            # 更新全局游标
            global_ref_cursor += len(refs)

            # 替换 Response 中的引用标记
            # 1. 替换 [bXXXX] -> [ref-GlobalID]
            def replace_tag(match):
                tag_content = match.group(1) # b0001 or ref-1
                full_tag = f"[{tag_content}]"
                
                if full_tag in current_ref_map:
                    return f"[ref-{current_ref_map[full_tag]}]"
                
                # 尝试直接匹配 block_id
                if tag_content in current_ref_map:
                    return f"[ref-{current_ref_map[tag_content]}]"
                    
                # 如果没找到映射（可能是 ref-N 对应关系复杂），尝试回退
                # 此时 active_refs 和 tags 是顺序对应的
                # 但正则匹配是按文本顺序，active_refs 是按 tag 排序...
                # 简单起见，如果 response 中直接写了 [b0001]，我们希望能替换
                
                return match.group(0) # 保持原样

            # 这里的 active_refs 是依据 sorted unique tags 生成的
            # 如 response 有 [b0005] [b0001]
            # unique sorted: [b0001], [b0005]
            # active_refs: [chunk(b0001), chunk(b0005)]
            # global_ids: start+1 -> b0001, start+2 -> b0005
            
            # 我们重新构建一个精确映射：
            # 提取 response 中所有的 unique tags
            ref_tags = re.findall(r'\[(b\d+|ref-\d+)\]', response)
            unique_tags = sorted(list(set(ref_tags)))
            
            # 理论上 len(unique_tags) == len(refs)
            tag_to_global_id = {}
            for i, tag in enumerate(unique_tags):
                if i < len(refs):
                     # 计算 global ID
                     # 该 item 的 refs 起始 global id 是 global_ref_cursor - len(refs) + 1
                     # refs[i] 对应 unique_tags[i]
                     # 所以 unique_tags[i] 对应的 global_id 是 (global_ref_cursor - len(refs) + 1) + i
                     gid = (global_ref_cursor - len(refs)) + 1 + i
                     tag_to_global_id[tag] = gid
            
            def replace_precise(match):
                tag = match.group(1)
                if tag in tag_to_global_id:
                    return f"[ref-{tag_to_global_id[tag]}]"
                return match.group(0)
            
            response = re.sub(r'\[(b\d+|ref-\d+)\]', replace_precise, response)
            
            # 转义表格中的管道符
            req = req.replace('|', '\\|')
            response = response.replace('|', '\\|')
            
            lines.append(f"| {item['id']} | {req} | {response} | {status} |")
        
        return "\n".join(lines)

    async def _verify_single_requirement(self, doc_id: str, requirement: str, api_key: str = None) -> Dict[str, Any]:
        """验证单条要求"""
        try:
            # 1. 检索相关文段
            chunks = await rag_engine.retrieve(requirement, doc_id, top_k=10, api_key=api_key)
            
            if not chunks:
                return {
                    "requirement": requirement,
                    "status": "unknown",
                    "response": "在文档中未找到相关内容。",
                    "references": []
                }
            
            # 2. 构建验证Prompt
            # 使用 block_id (bXXXX) 如果存在，否则使用 ref-N
            def get_cid(c, i):
                return c.block_id if c.block_id else f"ref-{i+1}"
                
            context = "\n\n".join([f"[{get_cid(c, i)}] (第{c.page_number}页) {c.content}" for i, c in enumerate(chunks)])
            
            prompt = f"""你是一个技术文件核对专家。请根据以下文档片段，判断是否能够支撑技术要求。

判断标准：
1. 直接满足：文档中存在与技术要求完全一致的表述。
2. 分析满足：虽然没有完全一致的表述，但通过分析文档内容（如数据范围包含、单位换算、逻辑推断等），可以明确确认满足技术要求。

技术要求：{requirement}

文档片段：
---
{context}
---

请JSON格式返回结果：
{{
    "status": "satisfied" | "unsatisfied" | "partial" | "unknown",
    "reason": "简要说明理由。如果是通过分析确认满足，请在理由中说明推导逻辑。务必引用支持的段落编号（如[b0001]或[ref-1]），如果涉及多个片段，请全部列出。"
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
            active_refs = []
            
            # 提取reason中的引用标记: [b0001], [ref-1]
            # 匹配 [bXXXX] 或 [ref-N]
            ref_tags = re.findall(r'\[(b\d+|ref-\d+)\]', reason)
            unique_tags = sorted(list(set(ref_tags)))
            
            # 建立映射: block_id -> chunk, ref-id -> chunk
            chunk_map = {}
            for i, c in enumerate(chunks):
                if c.block_id:
                    chunk_map[c.block_id] = c
                chunk_map[f"ref-{i+1}"] = c
            
            for tag in unique_tags:
                if tag in chunk_map:
                    active_refs.append(chunk_map[tag])
            
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
