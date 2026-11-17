"""표시용 텍스트 포맷 (시트·슬랙 공용)."""

from __future__ import annotations

import re

# 문장 끝(. ! ? 。) + 공백에서 분리
_SENT_SPLIT = re.compile(r"(?<=[.!?。])\s+")


def bulletize(text: str) -> str:
    """판정 사유 줄글 → 문장별 '- ' 불릿 줄바꿈.

    "A입니다. B입니다." → "- A입니다.\n- B입니다."
    가로로 긴 한 줄을 세로로 읽기 좋게 만든다.
    """
    if not text:
        return ""
    parts = [p.strip() for p in _SENT_SPLIT.split(text.strip()) if p.strip()]
    return "\n".join("- " + p for p in parts)
