"""rules 단위 테스트 — 스펙 3-1절의 두 예시로 검증."""

from __future__ import annotations

from src.parser import parse_message
from src.rules import calling_code_of, run_rules

# 정합 케이스: 도메인·국가 일치 → 1차 통과
GOOD = """\
acmeglobal(10001)
Size        11~50
Email       hr@acme-global.example
Mobile      +8210****
CountryCode KR
Homepage    https://www.acme-global.example
"""

# 의심 케이스: 무료도메인 + 국가/전화 불일치 + 부자연 사명
BAD = """\
Xqzrtvbn Ltd
Size        11~50
Email       xqzrtvbn@gmail.com
Mobile      +60123456789
CountryCode GB
"""


def test_good_passes():
    r = run_rules(parse_message(GOOD))
    assert r.verdict == "likely_ok"
    assert r.skip_external is False
    assert r.red_flags() == []
    assert r.score >= 80


def test_bad_is_obvious_fraud():
    r = run_rules(parse_message(BAD))
    rules_hit = {f.rule for f in r.findings}
    assert "free_email_domain" in rules_hit           # 🔴 무료도메인
    assert "phone_country_mismatch" in rules_hit       # +60(MY) vs GB
    assert r.verdict == "likely_fraud"
    assert r.skip_external is True                      # 외부호출 없이 거름


# 기준 #2: 개인/무료 이메일(gmail)이면 전화·국가가 정합해도 즉시 🔴 컷.
REAL_GMAIL = """\
Company approval request
Sunrise Trading Co(10002)
Size 1~4
Email sunrise.owner@gmail.com
Mobile +61400000000
CountryCode AU
"""


def test_free_email_is_immediately_rejected():
    r = run_rules(parse_message(REAL_GMAIL))
    hit = {f.rule for f in r.findings}
    assert "free_email_domain" in hit               # gmail 적발
    assert r.skip_external is True                   # 외부검증 없이 즉시 컷
    assert r.verdict == "likely_fraud"              # → 🔴


def test_calling_code():
    assert calling_code_of("+8210****") == "82"
    assert calling_code_of("+60123456789") == "60"
    assert calling_code_of("+1 415 555 0100") == "1"
    assert calling_code_of("010-1234-5678") is None    # + 없음


def _run():
    test_good_passes()
    test_bad_is_obvious_fraud()
    test_calling_code()
    print("test_rules: OK")


if __name__ == "__main__":
    _run()
