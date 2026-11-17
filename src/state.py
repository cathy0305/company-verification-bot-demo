"""마지막 처리 ts 저장/로드 (스펙 4-2절, 중복 방지).

기본 구현은 로컬 JSON 파일. GitHub Actions에서는 매 실행이 새 환경이므로
이 파일을 ① 리포 커밋 또는 ② Actions 캐시/Gist 로 영속화해야 한다(스펙 4-2).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_DEFAULT_PATH = os.environ.get("STATE_PATH", ".state/last_ts.json")


def _path() -> Path:
    return Path(_DEFAULT_PATH)


def load_ts() -> Optional[str]:
    """마지막으로 처리한 Slack 메시지 ts. 없으면 None."""
    p = _path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("last_ts")
    except (json.JSONDecodeError, OSError):
        return None


def save_ts(ts: str) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"last_ts": ts}, ensure_ascii=False))
