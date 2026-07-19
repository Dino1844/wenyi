"""概念抽取 Agent。

从浓缩后的文本中抽取新出现或被确认的概念，更新概念图。
"""

from __future__ import annotations

from typing import Any

from ..concepts.store import ConceptStore, ConceptEntry
from ..agents.base import Agent

_SYSTEM = """\
你是概念追踪器。从给定的浓缩文本中，识别关键概念及其在文中的定义或用法。

只抽取：
1. 首次出现且被定义了的概念（术语、专有名词、技术性用语）
2. 在论证中起关键作用的概念（即使不是首次出现，但本章赋予了新含义）

不要抽取：普通词汇、一次性修辞、人名（除非是概念性人名如"笛卡尔剧场"）。

仅输出 JSON：
{"concepts": [{"term": "概念名", "definition": "在本文中的定义/用法（1句）", "role": "在论证中的角色"}]}\
"""

_USER = """\
【已有概念图（参考，避免重复）】
$existing

【浓缩文本】
$text

请抽取新出现或被重新定义的概念。\
"""


class ConceptExtractor(Agent):
    """从浓缩文本中抽取概念，更新概念图。"""

    def extract_and_store(
        self, store: ConceptStore, condensed_text: str, chapter: int
    ) -> int:
        """抽取概念并写入 store，返回新增条目数。"""
        existing = store.render_for_prompt()
        from string import Template
        user = Template(_USER).safe_substitute(
            existing=existing, text=condensed_text
        )
        data = self._ask_json(_SYSTEM, user, tier="fast", default={})
        items = self.dict_items(data.get("concepts") if isinstance(data, dict) else data)
        count = 0
        for item in items:
            term = str(item.get("term", "")).strip()
            if not term:
                continue
            store.upsert(
                ConceptEntry(
                    term=term,
                    definition=str(item.get("definition", "")).strip(),
                    role=str(item.get("role", "")).strip(),
                ),
                chapter=chapter,
            )
            count += 1
        store.save()
        return count
