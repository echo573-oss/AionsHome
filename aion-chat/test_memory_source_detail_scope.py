"""记忆「原文挂载」的粒度守护。

`[记忆来源原文]` 段以前会把**所有**召回记忆的原文都挂进 prompt。
实测一次误触发多花 12000+ tokens，而且挂进来的原文与用户所问无关
（用户问「以赛亚书那句话怎么说」，挂进来的是「相处偏好」「深夜思考记忆系统」）。

现在只挂最相关的那一条；且要取「第一条有来源的」而非固定 `recalled[0]`——
最相关的那条可能是没有来源的旧记忆，固定取它会把原文整段丢掉。
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from context_builder import build_memory_blocks


def _mem(mem_id, score, has_source=True):
    return {
        "id": mem_id,
        "content": f"记忆正文 {mem_id}",
        "type": "daily",
        "created_at": 1000.0,
        "score": score,
        "vec_sim": score,
        "kw_score": 0.0,
        "importance": 0.5,
        "keywords": "",
        "source_start_ts": 900.0 if has_source else None,
        "source_end_ts": 950.0 if has_source else None,
        "source_conv": "conv",
        "source_msg_id": '["private:m1"]' if has_source else None,
        "evidence_summary": "",
    }


class SourceDetailScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_top_relevant_memory_gets_source_details(self):
        captured = []

        async def fake_fetch(memories, keywords):
            captured.extend(memories)
            return "来源原文正文"

        candidates = [
            _mem("mem-top", 0.9, has_source=True),
            _mem("mem-second", 0.8, has_source=True),
            _mem("mem-third", 0.7, has_source=True),
        ]

        with (
            patch("context_builder.fetch_source_details", new=fake_fetch),
            patch("context_builder.recall_memories", new=AsyncMock(return_value=(candidates, candidates))),
            patch("context_builder.build_surfacing_memories", new=AsyncMock(return_value=([], set()))),
        ):
            await build_memory_blocks(
                "今天的对话讲了什么细节",
                recent_messages=[],
                use_main_memories=True,
                digest_result={
                    "is_search_needed": False,
                    "require_detail": True,
                    "keywords": ["细节"],
                    "topic": "细节",
                },
            )

        # 只应挂载 1 条，而不是全部召回
        self.assertEqual(1, len(captured))
        self.assertEqual("mem-top", captured[0]["id"])

    async def test_falls_back_to_the_next_memory_when_top_has_no_source(self):
        """最相关的记忆若没有来源，要顺延到下一条有来源的，而不是整段丢掉原文。"""
        captured = []

        async def fake_fetch(memories, keywords):
            captured.extend(memories)
            return "来源原文正文"

        candidates = [
            _mem("mem-no-source", 0.9, has_source=False),
            _mem("mem-second", 0.8, has_source=True),
            _mem("mem-third", 0.7, has_source=True),
        ]

        with (
            patch("context_builder.fetch_source_details", new=fake_fetch),
            patch("context_builder.recall_memories", new=AsyncMock(return_value=(candidates, candidates))),
            patch("context_builder.build_surfacing_memories", new=AsyncMock(return_value=([], set()))),
        ):
            await build_memory_blocks(
                "细节",
                recent_messages=[],
                use_main_memories=True,
                digest_result={
                    "is_search_needed": False,
                    "require_detail": True,
                    "keywords": [],
                    "topic": "细节",
                },
            )

        # 顺延到第一条有来源的
        self.assertEqual(1, len(captured))
        self.assertEqual("mem-second", captured[0]["id"])


if __name__ == "__main__":
    unittest.main()
