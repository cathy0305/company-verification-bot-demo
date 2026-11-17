"""judge 응답 파싱 단위 테스트 (네트워크/requests 불필요)."""

from __future__ import annotations

from src.judge import (
    _extract_json,
    _flatten_content,
    _normalize_grade,
    build_params,
)
from src.models import Company, RuleResult
from src.parser import parse_message
from src.rules import run_rules


def test_extract_json_plain():
    j = _extract_json('{"grade":"🟢","reason":"ok","evidence_links":[]}')
    assert j["grade"] == "🟢"


def test_extract_json_in_codeblock_with_noise():
    text = '판정 결과입니다:\n```json\n{"grade": "🔴", "reason": "사기 패턴"}\n```\n끝.'
    j = _extract_json(text)
    assert j["grade"] == "🔴"
    assert j["reason"] == "사기 패턴"


def test_extract_json_nested_braces():
    text = 'x {"grade":"🟡","reason":"a {b} c","meta":{"k":1}} y'
    j = _extract_json(text)
    assert j["grade"] == "🟡"
    assert j["meta"] == {"k": 1}


def test_extract_json_none():
    assert _extract_json("브레이스 없음") is None
    assert _extract_json("") is None


def test_normalize_grade():
    assert _normalize_grade("🟢") == "🟢"
    assert _normalize_grade("RED") == "🔴"
    assert _normalize_grade("의심") == "🟡"
    assert _normalize_grade("green light") == "🟢"   # 부분일치
    assert _normalize_grade(None) == "🟡"            # 불명확 → 사람검토
    assert _normalize_grade("ㅁㄴㅇㄹ") == "🟡"      # 미매칭 → 사람검토


def test_flatten_content():
    assert _flatten_content("hi") == "hi"
    assert _flatten_content([{"type": "text", "text": "a"},
                             {"type": "text", "text": "b"}]) == "ab"
    assert _flatten_content({"content": [{"text": "x"}]}) == "x"


def test_build_params_maps_preset_schema():
    company = parse_message(
        "Xqzrtvbn Ltd\nEmail xqzrtvbn@gmail.com\nMobile +60\nCountryCode GB"
    )
    rr = run_rules(company)
    evidence = {"corporate": {"status": "not_found"}}
    p = build_params(company, rr, evidence)
    # preset 이 받는 9개 키가 모두 채워지고 전부 문자열
    assert set(p) == {"company_name", "platform_id", "country_code", "email",
                      "phone", "homepage", "size", "rule_flags", "evidence"}
    assert all(isinstance(v, str) for v in p.values())
    assert p["company_name"] == "Xqzrtvbn Ltd"
    assert p["country_code"] == "GB"
    assert p["phone"] == "+60"                       # mobile → phone 매핑
    assert "free_email_domain" in p["rule_flags"]    # 1차 룰 요약 포함
    assert "not_found" in p["evidence"]              # 2차 근거 직렬화 포함


def _run():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("test_judge: OK")


if __name__ == "__main__":
    _run()
