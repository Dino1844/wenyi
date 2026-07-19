"""概念图存储。

追踪全书关键概念：术语、书中定义、首次出现章节、依赖关系。
确保浓缩版中每个概念在首次出现时有足够定义，不依赖读者读过原文。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class ConceptEntry:
    """单个概念条目。"""
    term: str
    definition: str = ""
    role: str = ""
    first_chapter: int = -1
    dependencies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ConceptEntry":
        return cls(
            term=str(d.get("term", "")).strip(),
            definition=str(d.get("definition", "")).strip(),
            role=str(d.get("role", "")).strip(),
            first_chapter=int(d.get("first_chapter", -1)),
            dependencies=[str(x) for x in (d.get("dependencies") or [])],
        )


class ConceptStore:
    """概念图的持久化存储（JSON 文件）。"""

    def __init__(self, path: str):
        self.path = path
        self._concepts: dict[str, ConceptEntry] = {}
        if os.path.exists(path):
            self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("concepts", []):
                entry = ConceptEntry.from_dict(item)
                if entry.term:
                    self._concepts[entry.term] = entry
        except (json.JSONDecodeError, OSError):
            pass

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        data = {"concepts": [e.to_dict() for e in self._concepts.values()]}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def upsert(self, entry: ConceptEntry, chapter: int = -1) -> None:
        """插入或更新概念。已有定义时不覆盖（首次定义优先）。"""
        existing = self._concepts.get(entry.term)
        if existing:
            if not existing.definition and entry.definition:
                existing.definition = entry.definition
            if not existing.role and entry.role:
                existing.role = entry.role
            if existing.first_chapter < 0 and chapter >= 0:
                existing.first_chapter = chapter
            if entry.dependencies:
                existing.dependencies = list(
                    set(existing.dependencies) | set(entry.dependencies)
                )
        else:
            if chapter >= 0 and entry.first_chapter < 0:
                entry.first_chapter = chapter
            self._concepts[entry.term] = entry

    def get(self, term: str) -> ConceptEntry | None:
        return self._concepts.get(term)

    def all_concepts(self) -> list[ConceptEntry]:
        return list(self._concepts.values())

    def concepts_for_chapter(self, chapter_text: str) -> list[ConceptEntry]:
        """返回在给定章节文本中出现的概念。"""
        hits = []
        for entry in self._concepts.values():
            if entry.term in chapter_text:
                hits.append(entry)
        return hits

    def render_for_prompt(self, entries: list[ConceptEntry] | None = None) -> str:
        """渲染为注入提示词的文本。"""
        items = entries if entries is not None else self.all_concepts()
        if not items:
            return "（暂无概念）"
        lines = []
        for e in items:
            dep = f"（依赖：{', '.join(e.dependencies)}）" if e.dependencies else ""
            lines.append(f"- {e.term}: {e.definition}{dep}")
        return "\n".join(lines)

    def seed_from_analysis(self, concepts: list[dict[str, str]], chapter: int = 0) -> int:
        """从结构分析结果种入概念。返回写入数。"""
        count = 0
        for c in concepts:
            term = str(c.get("term", "")).strip()
            if not term:
                continue
            self.upsert(
                ConceptEntry(
                    term=term,
                    definition=str(c.get("definition", "")).strip(),
                    role=str(c.get("role", "")).strip(),
                ),
                chapter=chapter,
            )
            count += 1
        return count
