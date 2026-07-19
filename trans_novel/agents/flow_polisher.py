"""流畅度润色 Agent（强档）。

去除浓缩文本的"报告感"和"列表感"，确保它读起来像一条连续的思维河流。
不改变内容、不增加信息，只调整节奏和衔接。
"""

from __future__ import annotations

from .base import Agent

_SYSTEM = """\
你是一位中文文体编辑，负责把一段浓缩文本打磨成流畅的书面语。

目标文体：流畅、连贯的书面中文，像编辑精良的随笔或优质非虚构译文。
不是口语，不是聊天，不是"老北京话"。

只做克制的润色，优先保留原文的句子与结构。仅在以下情况动手：
1. 路标词报幕——"拿X做类比""同样""此外""首先/其次""让我们看"——删掉，
   让前后句靠思路惯性衔接。
2. 翻译腔——被动式、三重"的"定语链、整句塞进定语——理顺成自然中文。
3. 元叙述——"本段讨论""接下来我们看""作者认为"——删除。

硬性要求：
- 不改变内容、不增删论证步骤、不改变结论与落点。
- 绝不打碎推理链：因果、转折、递进的关系必须保留，该有"因为/所以/然而/也就是说"
  的地方保留，让每一步推理连贯可读，不能切成一堆互不衔接的碎句。
- 以结构完整的长句为主体；不要为了"节奏"把句子拆碎，更不要添加口语化语气。
- 如果原文已经通顺，可以几乎原样返回——克制是一种美德。

仅输出 JSON：{"polished": "润色后的文本"}\
"""

_USER = """\
【待润色文本】
$text

请做克制的文体润色（保留全部论证内容与推理连贯），输出 JSON：{"polished": "..."}\
"""


class FlowPolisher(Agent):
    """流畅度润色：去报告感，加推进感。"""

    def polish(self, text: str) -> str:
        """润色浓缩文本。

        Args:
            text: 浓缩后的文本。

        Returns:
            润色后的文本。失败时返回原文。
        """
        from string import Template
        user = Template(_USER).safe_substitute(text=text)
        data = self._ask_json(_SYSTEM, user, tier="strong", default={})
        if isinstance(data, dict):
            polished = str(data.get("polished", "")).strip()
            # 安全检查：润色后不应比原文短太多（防止模型误删内容）
            if polished and len(polished) > len(text) * 0.5:
                return polished
        return text
