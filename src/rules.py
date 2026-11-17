"""1차 룰베이스 검증 (스펙 3-1절).

입력 필드만으로 명백한 사기 신호를 잡아 가산/감산한다. **외부 호출 0.**
명백한 🔴는 여기서 걸러 외부 API 비용/지연을 아낀다.
"""

from __future__ import annotations

import re
from typing import List, Optional, Set

from .models import Company, RuleFinding, RuleResult
from .parser import domain_of

# --- 무료 이메일 도메인 (스펙: gmail/outlook/hotmail/naver 등) -----------------
FREE_EMAIL_DOMAINS: Set[str] = {
    "gmail.com", "googlemail.com",
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "yahoo.co.kr", "ymail.com",
    "naver.com", "daum.net", "hanmail.net", "nate.com", "kakao.com",
    "icloud.com", "me.com",
    "aol.com", "gmx.com", "mail.com", "proton.me", "protonmail.com",
    "yandex.com", "qq.com", "163.com", "126.com",
}

# --- 국제전화 calling code → 가능한 ISO2 국가 -----------------------------------
# 모호한 코드(+1=US/CA, +7=RU/KZ 등)는 집합으로 둬서 "불일치"만 보수적으로 판정.
CALLING_CODE_TO_ISO2 = {
    "1": {"US", "CA"},
    "7": {"RU", "KZ"},
    "20": {"EG"}, "27": {"ZA"}, "30": {"GR"}, "31": {"NL"}, "32": {"BE"},
    "33": {"FR"}, "34": {"ES"}, "36": {"HU"}, "39": {"IT"},
    "40": {"RO"}, "41": {"CH"}, "43": {"AT"}, "44": {"GB"}, "45": {"DK"},
    "46": {"SE"}, "47": {"NO"}, "48": {"PL"}, "49": {"DE"},
    "51": {"PE"}, "52": {"MX"}, "53": {"CU"}, "54": {"AR"}, "55": {"BR"},
    "56": {"CL"}, "57": {"CO"}, "58": {"VE"},
    "60": {"MY"}, "61": {"AU"}, "62": {"ID"}, "63": {"PH"}, "64": {"NZ"},
    "65": {"SG"}, "66": {"TH"},
    "81": {"JP"}, "82": {"KR"}, "84": {"VN"}, "86": {"CN"},
    "90": {"TR"}, "91": {"IN"}, "92": {"PK"}, "93": {"AF"}, "94": {"LK"},
    "95": {"MM"}, "98": {"IR"},
    "212": {"MA"}, "213": {"DZ"}, "234": {"NG"}, "254": {"KE"}, "255": {"TZ"},
    "351": {"PT"}, "352": {"LU"}, "353": {"IE"}, "354": {"IS"}, "358": {"FI"},
    "359": {"BG"}, "370": {"LT"}, "371": {"LV"}, "372": {"EE"}, "380": {"UA"},
    "385": {"HR"}, "386": {"SI"}, "420": {"CZ"}, "421": {"SK"},
    "852": {"HK"}, "853": {"MO"}, "855": {"KH"}, "856": {"LA"},
    "880": {"BD"}, "886": {"TW"},
    "961": {"LB"}, "962": {"JO"}, "966": {"SA"}, "971": {"AE"}, "972": {"IL"},
    "974": {"QA"}, "977": {"NP"}, "994": {"AZ"}, "998": {"UZ"},
}

# 법인 접미사 (사명 비정상성 판정 시 제거)
_LEGAL_SUFFIX_RE = re.compile(
    r"\b(ltd|limited|inc|incorporated|llc|llp|plc|gmbh|ag|sa|s\.a|bv|b\.v|"
    r"pte|pvt|co|corp|corporation|company|group|holdings?|kg|oy|ab|as|nv)\b\.?",
    re.IGNORECASE,
)

# 가중치 (스펙 3-1 표)
W_FREE_EMAIL = -40        # 🔴 강한 감점
W_DOMAIN_MISMATCH = -25   # 🔴 감점
W_PHONE_MISMATCH = -15    # 🟡 감점
W_ABNORMAL_NAME = -10     # 🟡 감점
W_NO_HOMEPAGE = -5        # 🟡 정보부족


def calling_code_of(mobile: Optional[str]) -> Optional[str]:
    """`+8210****` → "82". + 로 시작하는 번호에서 calling code(최장일치) 추출."""
    if not mobile:
        return None
    m = re.match(r"\s*\+\s*(\d[\d\s-]*)", mobile)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    for length in (3, 2, 1):
        if digits[:length] in CALLING_CODE_TO_ISO2:
            return digits[:length]
    return None


def _is_abnormal_name(name: Optional[str]) -> bool:
    """무의미 철자/난수형 사명 휴리스틱. 보수적으로만 True (약신호)."""
    if not name:
        return False
    core = _LEGAL_SUFFIX_RE.sub("", name)
    core = re.sub(r"[^A-Za-z]", "", core)
    if len(core) < 4:
        return False
    vowels = sum(1 for c in core.lower() if c in "aeiou")
    vowel_ratio = vowels / len(core)
    max_consonant_run = max(
        (len(r) for r in re.findall(r"[^aeiouAEIOU]+", core)), default=0
    )
    # 모음이 거의 없거나, 자음이 5연속 이상이면 사람이 읽기 힘든 난수형으로 본다.
    return vowel_ratio < 0.18 or max_consonant_run >= 5


def run_rules(company: Company) -> RuleResult:
    """Company 에 1차 룰을 적용해 RuleResult 반환."""
    findings: List[RuleFinding] = []

    email_domain = company.email_domain()
    home_domain = domain_of(company.homepage)

    # 1) 무료 이메일 도메인
    if email_domain and email_domain in FREE_EMAIL_DOMAINS:
        findings.append(RuleFinding(
            "free_email_domain", "red", W_FREE_EMAIL,
            f"채용 연락 이메일이 무료 도메인({email_domain}) — 법인 도메인 부재",
        ))

    # 2) 이메일 ↔ 홈페이지 도메인 불일치 (무료도메인은 1)에서 이미 다룸)
    if (
        email_domain and home_domain
        and email_domain not in FREE_EMAIL_DOMAINS
        and not _same_registrable(email_domain, home_domain)
    ):
        findings.append(RuleFinding(
            "email_homepage_domain_mismatch", "red", W_DOMAIN_MISMATCH,
            f"이메일 도메인({email_domain}) ≠ 홈페이지 도메인({home_domain})",
        ))

    # 3) 전화 국가코드 ↔ CountryCode 불일치
    cc = calling_code_of(company.mobile)
    if cc and company.country_code:
        expected = CALLING_CODE_TO_ISO2.get(cc, set())
        if expected and company.country_code.upper() not in expected:
            findings.append(RuleFinding(
                "phone_country_mismatch", "yellow", W_PHONE_MISMATCH,
                f"전화 국가코드 +{cc}({'/'.join(sorted(expected))}) "
                f"≠ CountryCode {company.country_code.upper()}",
            ))

    # 4) 사명 비정상성
    if _is_abnormal_name(company.company_name):
        findings.append(RuleFinding(
            "abnormal_company_name", "yellow", W_ABNORMAL_NAME,
            f"사명이 무의미 철자/난수형으로 의심: {company.company_name!r}",
        ))

    # 5) 홈페이지 누락
    if not company.homepage:
        findings.append(RuleFinding(
            "missing_homepage", "info", W_NO_HOMEPAGE,
            "홈페이지 URL 없음 — 도메인/WHOIS 검증 입력 부족",
        ))

    score = 100 + sum(f.score_delta for f in findings)
    score = max(0, min(100, score))

    reds = [f for f in findings if f.severity == "red"]
    has_free_email = any(f.rule == "free_email_domain" for f in findings)
    # 기준 #2(도메인=회사주소): 개인/무료 이메일(회사 도메인 아님)이면 그 자리에서
    # 즉시 컷 → 🔴 (외부검증 생략). red 2개+ 도 명백 위험으로 즉시 컷.
    obvious_fraud = has_free_email or len(reds) >= 2

    if obvious_fraud or score <= 40:
        verdict, skip_external = "likely_fraud", True
    elif not reds and score >= 80:
        verdict, skip_external = "likely_ok", False
    else:
        verdict, skip_external = "needs_review", False

    return RuleResult(
        score=score, findings=findings,
        verdict=verdict, skip_external=skip_external,
    )


# 공통 다중부 TLD (co.kr, co.uk 등) — registrable 비교용
_MULTI_PART_TLDS = {
    "co.kr", "or.kr", "ne.kr", "go.kr",
    "co.uk", "org.uk", "ac.uk", "gov.uk",
    "com.au", "net.au", "org.au",
    "co.jp", "or.jp", "com.cn", "com.sg", "com.my", "com.hk", "com.br",
}


def _registrable(host: Optional[str]) -> Optional[str]:
    if not host:
        return None
    parts = host.lower().split(".")
    if len(parts) < 2:
        return host.lower()
    last2 = ".".join(parts[-2:])
    last3 = ".".join(parts[-3:]) if len(parts) >= 3 else None
    if last2 in _MULTI_PART_TLDS and last3:
        return last3
    return last2


def _same_registrable(a: Optional[str], b: Optional[str]) -> bool:
    ra, rb = _registrable(a), _registrable(b)
    return bool(ra and rb and ra == rb)
