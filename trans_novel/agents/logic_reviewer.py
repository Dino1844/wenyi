"""逻辑审校 Agent。

检查浓缩版是否丢失了关键论证步骤、是否引入了逻辑跳跃、概念是否自足。
"""

from __future__ import annotations

from typing import Any

from .base import Agent

_SYSTEM = """\
你是逻辑审校员。比对原文与浓缩版，检查浓缩过程中是否引入了以下问题：

问题类型：
- logic_gap：论证中缺少了必要的一步，读者无法从A推到B
- concept_undefined：某概念被使用但未在浓缩版中定义过（假设读者没读过原书）
- lost_punch：原文的"落点"（关键反问/核心结论/情感高点）在浓缩版中消失了
- false_summary：浓缩版把论证变成了断言（原文是逐步推出的，浓缩版直接说了结论）
- flow_break：前后段之间缺乏逻辑衔接，读起来像两个不相关的片段

只报实质性问题。合理的省略（去掉重复例子、去掉学术引用）不算问题。
拿不准就不报，宁缺毋滥。

仅输出 JSON：
{"issues": [{"type": "...", "detail": "简述问题", "suggestion": "如何修复（1句）"}]}
没有问题则输出 {"issues": []}\
"""

_USER = """\
【原文（供对照）】
$source

【浓缩版（待审校）】
$condensed

【概念图（读者已知的概念）】
$concepts

请审校浓缩版，输出 JSON：{"issues": [...]}\
"""


class LogicReviewer(Agent):
    """逻辑审校：检查浓缩版的论证完整性和概念自足性。"""

    def review(
        self, source_text: str, condensed_text: str, concepts: str = ""
    ) -> list[dict[str, Any]]:
        """审校单章浓缩结果。

        Args:
            source_text: 章节原文。
            condensed_text: 浓缩后文本。
            concepts: 概念图文本。

        Returns:
            问题列表。
        """
        from string import Template
        user = Template(_USER).safe_substitute(
            source=source_text,
            condensed=condensed_text,
            concepts=concepts or "（暂无）",
        )
        data = self._ask_json(_SYSTEM, user, tier="cheap", default={})
        if isinstance(data, dict):
            return self.dict_items(data.get("issues"))
        return []

    def has_severe_issues(self, issues: list[dict]) -> bool:
        """判断是否存在严重问题（logic_gap 或 lost_punch）。"""
        severe_types = {"logic_gap", "lost_punch", "false_summary"}
        return any(i.get("type") in severe_types for i in issues)
