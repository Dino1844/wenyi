"""章节角色定位 Agent。

为每一章确定它在全书结构中的"功能角色"——不是"这章讲了什么"，
而是"这章在全书推进中干了什么活"。识别核心段落（泵）和可省略段落（说明书）。
替代原 Synopsizer（章节梗概）在浓缩模式下的角色。
"""

from __future__ import annotations

from typing import Any

from .base import Agent

_SYSTEM = """\
你是一位编辑，正在为一本浓缩版做章节规划。

读完给定的章节原文，结合全书结构信息，告诉我：
1. 它在全书论证/叙事/知识推进中承担什么功能？（如果删掉这章，全书会缺什么？）
2. 哪些段落是"泵"——真正推动读者认知前进的核心段落？（思想实验、关键类比、
   核心论证步骤、情节转折点、概念的"啊哈时刻"）
3. 哪些段落是"说明书"——学术辩护、重复论证、过渡废话、文献回顾、"如前所述"式回顾？
4. 如果只能保留原文的 20-30%，你保留哪些？为什么？
5. 本章使用了哪些概念？引入了哪些新概念？

仅输出 JSON：
{
  "role": "本章在全书中的功能（1句话）",
  "argument_function": "具体论证/叙事功能（如：正面论证、反驳、类比说明、情节转折、概念引入）",
  "key_passages": [
    {"paragraph_range": "段落范围（如 3-5 或 12）", "reason": "为什么这是核心段落"}
  ],
  "dispensable": [
    {"paragraph_range": "段落范围", "reason": "为什么可以省略"}
  ],
  "concepts_used": ["本章使用的已有概念"],
  "concepts_introduced": ["本章新引入的概念"],
  "retention_plan": "如果保留20-30%，保留什么、去掉什么（2-3句概述）"
}\
"""

_USER = """\
【全书结构】
$structure_brief

【本章位置】第 $chapter_index 章（共 $total_chapters 章）
【本章标题】$chapter_title

【章节原文】
$source

请分析本章在全书中的角色。\
"""


class ChapterRoleAnalyzer(Agent):
    """为每章确定在全书结构中的功能角色。"""

    def analyze_chapter(
        self,
        source_text: str,
        chapter_index: int,
        total_chapters: int,
        chapter_title: str = "",
        structure_brief: str = "",
    ) -> dict[str, Any]:
        """分析单章的角色定位。

        Args:
            source_text: 章节原文。
            chapter_index: 章节序号（从 0 开始）。
            total_chapters: 全书总章数。
            chapter_title: 章节标题。
            structure_brief: 全书结构简报。

        Returns:
            角色定位结果字典。
        """
        from string import Template
        user = Template(_USER).safe_substitute(
            source=source_text,
            chapter_index=chapter_index,
            total_chapters=total_chapters,
            chapter_title=chapter_title or f"第 {chapter_index + 1} 章",
            structure_brief=structure_brief or "（暂无全书结构信息）",
        )
        data = self._ask_json(_SYSTEM, user, tier="cheap", default={})
        if not isinstance(data, dict):
            data = {}
        # 清洗
        for key in ("role", "argument_function", "retention_plan"):
            val = data.get(key)
            data[key] = val.strip() if isinstance(val, str) else ""
        data["key_passages"] = self.dict_items(data.get("key_passages"))
        data["dispensable"] = self.dict_items(data.get("dispensable"))
        data["concepts_used"] = [
            str(c).strip() for c in (data.get("concepts_used") or []) if c
        ]
        data["concepts_introduced"] = [
            str(c).strip() for c in (data.get("concepts_introduced") or []) if c
        ]
        return data

    def role_brief(self, role_data: dict[str, Any]) -> str:
        """把角色定位结果浓缩为注入 Condenser 的简报。"""
        lines = []
        if role_data.get("role"):
            lines.append(f"本章功能：{role_data['role']}")
        if role_data.get("argument_function"):
            lines.append(f"论证类型：{role_data['argument_function']}")
        if role_data.get("retention_plan"):
            lines.append(f"保留策略：{role_data['retention_plan']}")
        key = role_data.get("key_passages", [])
        if key:
            ranges = [str(p.get("paragraph_range", "?")) for p in key]
            lines.append(f"核心段落：{', '.join(ranges)}")
        disp = role_data.get("dispensable", [])
        if disp:
            ranges = [str(p.get("paragraph_range", "?")) for p in disp]
            lines.append(f"可省略段落：{', '.join(ranges)}")
        return "\n".join(lines)
