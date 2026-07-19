"""书籍类型识别 Agent。

通读多点采样文本，判断书籍的主导类型（论证/叙事/知识/混合），
决定后续浓缩策略。
"""

from __future__ import annotations

from typing import Any

from .base import Agent

# 类型 → 浓缩策略映射
STRATEGY_MAP = {
    "argumentative": "preserve_argument_chain",
    "narrative": "preserve_plot_arc",
    "knowledge": "preserve_concept_map",
    "mixed": "hybrid",
}

_SYSTEM = """\
你是一位阅读策略分析师。你的任务是判断一本书的类型和最佳浓缩策略。

不要只看书名或主题——要看它的写作方式：
- 它是在论证一个观点（提出论点、给出论据、反驳对手、得出结论）？
- 还是在讲述一个故事（有人物、情节、场景、冲突）？
- 还是在传授一套知识（定义概念、举例说明、层层展开）？

它的核心"推进力"是什么——逻辑推理的惯性、情节张力、还是概念的层层展开？

仅输出 JSON：
{
  "book_type": "argumentative | narrative | knowledge | mixed",
  "sub_type": "具体子类型（如：哲学论证、科普、回忆录、小说、教材、自助等）",
  "driving_force": "这本书靠什么推动读者往下读（1-2句）",
  "condensation_strategy": "preserve_argument_chain | preserve_plot_arc | preserve_concept_map | hybrid",
  "reasoning": "判断依据（2-3句，引用你观察到的文本特征）"
}\
"""

_USER = """\
【书籍样本（取自开头/中部/结尾）】
$sample

【目录结构（如有）】
$toc

请分析这本书的类型和最佳浓缩策略。\
"""


class TypeDetector(Agent):
    """识别书籍类型，返回类型信息和浓缩策略。"""

    def detect(self, sample_text: str, toc: str = "") -> dict[str, Any]:
        """分析样本文本，返回书籍类型和浓缩策略。

        Args:
            sample_text: 多点采样的书籍文本（开头/中部/结尾）。
            toc: 目录结构文本（可选）。

        Returns:
            包含 book_type, sub_type, driving_force, condensation_strategy, reasoning 的字典。
        """
        from string import Template
        user = Template(_USER).safe_substitute(
            sample=sample_text,
            toc=toc or "（无目录信息）",
        )
        data = self._ask_json(_SYSTEM, user, tier="cheap", default={})
        if not isinstance(data, dict):
            data = {}
        # 规范化 book_type
        book_type = str(data.get("book_type", "")).strip().lower()
        if book_type not in STRATEGY_MAP:
            book_type = "mixed"
        data["book_type"] = book_type
        # 确保 strategy 与 type 一致
        if not data.get("condensation_strategy"):
            data["condensation_strategy"] = STRATEGY_MAP[book_type]
        return data

    def strategy_for(self, detection: dict[str, Any]) -> str:
        """从检测结果中提取浓缩策略名。"""
        return detection.get("condensation_strategy", "hybrid")
