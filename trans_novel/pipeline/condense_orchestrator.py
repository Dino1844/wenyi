"""浓缩编排器：驱动书本浓缩全流程。

流水线：
  读取输入 → 书籍类型识别 → 全书结构分析 → 建立概念图 →
  逐章角色定位（可并行）→ 逐章浓缩（串行，带滚动上下文）→
  流畅度润色 → 逻辑审校 → 组装浓缩版 EPUB

章级状态机 + 断点续跑，复用 RunStore 基础设施。
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional

from ..config import Config
from ..concepts.store import ConceptStore
from ..concepts.extractor import ConceptExtractor
from ..llm.base import LLMClient
from ..llm.factory import build_client
from ..llm.usage import merge_usage_summaries, usage_delta
from ..ingest.segmenter import load_document
from ..agents.type_detector import TypeDetector
from ..agents.structure_analyzer import StructureAnalyzer
from ..agents.book_outline import BookOutline
from ..agents.chapter_role import ChapterRoleAnalyzer
from ..agents.condenser import Condenser
from ..agents.flow_polisher import FlowPolisher
from ..agents.logic_reviewer import LogicReviewer
from .runstore import RunStore, STATUS_DONE, slugify

ProgressFn = Callable[[int, int, str], None]


class CondenseOrchestrator:
    """书本浓缩全流程编排。"""

    def __init__(self, config: Config, client: LLMClient | None = None):
        self.config = config
        self.client = client or build_client(config)
        self._usage_checkpoint = self.client.usage_summary()

        # Agents
        self.type_detector = TypeDetector(self.client, config)
        self.structure_analyzer = StructureAnalyzer(self.client, config)
        self.book_outline = BookOutline(self.client, config)
        self.chapter_role_analyzer = ChapterRoleAnalyzer(self.client, config)
        self.condenser = Condenser(self.client, config)
        self.flow_polisher = FlowPolisher(self.client, config)
        self.logic_reviewer = LogicReviewer(self.client, config)
        self.concept_extractor = ConceptExtractor(self.client, config)

    def _flush_usage(self, store: RunStore, *, scope: str) -> None:
        current = self.client.usage_summary()
        increment = usage_delta(current, self._usage_checkpoint)
        self._usage_checkpoint = current
        accumulated = store.load_usage() or {"totals": {}, "by_tier": {}, "by_stage": {}}
        if not increment["totals"]["calls"]:
            return
        cumulative = merge_usage_summaries(accumulated, increment)
        store.save_usage(cumulative)

    @staticmethod
    def _sample_text(doc, max_chars: int = 8000) -> str:
        """多点采样：开头/中部/结尾各取一段。"""
        texts = ["\n".join(s.source for s in ch.text_segments) for ch in doc.chapters]
        texts = [t for t in texts if len(t) > 100]
        if not texts:
            return ""
        picks = [(0, "【开头】"), (len(texts) // 2, "【中部】"), (len(texts) - 1, "【结尾】")]
        parts = []
        seen = set()
        per_chunk = max_chars // 3
        for idx, tag in picks:
            if idx in seen:
                continue
            seen.add(idx)
            chunk = texts[idx][:per_chunk]
            parts.append(f"{tag}\n{chunk}")
        return "\n\n".join(parts)

    @staticmethod
    def _toc_text(doc) -> str:
        """提取目录结构文本。"""
        titles = [ch.title for ch in doc.chapters if ch.title]
        if not titles:
            return ""
        return "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))

    # 前置/附属章节的标题关键词：这些不是正文论证，浓缩它们没有意义，直接跳过。
    _SKIP_TITLE_KEYWORDS = (
        "推荐序", "赞誉", "前言", "引言", "序言", "序 ", "自序",
        "献 ", "致谢", "素材来源", "参考文献", "注释", "译者后记", "后记",
        "小结", "结语", "附录", "版权",
    )

    @classmethod
    def _is_skippable_chapter(cls, title: str) -> bool:
        """判断某章是否为前置/附属内容（按标题），这类章节不参与浓缩。"""
        t = (title or "").strip()
        if not t:
            return False
        return any(k.strip() and k.strip() in t for k in cls._SKIP_TITLE_KEYWORDS)

    def run(
        self,
        input_path: str,
        *,
        progress: Optional[ProgressFn] = None,
        out_path: str | None = None,
        start: int = 0,
        limit: int | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """执行完整浓缩流水线。

        start: 从第几章开始浓缩（默认 0）。
        limit: 共浓缩几章（用于快速试跑）；None 表示到书末。
        force: 忽略已完成的章节状态，强制重新浓缩（改 prompt 后重跑用）。
        """
        if progress:
            progress(0, 0, "解析文档…")
        doc = load_document(
            input_path,
            self.config.source_lang,
            self.config.target_lang,
            split_segments=self.config.segment.max_chars_per_segment,
        )
        run_dir = os.path.join(self.config.state_dir, slugify(doc.title))
        store = RunStore(run_dir)

        with store.lock():
            return self._run_locked(
                doc, store, input_path,
                progress=progress, out_path=out_path,
                start=start, limit=limit, force=force,
            )

    def _run_locked(
        self,
        doc,
        store: RunStore,
        input_path: str,
        *,
        progress: Optional[ProgressFn],
        out_path: str | None,
        start: int = 0,
        limit: int | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """在书级锁内执行浓缩全流程。"""
        cp = self.config.condense_pipeline
        cc = self.config.condense
        total_chapters = len(doc.chapters)
        # 试跑区间：[start, end)。limit 为 None 时一直浓缩到书末。
        start = max(0, min(start, total_chapters))
        end = total_chapters if not limit else min(start + limit, total_chapters)
        span = end - start  # 本次实际处理的章数



        # ── 阶段 0：初始化运行状态（首次运行写入章节文件与 manifest）────────
        # 已存在 manifest 时跳过，保留断点续跑时已落盘的浓缩结果。
        if not store.exists():
            manifest = store.stage_document(doc)
            store.save_manifest(manifest)
        done_statuses = set() if force else {
            c["index"]
            for c in store.load_manifest()["chapters"]
            if c.get("status") == STATUS_DONE
        }

        # ── 阶段 1：书籍类型识别 ──────────────────────────────────────────
        if progress:
            progress(0, total_chapters, "识别书籍类型…")
        sample = self._sample_text(doc)
        toc = self._toc_text(doc)
        detection = self.type_detector.detect(sample, toc)
        book_type = detection.get("book_type", "mixed")
        if cc.book_type != "auto":
            book_type = cc.book_type
        store.log_event("book_type_detected", detection=detection, effective=book_type)

        # ── 阶段 2：全书结构分析 ──────────────────────────────────────────
        structure = {}
        structure_brief = ""
        if cp.structure_analysis:
            if progress:
                progress(0, total_chapters, "分析全书结构…")
            structure = self.structure_analyzer.analyze(
                sample, toc,
                book_type=book_type,
                sub_type=detection.get("sub_type", ""),
            )
            structure_brief = self.structure_analyzer.structure_brief(structure)
            store.save_analysis(structure)
            store.log_event("structure_analyzed", structure=structure)
            # 顺带产出一份"阅读价值速览"，供用户在浓缩前判断这本书值不值得读。
            try:
                outline_data = self.book_outline.analyze(
                    sample, toc,
                    book_type=book_type,
                    sub_type=detection.get("sub_type", ""),
                )
                outline_md = self.book_outline.render_markdown(outline_data, doc.title)
                self._save_outline(store, outline_data, outline_md)
                store.log_event(
                    "book_outline_generated",
                    verdict=outline_data.get("verdict", ""),
                )
            except Exception as exc:  # 大纲是增值功能，失败不阻断主流程
                store.log_event("book_outline_failed", error=str(exc))

        # ── 阶段 3：建立概念图 ────────────────────────────────────────────
        concept_store = ConceptStore(
            os.path.join(store.run_dir, "concepts.json")
        )
        if cp.concept_map and structure:
            seeds = self.structure_analyzer.concept_seed(structure)
            concept_store.seed_from_analysis(seeds, chapter=0)
            concept_store.save()
            store.log_event("concept_map_seeded", count=len(seeds))

        # ── 阶段 4：逐章角色定位（可并行）────────────────────────────────
        chapter_roles: dict[int, dict] = {}
        if cp.chapter_role:
            if progress:
                progress(0, span, "定位章节角色…")
            workers = max(1, cp.prescan_concurrency)

            def _role_one(ci: int) -> tuple[int, dict]:
                ch = doc.chapters[ci]
                src = "\n".join(s.source for s in ch.text_segments)
                if not src.strip():
                    return ci, {}
                return ci, self.chapter_role_analyzer.analyze_chapter(
                    src, ci, total_chapters,
                    chapter_title=ch.title or "",
                    structure_brief=structure_brief,
                )

            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_role_one, ci): ci for ci in range(start, end)}
                done_count = 0
                for fut in as_completed(futs):
                    ci, role_data = fut.result()
                    chapter_roles[ci] = role_data
                    done_count += 1
                    if progress:
                        progress(done_count, span, "定位章节角色…")
            store.log_event("chapter_roles_analyzed", count=len(chapter_roles))

        # ── 阶段 5：逐章浓缩（串行，带滚动上下文）────────────────────────
        if progress:
            progress(0, span, "浓缩中…")
        # 与 doc.chapters 等长、按真实章号对齐；未处理的章保持空串，组装时跳过。
        condensed_chapters: list[str] = [""] * total_chapters
        rolling_context: list[str] = []
        ctx_keep = cp.rolling_context_segments
        all_issues: list[dict] = []

        for ci in range(start, end):
            ch = doc.chapters[ci]
            src = "\n".join(s.source for s in ch.text_segments)
            if not src.strip():
                continue

            # 前置/附属章节（推荐序、赞誉、前言、小结、附录等）不参与浓缩。
            if self._is_skippable_chapter(ch.title):
                store.log_event("chapter_skipped", chapter=ci, title=ch.title)
                if progress:
                    progress(ci - start + 1, span, "浓缩中…")
                continue

            # 断点续跑：已完成章节直接复用落盘结果，仅重建滚动上下文。
            if ci in done_statuses:
                saved = store.load_chapter(ci).meta.get("condensed", "")
                rolling_context.append(saved)
                condensed_chapters[ci] = saved
                if progress:
                    progress(ci - start + 1, span, "浓缩中…")
                continue

            # 渲染上下文
            ctx_text = "\n\n".join(rolling_context[-ctx_keep:]) if rolling_context else ""
            # 概念图子集
            concepts_text = ""
            if cp.concept_map:
                hits = concept_store.concepts_for_chapter(src)
                concepts_text = concept_store.render_for_prompt(hits) if hits else ""
            # 角色简报
            role_brief = ""
            if ci in chapter_roles:
                role_brief = self.chapter_role_analyzer.role_brief(chapter_roles[ci])

            # 浓缩
            condensed = self.condenser.condense_chapter(
                source_text=src,
                structure_brief=structure_brief,
                chapter_role=role_brief,
                concepts=concepts_text,
                context=ctx_text,
                book_type=book_type,
                target_ratio=cc.target_ratio,
            )

            # 润色
            if cp.polish and condensed:
                condensed = self.flow_polisher.polish(condensed)

            # 逻辑审校
            if cp.review and condensed:
                issues = self.logic_reviewer.review(src, condensed, concepts_text)
                for issue in issues:
                    issue["chapter"] = ci
                all_issues.extend(issues)
                # 如果有严重问题，记录但不自动修复（留给人工）
                if self.logic_reviewer.has_severe_issues(issues):
                    store.log_event(
                        "chapter_severe_issues", chapter=ci, issues=issues
                    )

            # 概念抽取
            if cp.concept_map and condensed:
                self.concept_extractor.extract_and_store(concept_store, condensed, ci)

            # 更新滚动上下文
            rolling_context.append(condensed)
            condensed_chapters[ci] = condensed

            # 落盘
            chapter_data = store.load_chapter(ci)
            chapter_data.meta["condensed"] = condensed
            chapter_data.meta["role"] = chapter_roles.get(ci, {})
            store.save_chapter(chapter_data)
            store.set_chapter_status(ci, STATUS_DONE)

            if progress:
                progress(ci - start + 1, span, "浓缩中…")
            store.log_event(
                "chapter_condensed",
                chapter=ci,
                title=ch.title,
                source_len=len(src),
                condensed_len=len(condensed),
                ratio=round(len(condensed) / max(len(src), 1), 3),
            )

        # ── 阶段 6：组装输出 ─────────────────────────────────────────────
        if progress:
            progress(span, span, "组装输出…")
        output_path = self._assemble_output(
            doc, condensed_chapters, store, input_path, out_path
        )

        self._flush_usage(store, scope="condense")
        store.log_event(
            "condense_finished",
            book_type=book_type,
            total_chapters=total_chapters,
            issue_count=len(all_issues),
            output=output_path,
        )
        return {
            "store": store,
            "output": output_path,
            "book_type": book_type,
            "issues": all_issues,
            "structure": structure,
            "outline": os.path.join(store.run_dir, "outline.md"),
        }

    # ── 阅读价值速览（大纲）──────────────────────────────────────────────
    def _save_outline(self, store: RunStore, data: dict, markdown: str) -> None:
        """把大纲结果落盘：outline.json（结构化）+ outline.md（可读简报）。"""
        store._write_json(os.path.join(store.run_dir, "outline.json"), data)
        md_path = os.path.join(store.run_dir, "outline.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(markdown)

    def outline(
        self,
        input_path: str,
        *,
        progress: Optional[ProgressFn] = None,
        out_path: str | None = None,
        book_type: str | None = None,
    ) -> dict[str, Any]:
        """只跑"类型识别 + 结构分析 + 大纲"，产出阅读价值速览。

        用于在完整浓缩之前先判断一本书值不值得读。不进入逐章浓缩，
        因此成本远低于 run()。

        Args:
            input_path: 输入书籍路径。
            progress: 进度回调。
            out_path: 大纲 Markdown 输出路径；None 时仅写入状态目录。
            book_type: 强制指定书籍类型；None 时自动识别。

        Returns:
            {"store", "outline"(md 文本), "outline_path", "verdict", "book_type"}
        """
        if progress:
            progress(0, 0, "解析文档…")
        doc = load_document(
            input_path,
            self.config.source_lang,
            self.config.target_lang,
            split_segments=self.config.segment.max_chars_per_segment,
        )
        run_dir = os.path.join(self.config.state_dir, slugify(doc.title))
        store = RunStore(run_dir)

        with store.lock():
            if not store.exists():
                manifest = store.stage_document(doc)
                store.save_manifest(manifest)

            if progress:
                progress(0, 3, "识别书籍类型…")
            sample = self._sample_text(doc)
            toc = self._toc_text(doc)
            detection = self.type_detector.detect(sample, toc)
            detected_type = detection.get("book_type", "mixed")
            effective_type = book_type or (
                self.config.condense.book_type
                if self.config.condense.book_type != "auto"
                else detected_type
            )
            store.log_event(
                "book_type_detected", detection=detection, effective=effective_type
            )

            if progress:
                progress(1, 3, "生成阅读价值速览…")
            outline_data = self.book_outline.analyze(
                sample, toc,
                book_type=effective_type,
                sub_type=detection.get("sub_type", ""),
            )
            markdown = self.book_outline.render_markdown(outline_data, doc.title)
            self._save_outline(store, outline_data, markdown)
            store.log_event(
                "book_outline_generated",
                verdict=outline_data.get("verdict", ""),
            )

            # 可选：另存到用户指定路径
            saved_path = os.path.join(store.run_dir, "outline.md")
            if out_path:
                os.makedirs(
                    os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True
                )
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(markdown)
                saved_path = out_path

            self._flush_usage(store, scope="outline")
            if progress:
                progress(3, 3, "完成")
            return {
                "store": store,
                "outline": markdown,
                "outline_path": saved_path,
                "verdict": outline_data.get("verdict", ""),
                "book_type": effective_type,
                "title": doc.title,
            }

    def _assemble_output(
        self, doc, condensed_chapters: list[str],
        store: RunStore, input_path: str, out_path: str | None,
    ) -> str:
        """将浓缩结果组装为 EPUB/Markdown/TXT。"""
        from ebooklib import epub

        co = self.config.condense_output
        fmt = co.format

        # 确定输出路径
        if out_path:
            output_path = out_path
        else:
            base = os.path.splitext(os.path.basename(input_path))[0]
            out_dir = os.path.join(os.path.dirname(input_path), "output")
            os.makedirs(out_dir, exist_ok=True)
            ext = {"epub": ".condensed.epub", "markdown": ".condensed.md", "txt": ".condensed.txt"}
            output_path = os.path.join(out_dir, base + ext.get(fmt, ".condensed.epub"))

        if fmt == "markdown":
            self._write_markdown(doc, condensed_chapters, output_path)
        elif fmt == "txt":
            self._write_txt(doc, condensed_chapters, output_path)
        else:
            self._write_epub(doc, condensed_chapters, output_path)
        return output_path

    def _write_epub(self, doc, condensed_chapters: list[str], output_path: str) -> None:
        """组装浓缩版 EPUB。"""
        from ebooklib import epub

        book = epub.EpubBook()
        book.set_identifier(f"condensed-{doc.title}")
        book.set_title(f"{doc.title}（浓缩版）")
        book.set_language("zh")

        chapters = []
        toc_items = []
        for ci, text in enumerate(condensed_chapters):
            if not text.strip():
                continue
            ch_title = doc.chapters[ci].title or f"第 {ci + 1} 章"
            filename = f"chapter_{ci:03d}.xhtml"
            chapter = epub.EpubHtml(
                title=ch_title, file_name=filename, lang="zh"
            )
            # 简单段落化
            paragraphs = text.split("\n\n") if "\n\n" in text else [text]
            body = "".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())
            chapter.content = f"<h2>{ch_title}</h2>{body}"
            book.add_item(chapter)
            chapters.append(chapter)
            toc_items.append(chapter)

        book.toc = toc_items
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ["nav"] + chapters

        # 关于页
        if self.config.condense_output.about_page:
            about = epub.EpubHtml(title="关于此浓缩", file_name="about.xhtml", lang="zh")
            about.content = (
                "<h2>关于此浓缩</h2>"
                f"<p>本书由 Wenyi 浓缩工具自动生成，目标比例为原文的 "
                f"{int(self.config.condense.target_ratio * 100)}%。</p>"
                "<p>浓缩保留了原书的核心论证/叙事推进，去除了重复、学术辩护和过渡性内容。</p>"
            )
            book.add_item(about)
            book.spine.append(about)

        epub.write_epub(output_path, book)

    def _write_markdown(self, doc, condensed_chapters: list[str], output_path: str) -> None:
        """组装浓缩版 Markdown。"""
        parts = [f"# {doc.title}（浓缩版）\n"]
        for ci, text in enumerate(condensed_chapters):
            if not text.strip():
                continue
            ch_title = doc.chapters[ci].title or f"第 {ci + 1} 章"
            parts.append(f"\n## {ch_title}\n\n{text.strip()}\n")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(parts))

    def _write_txt(self, doc, condensed_chapters: list[str], output_path: str) -> None:
        """组装浓缩版纯文本。"""
        parts = [f"{doc.title}（浓缩版）\n{'=' * 40}\n"]
        for ci, text in enumerate(condensed_chapters):
            if not text.strip():
                continue
            ch_title = doc.chapters[ci].title or f"第 {ci + 1} 章"
            parts.append(f"\n{ch_title}\n{'-' * 30}\n\n{text.strip()}\n")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(parts))
