"""全书大纲 + 阅读价值判断 Agent（强档）。

定位：在结构分析阶段（或独立调用）产出一份"值不值得读"的速览简报，
让用户在花几小时浓缩/阅读之前，先判断这本书是否值得投入。

它不是摘要，而是一份"导购式诊断"：这本书回答什么问题、骨架长什么样、
读完能拿走什么、哪些是承重章节、哪些可以跳过、适合谁、怎么读最划算，
以及一个明确的裁决（通读 / 选读 / 不必读）和理由。
"""

from __future__ import annotations

from typing import Any

from .base import Agent

# 裁决取值（供下游渲染/校验用）
VERDICT_WORTH_ALL = "worth_all"        # 值得通读
VERDICT_WORTH_SELECTIVE = "worth_selective"  # 值得选读
VERDICT_SKIP = "skip"                  # 不必读
_VERDICT_LABELS = {
    VERDICT_WORTH_ALL: "值得通读",
    VERDICT_WORTH_SELECTIVE: "值得选读",
    VERDICT_SKIP: "不必读",
}

_SYSTEM = """\
你是一位挑剔且诚实的阅读顾问。你的任务不是夸赞这本书，而是帮一个时间宝贵的读者
判断：这本书值不值得他花时间读（或浓缩）。

要求：
1. 诚实。如果这本书注水严重、论点平庸、或只对极窄的受众有用，就直说，不要客套。
2. 具体。裁决必须给出可验证的理由，指向书里真实的内容特征，而非空泛的"很有深度"。
3. 站在读者立场。读者想知道的是"我读完能拿走什么""哪些章节真正承重""能不能跳过一半"。

分析维度：
1. 一句话：这本书到底是什么（不超过 30 字，抓住独特性，别写成套话）。
2. 核心问题：它试图回答什么问题。
3. 大纲：全书骨架，按部分/章节范围列出，每条说明它在全书中承担的功能。
4. 读完能拿走什么：读完后读者真正获得的东西（思维工具/知识/视角），要具体。
5. 承重章节：哪些部分"去掉它全书塌掉"，必读。
6. 可跳过：哪些部分是装饰、重复或可替代的，可以略读或跳过。
7. 裁决：worth_all（值得通读）/ worth_selective（值得选读）/ skip（不必读）。
8. 裁决理由：为什么是这个裁决（2-4 句，诚实、具体）。
9. 适合谁 / 不适合谁：目标读者与劝退人群。
10. 建议读法：怎么读最划算（如"只读第X-Y部分 + 跳过Z"）。

仅输出 JSON：
{
  "one_liner": "一句话概括（≤30字）",
  "central_question": "核心问题",
  "outline": [
    {"chapter_range": "章节范围（如 1-12 或 前言）", "title": "这部分的主题", "function": "在全书中的功能"}
  ],
  "payoff": "读完能拿走什么（具体）",
  "load_bearing": ["必读的承重部分（章节范围 + 为什么）"],
  "skippable": ["可跳过的部分（章节范围 + 为什么）"],
  "verdict": "worth_all | worth_selective | skip",
  "verdict_reason": "裁决理由（2-4句）",
  "audience": "适合谁",
  "not_for": "不适合谁",
  "reading_path": "建议读法"\
}
"""

_USER = """\
【书籍类型】$book_type（$sub_type）

【书籍样本（取自开头/中部/结尾）】
$sample

【目录结构（如有）】
$toc

请给出这本书的大纲与阅读价值判断。\
"""


class BookOutline(Agent):
    """产出全书大纲 + 阅读价值判断，帮用户决定要不要读这本书。"""

    def analyze(
        self,
        sample_text: str,
        toc: str = "",
        book_type: str = "",
        sub_type: str = "",
    ) -> dict[str, Any]:
        """生成大纲与阅读价值判断。

        Args:
            sample_text: 多点采样文本。
            toc: 目录结构文本。
            book_type: 已识别的书籍类型。
            sub_type: 子类型。

        Returns:
            大纲与判断结果字典。
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
        # 清洗字符串字段
        for key in ("one_liner", "central_question", "payoff",
                    "verdict_reason", "audience", "not_for", "reading_path"):
            val = data.get(key)
            data[key] = val.strip() if isinstance(val, str) else ""
        # 清洗列表字段
        data["outline"] = self.dict_items(data.get("outline"))
        for key in ("load_bearing", "skippable"):
            val = data.get(key)
            if isinstance(val, list):
                data[key] = [str(x).strip() for x in val if str(x).strip()]
            elif isinstance(val, str) and val.strip():
                data[key] = [val.strip()]
            else:
                data[key] = []
        # 归一化裁决取值
        verdict = str(data.get("verdict", "")).strip().lower()
        if verdict not in _VERDICT_LABELS:
            verdict = VERDICT_WORTH_SELECTIVE  # 无法判断时给保守的"选读"
        data["verdict"] = verdict
        return data

    @staticmethod
    def verdict_label(verdict: str) -> str:
        """裁决取值 → 中文标签。"""
        return _VERDICT_LABELS.get(verdict, verdict)

    def render_markdown(self, data: dict[str, Any], title: str = "") -> str:
        """把大纲与判断渲染为一份可读的 Markdown 简报。"""
        lines: list[str] = []
        heading = f"# 《{title}》阅读价值速览" if title else "# 阅读价值速览"
        lines.append(heading)
        lines.append("")

        verdict = data.get("verdict", "")
        if verdict:
            lines.append(f"> **裁决：{self.verdict_label(verdict)}**")
            lines.append("")

        if data.get("one_liner"):
            lines.append(f"**一句话**：{data['one_liner']}")
            lines.append("")
        if data.get("central_question"):
            lines.append(f"**核心问题**：{data['central_question']}")
            lines.append("")
        if data.get("verdict_reason"):
            lines.append(f"**为什么这么说**：{data['verdict_reason']}")
            lines.append("")

        outline = data.get("outline") or []
        if outline:
            lines.append("## 全书大纲")
            lines.append("")
            for item in outline:
                cr = item.get("chapter_range", "").strip()
                t = item.get("title", "").strip()
                fn = item.get("function", "").strip()
                head = f"- **{cr}**" if cr else "-"
                if t:
                    head += f" {t}"
                if fn:
                    head += f" —— {fn}"
                lines.append(head)
            lines.append("")

        if data.get("payoff"):
            lines.append(f"## 读完能拿走什么\n\n{data['payoff']}\n")

        load_bearing = data.get("load_bearing") or []
        if load_bearing:
            lines.append("## 必读（承重章节）")
            lines.append("")
            for x in load_bearing:
                lines.append(f"- {x}")
            lines.append("")

        skippable = data.get("skippable") or []
        if skippable:
            lines.append("## 可跳过")
            lines.append("")
            for x in skippable:
                lines.append(f"- {x}")
            lines.append("")

        tail: list[str] = []
        if data.get("audience"):
            tail.append(f"- **适合**：{data['audience']}")
        if data.get("not_for"):
            tail.append(f"- **不适合**：{data['not_for']}")
        if data.get("reading_path"):
            tail.append(f"- **建议读法**：{data['reading_path']}")
        if tail:
            lines.append("## 谁该读 / 怎么读")
            lines.append("")
            lines.extend(tail)
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"
