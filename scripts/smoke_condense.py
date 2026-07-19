"""端到端冒烟测试：用 fake LLM 跑通 condense 全流程并产出 EPUB。

不依赖任何 API key。通过给 FakeClient 注入一个按 system 提示路由的 handler，
为每个 agent 阶段返回结构合法的 JSON，从而验证整条流水线的接线是否正确。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

# 让脚本能 import trans_novel
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ebooklib import epub  # noqa: E402

from trans_novel.config import Config  # noqa: E402
from trans_novel.llm.providers.fake import FakeClient  # noqa: E402
from trans_novel.pipeline.condense_orchestrator import CondenseOrchestrator  # noqa: E402


CONDENSED_TEXT = (
    "感受质指的是主观体验的质感，比如看到红色时那种"
    "独一无二的感觉。它无法被完全还原为物理描述，这正是"
    "论证的枢纽所在。如果我们承认感受质存在，就必须解释它"
    "如何与物理世界关联；若否认它，则要说明为何体验看起来"
    "如此真实。无论哪条路，都把我们引向意识的核心难题。"
) * 2  # ~300 字，确保润色安全检查通过


def make_handler():
    """按 system 提示关键词路由，返回各阶段所需的合法 JSON 字符串。"""

    def handler(messages, tier, json_mode):
        sys_msg = messages[0]["content"] if messages else ""

        if "阅读策略分析师" in sys_msg:  # type_detector
            return json.dumps({
                "book_type": "argumentative",
                "sub_type": "哲学论证",
                "driving_force": "逻辑推理的惯性",
                "condensation_strategy": "preserve_argument_chain",
                "reasoning": "文本提出论点并逐步论证。",
            }, ensure_ascii=False)

        if "结构分析师" in sys_msg:  # structure_analyzer
            return json.dumps({
                "central_question": "意识能否被物理主义解释？",
                "structure_type": "问题驱动型论证",
                "arc": "从直觉泵出发逐层逼近意识难题",
                "load_bearing": "感受质论证与反驳",
                "dispensable": "学术史回顾与重复举例",
                "key_moves": [
                    {"chapter_range": "1-2", "function": "提出感受质概念"},
                    {"chapter_range": "3", "function": "反驳与回应"},
                ],
                "concepts": [
                    {
                        "term": "感受质",
                        "definition_in_book": "主观体验的不可还原质感",
                        "role": "全书论证枢纽",
                    }
                ],
            }, ensure_ascii=False)

        if "章节规划" in sys_msg:  # chapter_role
            return json.dumps({
                "role": "提出并界定核心概念",
                "argument_function": "立论",
                "retention_plan": "保留感受质的定义与首个论证",
                "key_passages": [{"paragraph_range": "1-2"}],
                "dispensable": [],
                "concepts_used": ["感受质"],
                "concepts_introduced": ["感受质"],
            }, ensure_ascii=False)

        if "书本浓缩师" in sys_msg:  # condenser
            return json.dumps({"condensed": CONDENSED_TEXT}, ensure_ascii=False)

        if "文体编辑" in sys_msg:  # flow_polisher
            return json.dumps({"polished": CONDENSED_TEXT}, ensure_ascii=False)

        if "逻辑审校员" in sys_msg:  # logic_reviewer
            return json.dumps({"issues": []}, ensure_ascii=False)

        if "概念追踪器" in sys_msg:  # concept extractor
            return json.dumps({"concepts": [
                {"term": "感受质", "definition": "主观体验质感", "role": "枢纽"}
            ]}, ensure_ascii=False)

        return "{}"

    return handler


def build_tiny_epub(path: str) -> None:
    """构造一本 3 章的迷你 EPUB 作为输入。"""
    book = epub.EpubBook()
    book.set_identifier("smoke-test")
    book.set_title("意识测试书")
    book.set_language("zh")
    chapters = []
    for i in range(3):
        ch = epub.EpubHtml(title=f"第 {i+1} 章", file_name=f"c{i}.xhtml", lang="zh")
        body = (
            f"<p>这是第 {i+1} 章的原文。感受质是主观体验的质感。</p>"
            "<p>作者在此展开了漫长的论证，举了许多例子，反复重申观点，"
            "并回顾了学术史。这些内容大部分可以在浓缩时省略。</p>"
        ) * 4
        ch.content = f"<h2>第 {i+1} 章</h2>{body}"
        book.add_item(ch)
        chapters.append(ch)
    book.toc = chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + chapters
    epub.write_epub(path, book)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="wenyi_smoke_")
    src = os.path.join(tmp, "test_book.epub")
    build_tiny_epub(src)

    config = Config()
    config.state_dir = os.path.join(tmp, "state")
    config.condense_output.format = "epub"

    client = FakeClient(handler=make_handler())
    orch = CondenseOrchestrator(config, client=client)

    out_path = os.path.join(tmp, "out.condensed.epub")
    result = orch.run(src, out_path=out_path)

    print("book_type:", result["book_type"])
    print("issues:", len(result["issues"]))
    print("output:", result["output"])
    print("llm calls:", len(client.calls))

    assert os.path.isfile(out_path), "浓缩版 EPUB 未生成"
    assert os.path.getsize(out_path) > 0, "浓缩版 EPUB 为空"
    # 验证 EPUB 可被重新打开且含章节
    reopened = epub.read_epub(out_path)
    items = [it for it in reopened.get_items() if "chapter_" in it.get_name()]
    print("condensed chapter items in epub:", [it.get_name() for it in items])
    assert len(items) >= 3, f"期望至少 3 章浓缩内容，实际 {len(items)}"

    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
