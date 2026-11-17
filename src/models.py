"""공용 데이터 모델.

parser → rules → enrich → judge 단계 사이에서 주고받는 구조체를 한곳에 모은다.
스펙 2절(입력 필드)과 3절(검증 결과)에 대응.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Company:
    """소스 슬랙봇 메시지 1건을 파싱한 결과 (스펙 2절)."""

    company_name: Optional[str] = None       # 라인 머리의 회사명 (법인 접미사 포함)
    platform_id: Optional[int] = None          # (10001) 괄호 안 숫자 (링크 와도 보조 추출)
    size: Optional[str] = None               # Size 라인 (예: "11~50")
    email: Optional[str] = None              # Email 라인
    mobile: Optional[str] = None             # Mobile 라인 (예: "+8210****")
    country_code: Optional[str] = None       # CountryCode 라인 (ISO2)
    homepage: Optional[str] = None           # Homepage/Website 라인
    industry: Optional[str] = None           # Industry 라인 (업종)
    member: Optional[str] = None             # Member 라인 (담당자)

    # 메타: 원본 추적/디버깅용
    slack_ts: Optional[str] = None           # 원본 메시지 타임스탬프
    raw_text: Optional[str] = None           # 원본 텍스트 (파싱 실패 분석용)

    def email_domain(self) -> Optional[str]:
        if not self.email or "@" not in self.email:
            return None
        return self.email.split("@", 1)[1].strip().lower() or None


# 1차 룰베이스 결과 -------------------------------------------------------------

Severity = str  # "red" | "yellow" | "info"


@dataclass
class RuleFinding:
    """룰 1건의 적발 결과."""

    rule: str            # 룰 식별자 (예: "free_email_domain")
    severity: Severity   # red / yellow / info
    score_delta: int     # 점수 가감 (음수 = 감점)
    detail: str          # 사람이 읽는 사유


@dataclass
class RuleResult:
    """1차(룰베이스) 종합 결과 (스펙 3-1절)."""

    score: int = 100                         # 시작 100, 룰별 가감
    findings: List[RuleFinding] = field(default_factory=list)
    # likely_ok: 1차 통과 → 2차 진행
    # needs_review: 정보부족/신호충돌 → 2차에서 확정
    # likely_fraud: 명백 🔴 → 외부호출 없이 거를 수 있음
    verdict: str = "likely_ok"
    skip_external: bool = False              # True면 2차 외부검증 생략 가능

    def red_flags(self) -> List[RuleFinding]:
        return [f for f in self.findings if f.severity == "red"]
