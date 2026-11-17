"""process_company 분기 테스트 (외부 키 없이 단락되는 경로)."""

from __future__ import annotations

from src.main import build_reply, process_company
from src.models import Company


def test_kr_is_domestic_redirect():
    # 기준 #1: KR 이면 검증 없이 국내 안내
    c = Company(company_name="국내회사", country_code="KR", email="a@acme.co.kr")
    rec = process_company(c)
    assert rec["grade"] == "🇰🇷"
    assert "국내" in rec["reason"]
    reply = build_reply(rec, "http://sheet")
    assert "국내 기업 안내" in reply


def test_personal_email_red():
    # 기준 #2: 개인메일이면 즉시 🔴 (KR 아님)
    c = Company(company_name="X", country_code="US", email="a@gmail.com",
                mobile="+12345", homepage=None)
    rec = process_company(c)
    assert rec["grade"] == "🔴"


def _run():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("test_main: OK")


if __name__ == "__main__":
    _run()
