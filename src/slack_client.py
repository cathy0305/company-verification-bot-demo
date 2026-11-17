"""Slack 메시지 수집 + 완료 알림 (스펙 4-1절).

필요 스코프(Bot Token): channels:history (비공개면 groups:history),
chat:write, reactions:write (등급 이모지 리액션), channels:read.
(사람 사용자 정보를 안 읽으므로 users:read 불필요)

발신자 필터(둘 다 선택):
  SLACK_SOURCE_APP_ID  — 실제 운영 시 소스 봇만 (예: A0000000000 = source-bot)
  SLACK_SOURCE_BOT_ID  — bot_id 로 거를 때
  → 둘 다 비우면 채널의 모든 사람·봇 메시지를 통과 (테스트 채널용).
"""

from __future__ import annotations

import os
from typing import List, Optional


def _client():
    from slack_sdk import WebClient  # 지연 import
    return WebClient(token=os.environ["SLACK_BOT_TOKEN"])


def fetch_history(channel: str, oldest: Optional[str] = None,
                  limit: int = 200, max_messages: int = 1000) -> List[dict]:
    """채널 히스토리를 oldest→newest 정렬로 반환 (cursor 페이지네이션)."""
    client = _client()
    out: List[dict] = []
    cursor = None
    while True:
        kw = {"channel": channel, "limit": min(200, limit)}
        if oldest:
            kw["oldest"] = oldest
        if cursor:
            kw["cursor"] = cursor
        resp = client.conversations_history(**kw)
        out.extend(resp.get("messages", []))
        if len(out) >= max_messages:
            break
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    out.sort(key=lambda m: float(m.get("ts", "0")))
    return out


def passes_source_filter(msg: dict) -> bool:
    """환경변수 기반 발신자 필터. 설정 안 하면 통과(테스트용)."""
    app_id = os.environ.get("SLACK_SOURCE_APP_ID")
    bot_id = os.environ.get("SLACK_SOURCE_BOT_ID")
    if app_id and msg.get("app_id") != app_id:
        return False
    if bot_id and msg.get("bot_id") != bot_id:
        return False
    # 입퇴장/핀 등 시스템 메시지 제외
    if msg.get("subtype") in ("channel_join", "channel_leave",
                              "channel_topic", "channel_purpose", "pinned_item"):
        return False
    return True


def fetch_new(channel: str, after_ts: Optional[str]) -> List[dict]:
    """after_ts 이후 새 메시지(발신자 필터 적용), 시간 오름차순.

    conversations.history(oldest=after_ts) 폴링. 경계 메시지(이미 처리한 ts)는 제외.
    """
    msgs = fetch_history(channel, oldest=after_ts)
    out = []
    for m in msgs:
        if after_ts and m.get("ts") == after_ts:
            continue
        if not passes_source_filter(m):
            continue
        out.append(m)
    return out


def latest_ts(channel: str) -> Optional[str]:
    """채널의 가장 최근 메시지 ts (첫 실행 워터마크 초기화용)."""
    h = _client().conversations_history(channel=channel, limit=1)
    msgs = h.get("messages", [])
    return msgs[0].get("ts") if msgs else None


def notify(channel: str, text: str, thread_ts: Optional[str] = None) -> None:
    """chat.postMessage. thread_ts 를 주면 그 메시지의 스레드 reply 로 단다."""
    kw = {"channel": channel, "text": text}
    if thread_ts:
        kw["thread_ts"] = thread_ts
    _client().chat_postMessage(**kw)


# 등급 이모지 → 슬랙 리액션 이름 (유니코드가 아니라 이름을 써야 함)
GRADE_REACTION = {
    "🟢": "large_green_circle",
    "🟡": "large_yellow_circle",
    "🔴": "red_circle",
    "🇰🇷": "kr",
}


def react(channel: str, ts: str, grade: Optional[str]) -> None:
    """검증 등급에 맞는 이모지를 원본 메시지에 리액션으로 단다.

    grade(🟢/🟡/🔴/🇰🇷)에 대응하는 이름이 없으면 조용히 건너뛴다.
    이미 같은 리액션이 있으면(already_reacted) 에러를 무시한다.
    """
    name = GRADE_REACTION.get((grade or "").strip())
    if not name or not ts:
        return
    try:
        _client().reactions_add(channel=channel, timestamp=ts, name=name)
    except Exception as e:  # already_reacted 등은 치명적이지 않음
        if "already_reacted" not in str(e):
            raise
