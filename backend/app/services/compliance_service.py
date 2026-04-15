"""
技术合规性检查服务
"""
from typing import List, Dict, Any
import asyncio
from app.services.rag_engine import rag_engine
from app.services.llm_router import llm_router
from app.models.schemas import TextChunk

class ComplianceService:
    async def verify_requirements(
        self,
        doc_id: str,
        requirements: List[str],
        api_key: str = None,
        allowed_pages: List[int] | None = None,
    ) -> Dict[str, Any]:
        """
        验证多条技术要求
        返回: { results: [...], markdown: "表格字符串" }
        """
        results = []
        
        # 并发处理每一条要求
        tasks = [self._verify_single_requirement(doc_id, req, api_key, allowed_pages) for req in requirements]
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
        """将结果格式化为 Markdown 表格 + 不符合项详情"""
        import re

        status_map = {
            "satisfied": "✅ 符合",
            "unsatisfied": "❌ 不符合",
            "partial": "⚠️ 部分符合",
            "unknown": "❓ 未知",
            "error": "🔴 错误"
        }

        global_ref_cursor = 0

        # 第一遍：分配全局 ref_id，并为每个 item 构建 tag->global_id 映射
        processed_items = []
        for item in results:
            response = item.get('response', '')
            gaps = item.get('gaps', '')
            combined_text = response + " " + gaps
            refs = item.get('references', [])

            # 分配全局 ref_id
            for idx, r in enumerate(refs):
                r.ref_id = f"ref-{global_ref_cursor + idx + 1}"

            # 提取合并文本中所有 unique tags，建立映射
            ref_tags = re.findall(r'\[(b\d+|ref-\d+)\]', combined_text)
            unique_tags = sorted(list(set(ref_tags)))

            tag_to_global_id = {}
            for i, tag in enumerate(unique_tags):
                if i < len(refs):
                    gid = global_ref_cursor + 1 + i
                    tag_to_global_id[tag] = gid

            global_ref_cursor += len(refs)

            def make_replacer(mapping):
                def replace_precise(match):
                    tag = match.group(1)
                    if tag in mapping:
                        return f"[ref-{mapping[tag]}]"
                    return match.group(0)
                return replace_precise

            replacer = make_replacer(tag_to_global_id)

            # 替换两个字段中的引用标记
            response_replaced = re.sub(r'\[(b\d+|ref-\d+)\]', replacer, response)
            gaps_replaced = re.sub(r'\[(b\d+|ref-\d+)\]', replacer, gaps)

            processed_items.append({
                **item,
                'response_fmt': response_replaced,
                'gaps_fmt': gaps_replaced,
            })

        # 第一部分：总览表格
        table_lines = [
            "| 序号 | 技术要求 | 应答情况 | 状态 |",
            "|:---:|:---|:---|:---:|"
        ]

        for item in processed_items:
            req = item.get('requirement', '').replace('|', '\\|')
            # 清除残余换行，确保表格每行只有一行文字
            response_cell = item['response_fmt'].replace('\n', ' ').strip().replace('|', '\\|')
            status = status_map.get(item.get('status', 'unknown'), '❓ 未知')
            table_lines.append(f"| {item['id']} | {req} | {response_cell} | {status} |")

        parts = ["\n".join(table_lines)]

        # 第二部分：不符合 / 部分符合 详情
        problem_items = [i for i in processed_items if i.get('status') in ('unsatisfied', 'partial')]

        if problem_items:
            detail_lines = ["\n---\n## ❌ 不符合 / ⚠️ 部分符合 说明\n"]
            for item in problem_items:
                req = item.get('requirement', '')
                gaps_text = item.get('gaps_fmt', '').strip()
                detail_lines.append(f"### {item['id']}. {req}\n")
                if gaps_text:
                    detail_lines.append(f"**缺失/问题：** {gaps_text}\n")
                else:
                    detail_lines.append(f"**缺失/问题：** {item['response_fmt'].strip()}\n")
            parts.append("\n".join(detail_lines))

        return "\n".join(parts)

    async def _verify_single_requirement(
        self,
        doc_id: str,
        requirement: str,
        api_key: str = None,
        allowed_pages: List[int] | None = None,
    ) -> Dict[str, Any]:
        """验证单条要求"""
        try:
            # 1. 检索相关文段（top_k=15 + 上下文扩展，减少段落被切断的漏判）
            chunks = await rag_engine.retrieve(
                requirement,
                doc_id,
                top_k=15,
                api_key=api_key,
                allowed_pages=allowed_pages,
                expand_context=True,
            )
            
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
            
            prompt = f"""你是一个技术文件核对专家。请根据以下文档片段，判断是否支撑技术要求。

技术要求：{requirement}

文档片段：
---
{context}
---

请以JSON格式返回（所有字段值不得含有换行符 \\n）：
{{
    "status": "satisfied" | "unsatisfied" | "partial" | "unknown",
    "summary": "一句话结论，不超过60字，引用关键段落如[b0001]或[ref-1]",
    "gaps": "仅当 status 为 unsatisfied 或 partial 时填写：列出具体缺失内容及依据（引用段落），satisfied/unknown 时填空字符串"
}}
注意：status 必须严格为 satisfied/unsatisfied/partial/unknown 之一。
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
            summary = result_data.get("summary", result_data.get("reason", "无法判断"))
            gaps = result_data.get("gaps", "")

            # 从 summary + gaps 合并文本中提取引用
            combined = summary + " " + gaps
            ref_tags = re.findall(r'\[(b\d+|ref-\d+)\]', combined)
            unique_tags = sorted(list(set(ref_tags)))

            # 建立映射: block_id -> chunk, ref-id -> chunk
            chunk_map = {}
            for i, c in enumerate(chunks):
                if c.block_id:
                    chunk_map[c.block_id] = c
                chunk_map[f"ref-{i+1}"] = c

            active_refs = []
            for tag in unique_tags:
                if tag in chunk_map:
                    active_refs.append(chunk_map[tag])

            return {
                "requirement": requirement,
                "status": status,
                "response": summary,
                "gaps": gaps,
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
