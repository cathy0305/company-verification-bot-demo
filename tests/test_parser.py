"""parser 단위 테스트 — pytest 없이도 실행 가능 (python -m tests.test_parser)."""

from __future__ import annotations

from src.parser import domain_of, parse_message

GOOD = """\
acmeglobal(10001)
Size        11~50
Email       hr@acme-global.example
Mobile      +8210****
CountryCode KR
Homepage    https://www.acme-global.example
"""

# Slack mrkdwn 링크가 섞인 형태
SLACK_LINKED = """\
<https://admin.example.com/companies/10001|acmeglobal(10001)>
Size        11~50
Email       <mailto:hr@acme-global.example|hr@acme-global.example>
Mobile      +8210****
CountryCode KR
Homepage    <https://www.acme-global.example|acme-global.example>
"""


def test_plain_text():
    c = parse_message(GOOD)
    assert c.company_name == "acmeglobal"
    assert c.platform_id == 10001
    assert c.size == "11~50"
    assert c.email == "hr@acme-global.example"
    assert c.email_domain() == "acme-global.example"
    assert c.mobile.startswith("+8210")
    assert c.country_code == "KR"
    assert c.homepage == "https://www.acme-global.example"


def test_slack_linked():
    c = parse_message(SLACK_LINKED)
    assert c.company_name == "acmeglobal"
    assert c.platform_id == 10001          # 링크에서 보조 추출 (hq_url 은 저장 안 함)
    assert c.email == "hr@acme-global.example"
    assert c.homepage == "https://www.acme-global.example"


# 소스 봇 메시지 형식 예시: 헤더 줄 + 푸터 줄 + 공백 포함 사명 + 중복 카드
SOURCE_BOT = """\
Company approval request
Sunrise Trading Co(10002)
Size 1~4
Email sunrise.owner@gmail.com
Mobile +61400000000
CountryCode AU
AdminConsole | Jun 3rd | Added by SOURCE BOT
Company approval request
Sunrise Trading Co(10002)
Size 1~4
Email sunrise.owner@gmail.com
Mobile +61400000000
CountryCode AU
"""

# 복붙 시 라벨·값이 붙는 경우 (공백 소실) — 견뎌야 함
CONCATENATED = """\
Company approval request
Sunrise Trading Co(10002)
Size1~4
Emailsunrise.owner@gmail.com
Mobile+61400000000
CountryCodeAU
"""


def test_source_bot_real_format():
    c = parse_message(SOURCE_BOT)
    assert c.company_name == "Sunrise Trading Co"   # 헤더/푸터에 안 흔들림, 공백 보존
    assert c.platform_id == 10002
    assert c.size == "1~4"
    assert c.email == "sunrise.owner@gmail.com"
    assert c.mobile == "+61400000000"
    assert c.country_code == "AU"
    assert c.homepage is None                       # companyurl 아직 없음


def test_concatenated_labels():
    c = parse_message(CONCATENATED)
    assert c.company_name == "Sunrise Trading Co"
    assert c.email == "sunrise.owner@gmail.com"
    assert c.mobile == "+61400000000"
    assert c.country_code == "AU"


def test_company_url_field():
    c = parse_message(SOURCE_BOT.rstrip() + "\nCompanyUrl https://sunrise-trading.example")
    assert c.homepage == "https://sunrise-trading.example"


# 확정 형식: 라벨과 값이 별도 줄 + Industry/Member/Website 신규 필드
MULTILINE = """\
Company approval request
샘플스포츠클럽(10004)
Size
5001~10000
Email
recruit@sample-sports.example
Mobile
+821000000000
CountryCode
HK
Website
https://sample-sports.example
Industry
국제, 외국기관
Member
홍길동
"""


def test_multiline_label_value():
    c = parse_message(MULTILINE)
    assert c.company_name == "샘플스포츠클럽"
    assert c.platform_id == 10004
    assert c.size == "5001~10000"
    assert c.email == "recruit@sample-sports.example"
    assert c.mobile == "+821000000000"
    assert c.country_code == "HK"
    assert c.homepage == "https://sample-sports.example"     # Website → homepage
    assert c.industry == "국제, 외국기관"
    assert c.member == "홍길동"


# 소스 봇 메시지 형식 예시: text 비어있고 attachments[].fields 에 카드
SOURCE_BOT_MSG = {
    "subtype": "bot_message", "text": "", "ts": "1781024941.646359",
    "app_id": "A0000000000", "bot_id": "B0000000000",
    "attachments": [{
        "title": "Sample Beauty Shop(10003)",
        "title_link": "https://admin.example.com/companies/10003",
        "author_name": "Company approval request",
        "fields": [
            {"title": "Size", "value": "1~4", "short": False},
            {"title": "Email",
             "value": "<mailto:samplebeauty.shop@gmail.com|samplebeauty.shop@gmail.com>"},
            {"title": "Mobile", "value": "+12025550100"},
            {"title": "CountryCode", "value": "US"},
        ],
    }],
}


def test_parse_attachment_real():
    from src.parser import parse_slack_message
    c = parse_slack_message(SOURCE_BOT_MSG)
    assert c.company_name == "Sample Beauty Shop"
    assert c.platform_id == 10003
    assert c.size == "1~4"
    assert c.email == "samplebeauty.shop@gmail.com"   # mailto 언랩
    assert c.mobile == "+12025550100"
    assert c.country_code == "US"
    assert c.slack_ts == "1781024941.646359"


def test_domain_of():
    assert domain_of("https://www.acme-global.example") == "acme-global.example"
    assert domain_of("hr@acme-global.example") == "acme-global.example"
    assert domain_of("acme-global.example/careers") == "acme-global.example"
    assert domain_of(None) is None


def _run():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("test_parser: OK")


if __name__ == "__main__":
    _run()
