"""서류 제출 요청 메일 초안 생성 (검증 🟢/🟡 기업용).

검증 결과가 🟢/🟡 이면, 원본 카드 스레드에 '메일 발송 안내' 초안을 함께 게시한다.
담당자가 내용을 확인한 뒤 직접 발송하는 구조다 (자동 발송하지 않음).
인사말은 회사명이 아니라 담당자(Member) 이름을 쓰고, 마감일은 발송일 +7일로 계산한다.

※ 공개용 데모: 아래 문구·서비스명·링크는 모두 예시이며 실제 회사 정책/문구가 아니다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
SUBJECT = "[Example Platform] Overseas Company Registration — Document Submission Request"


def _due() -> str:
    return (datetime.now(KST) + timedelta(days=7)).strftime("%B %d, %Y")


def _body(greeting: str, deadline: str) -> str:
    return f"""Dear {greeting},
This is the Partner Team at Example Platform.

We have received your application to register as an overseas company. Thank you for applying.

As part of our registration review, we kindly ask you to submit the following document:

■ Required Document
- (예시) Proof of business registration or recent financial statements

Please reply to this email with the document attached by {deadline} (within 7 days).
If you need more time or have any questions, please let us know.

Best regards,
Partner Team, Example Platform
https://example.com"""


def build_mail_reply(company, grade: str):
    """🟢/🟡 이면 '메일 발송 안내' reply 텍스트, 아니면 None.

    인사말 = 담당자(member) 이름 우선, 없으면 회사명.
    """
    if grade not in ("🟢", "🟡"):
        return None
    greeting = (company.member or company.company_name or "").strip() or "Sir or Madam"
    body = _body(greeting, _due())
    return (f"📧 *메일 발송 안내 (담당자 확인 후 발송)* — 수신: `{company.email or ''}`\n"
            f"*Subject:* {SUBJECT}\n\n{body}")
