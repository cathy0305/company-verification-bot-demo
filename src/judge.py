"""엔노이아 게이트웨이 경유 Claude 종합 판정 (스펙 3-2, 4-3절).

엔노이아는 Anthropic/OpenAI 호환이 아니라 **자체 preset API**다::

    POST {ENNOIA_BASE_URL}
    headers: project / apiKey / Content-Type
    body:    { hash, params, messages:[{role, content:[{type:"text", text}]}] }

- ENNOIA_HASH 는 엔노이아 콘솔에서 만든 "검증 preset" 식별자.
  → 이 프로젝트용 preset 을 하나 만들고 그 hash 를 시크릿에 넣어야 한다.
- preset 안에 시스템 프롬프트/출력형식을 넣어둘 수도 있지만, 여기서는 이식성을
  위해 판정 지시·근거를 messages 본문에 직접 담아 보낸다.

출력은 {grade, reason, evidence_links} JSON 으로 강제.
**근거 없는 항목은 추정 금지(판단보류)** 를 프롬프트에 명시해 환각을 차단한다.
"""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, List, Optional

from .models import Company, RuleResult

if TYPE_CHECKING:
    import requests  # 타입 힌트 전용 (런타임 지연 import)

_TIMEOUT = 60
_MAX_RETRIES = 4  # 간헐적 5xx 대비
_DEFAULT_URL = "https://api.ennoia.so/api/preset/v2/chat/completions"

# 등급 정규화: 모델이 무엇으로 답하든 🟢/🟡/🔴 로 수렴.
_GRADE_ALIASES = {
    "🟢": "🟢", "green": "🟢", "정상": "🟢", "ok": "🟢", "pass": "🟢",
    "🟡": "🟡", "yellow": "🟡", "의심": "🟡", "suspect": "🟡", "review": "🟡",
    "🔴": "🔴", "red": "🔴", "위험": "🔴", "fraud": "🔴", "danger": "🔴",
}

# 이 프로젝트 엔노이아 preset이 받는 params 스키마.
# 회사 필드 + 1차 룰 결과(rule_flags) + 2차 근거(evidence) 를 채워 보낸다.
# 판정 지시·출력형식은 preset 템플릿 쪽에 정의돼 있다고 가정.
PARAM_KEYS = (
    "company_name", "platform_id", "country_code", "email", "phone",
    "homepage", "size", "rule_flags", "evidence",
)

# preset 템플릿이 시스템 프롬프트·출력형식을 자체적으로 갖고 있으므로
# (실측 응답이 grade/confidence/reason/red_flags/evidence/evidence_links 스키마),
# messages 본문은 판정을 트리거하는 짧은 지시만 둔다. 출력형식은 preset 에 위임.
INSTRUCTION = (
    "params 에 담긴 회사 정보와 1차 룰 결과(rule_flags), 2차 외부근거(evidence)를 "
    "바탕으로 이 해외 채용기업의 실존성·채용 진정성을 판정해줘. "
    "특히 evidence.reputation 에서 strong_presence_signals(LinkedIn 회사페이지·공식사이트 "
    "상위노출·Crunchbase·리뷰사이트·실채용공고)를 '실제 운영 중'의 핵심 근거로 보고, "
    "news_candidates·negative_candidates 는 회사명·국가·도메인과의 정합성을 먼저 따져 "
    "동명이인/무관 결과나 회사가 자사 채널에 올린 '취업사기 주의' 공지를 부정 신호로 오인하지 마라. "
    "법인등기·사업자등록 조회는 이번 검증 범위가 아니다 — 판정 사유에 "
    "'법인등기 미확인' 같은 표현을 넣지 말고, 도메인·웹사이트 운영·검색 평판 근거만으로 설명하라."
)


def _rule_flags_str(rule_result: RuleResult) -> str:
    """1차 룰 결과를 사람이 읽는 한 줄 요약으로 (rule_flags param 용)."""
    if not rule_result.findings:
        flags = "이상 없음"
    else:
        flags = "; ".join(
            f"{f.severity}:{f.rule}({f.detail})" for f in rule_result.findings
        )
    return f"score={rule_result.score}, verdict={rule_result.verdict} | {flags}"


def build_params(company: Company, rule_result: RuleResult, evidence: dict) -> dict:
    """preset params 스키마(PARAM_KEYS)에 맞춰 문자열 값으로 채운다.

    개인정보 최소화: 검증(검색·판정)엔 이메일 도메인·전화 국가번호만 넘긴다.
    전체 이메일/전화번호는 엑셀에만 저장하고 외부로 보내지 않는다.
    """
    from .rules import calling_code_of  # 지연 import (순환 방지)

    cc = calling_code_of(company.mobile)
    return {
        "company_name": company.company_name or "",
        "platform_id": str(company.platform_id or ""),
        "country_code": company.country_code or "",
        "email": company.email_domain() or "",   # 도메인만 (개인정보 최소화)
        "phone": f"+{cc}" if cc else "",          # 국가번호만
        "homepage": company.homepage or "",
        "size": company.size or "",
        "rule_flags": _rule_flags_str(rule_result),
        "evidence": json.dumps(evidence, ensure_ascii=False),
    }


def _collect_links(evidence: dict) -> List[str]:
    links: List[str] = []
    for axis in evidence.values():
        if isinstance(axis, dict):
            links.extend(axis.get("links") or [])
    return list(dict.fromkeys(l for l in links if l))


def judge(company: Company, rule_result: RuleResult, evidence: dict) -> dict:
    """엔노이아 Claude 종합 판정. 반환: {grade, reason, evidence_links}."""
    api_key = os.environ.get("ENNOIA_API_KEY")
    project = os.environ.get("ENNOIA_PROJECT")
    preset_hash = os.environ.get("ENNOIA_HASH")
    url = os.environ.get("ENNOIA_BASE_URL", _DEFAULT_URL)
    if not (api_key and project and preset_hash):
        raise RuntimeError(
            "ENNOIA_API_KEY / ENNOIA_PROJECT / ENNOIA_HASH 가 필요합니다. "
            "엔노이아 콘솔에서 검증 preset 을 만들고 hash 를 시크릿에 넣으세요."
        )

    import time

    import requests  # 지연 import

    body = {
        "hash": preset_hash,
        "params": build_params(company, rule_result, evidence),
        "messages": [{
            "role": "user",
            "content": [{"type": "text", "text": INSTRUCTION}],
        }],
    }
    headers = {
        "project": project, "apiKey": api_key,
        "Content-Type": "application/json; charset=utf-8",
    }
    # 엔노이아 게이트웨이는 간헐적 5xx 가 있어 재시도(지수 백오프).
    resp = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.post(url, json=body, timeout=_TIMEOUT, headers=headers)
        except requests.RequestException:
            if attempt == _MAX_RETRIES - 1:
                raise
            time.sleep(1.0 + attempt)
            continue
        if resp.status_code < 500:
            break
        if attempt < _MAX_RETRIES - 1:
            time.sleep(1.0 + attempt)
    resp.raise_for_status()

    text = _extract_text(resp)
    parsed = _extract_json(text) or {}

    grade = _normalize_grade(parsed.get("grade"))
    links = parsed.get("evidence_links") or _collect_links(evidence)
    return {
        "grade": grade,
        "reason": parsed.get("reason") or (text[:500] if not parsed else ""),
        "evidence_links": links,
        "confidence": parsed.get("confidence"),   # preset 제공 (high/medium/low)
        "red_flags": parsed.get("red_flags") or [],
        "raw": text,
    }


# --- 응답 파싱 (엔노이아 preset 응답 구조 미확정 → 방어적 추출) ----------------

def _extract_text(resp: requests.Response) -> str:
    """응답에서 모델 생성 텍스트를 최대한 끄집어낸다."""
    try:
        data = resp.json()
    except ValueError:
        return resp.text or ""

    # 흔한 위치들을 순서대로 시도
    for getter in (
        lambda d: d["choices"][0]["message"]["content"],   # OpenAI 호환
        lambda d: d["content"],                            # Anthropic 호환
        lambda d: d["message"]["content"],
        lambda d: d["data"]["content"],
        lambda d: d["result"],
        lambda d: d["output"],
        lambda d: d["text"],
        lambda d: d["message"],
    ):
        try:
            val = getter(data)
        except (KeyError, IndexError, TypeError):
            continue
        flat = _flatten_content(val)
        if flat:
            return flat
    # 마지막 수단: 전체를 문자열로
    return json.dumps(data, ensure_ascii=False)


def _flatten_content(val) -> str:
    """content 가 문자열 / [{type:text,text}] / 중첩이어도 텍스트로 평탄화."""
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        return val.get("text") or _flatten_content(val.get("content"))  # type: ignore
    if isinstance(val, list):
        return "".join(_flatten_content(v) for v in val)
    return ""


def _extract_json(text: str) -> Optional[dict]:
    """텍스트에서 첫 번째 JSON 오브젝트를 중괄호 매칭으로 추출."""
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def _normalize_grade(raw: Optional[str]) -> str:
    if not raw:
        return "🟡"  # 불명확하면 사람 검토로
    key = raw.strip().lower()
    if key in _GRADE_ALIASES:
        return _GRADE_ALIASES[key]
    for alias, grade in _GRADE_ALIASES.items():
        if alias in key:
            return grade
    return "🟡"
