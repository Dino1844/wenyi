"""全书结构分析 Agent（强档）。

通读样章和目录，产出全书的论证/叙事/知识结构骨架，
识别核心问题、推进弧线、关键概念及其定义。
替代原 Analyzer（风格分析）在浓缩模式下的角色。
"""

from __future__ import annotations

from typing import Any

from .base import Agent

_SYSTEM = """\
你是一位结构分析师。你的任务不是总结这本书"讲了什么"，而是画出它的骨架。

分析维度：
1. 核心问题：这本书试图回答什么问题？（一句话）
2. 结构类型：它的推进方式是什么？
   - linear_argument（线性论证：前提→推理→结论）
   - layered_concepts（概念层叠：逐个引入概念，后者依赖前者）
   - chronological（时间线：按事件顺序推进）
   - problem_solution（问题-方案：提出问题，逐步给出解答）
   - spiral（螺旋：反复回到同一主题，每次加深一层）
3. 弧线：全书从哪里开始，经过什么，到哪里结束？（3-5句）
4. 关键结构节点：哪些部分/章节范围承担什么结构功能？
5. 关键概念：全书依赖的核心概念，及其在本书中的定义。
6. 承重墙 vs 装饰：哪些内容是"去掉它全书塌掉"的，哪些是"去掉不影响结构"的？

仅输出 JSON：
{
  "central_question": "这本书试图回答的核心问题",
  "structure_type": "linear_argument | layered_concepts | chronological | problem_solution | spiral",
  "arc": "全书推进弧线（3-5句）",
  "key_moves": [
    {"chapter_range": "章节范围（如 1-12 或 前言）", "function": "在全书结构中的功能"}
  ],
  "concepts": [
    {"term": "概念名", "definition_in_book": "本书中的定义/用法（1-2句）", "role": "在全书论证中的角色"}
  ],
  "load_bearing": "承重内容的特征描述（什么样的段落是核心的）",
  "dispensable": "可去掉内容的特征描述（什么样的段落是装饰性的）"
}\
"""

_USER = """\
【书籍类型】$book_type（$sub_type）

【书籍样本（取自开头/中部/结尾）】
$sample

【目录结构（如有）】
$toc

请分析这本书的结构骨架。\
"""


class StructureAnalyzer(Agent):
    """分析全书结构，产出结构骨架和概念图种子。"""

    def analyze(self, sample_text: str, toc: str = "",
                book_type: str = "", sub_type: str = "") -> dict[str, Any]:
        """分析全书结构。

        Args:
            sample_text: 多点采样文本。
            toc: 目录结构文本。
            book_type: 已识别的书籍类型。
            sub_type: 子类型。

        Returns:
            结构分析结果字典。
        """
        from string import Template
        user = Template(_USER).safe_substitute(
            sample=sample_text,
            toc=toc or "（无目录信息）",
            book_type=book_type or "未知",
            sub_type=sub_type or "未知",
        )
        data = self._ask_json(_SYSTEM, user, tier="strong", default={})
        if not isinstance(data, dict):
            data = {}
        # 清洗字段
        for key in ("central_question", "structure_type", "arc",
                    "load_bearing", "dispensable"):
            val = data.get(key)
            data[key] = val.strip() if isinstance(val, str) else ""
        data["key_moves"] = self.dict_items(data.get("key_moves"))
        data["concepts"] = self.dict_items(data.get("concepts"))
        return data

    def structure_brief(self, analysis: dict[str, Any]) -> str:
        """把结构分析浓缩为注入后续阶段的简报文本。"""
        lines = []
        if analysis.get("central_question"):
            lines.append(f"核心问题：{analysis['central_question']}")
        if analysis.get("structure_type"):
            lines.append(f"结构类型：{analysis['structure_type']}")
        if analysis.get("arc"):
            lines.append(f"全书弧线：{analysis['arc']}")
        moves = analysis.get("key_moves", [])
        if moves:
            lines.append("结构节点：")
            for m in moves:
                lines.append(f"  - {m.get('chapter_range', '?')}: {m.get('function', '')}")
        if analysis.get("load_bearing"):
            lines.append(f"承重内容：{analysis['load_bearing']}")
        if analysis.get("dispensable"):
            lines.append(f"可省略内容：{analysis['dispensable']}")
        return "\n".join(lines)

    def concept_seed(self, analysis: dict[str, Any]) -> list[dict[str, str]]:
        """从结构分析中提取概念图种子。"""
        concepts = []
        for c in self.dict_items(analysis.get("concepts")):
            term = str(c.get("term", "")).strip()
            definition = str(c.get("definition_in_book", "")).strip()
            role = str(c.get("role", "")).strip()
            if term:
                concepts.append({
                    "term": term,
                    "definition": definition,
                    "role": role,
                })
        return concepts
