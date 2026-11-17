"""2차 외부 검증: OpenCorporates / WHOIS / 홈페이지 / 웹검색 (스펙 3-2절).

각 검증축은 일관된 형태로 결과를 반환한다::

    {
      "axis":   "corporate" | "domain" | "web_hiring" | "reputation",
      "status": "ok" | "not_found" | "unknown" | "error",
      "summary": "사람이 읽는 한 줄",
      "data":   { ...구조화 근거... },
      "links":  ["근거 URL", ...],
    }

원칙(스펙 3-2): **근거 없는 항목은 추정 금지.** API 키가 없으면 status="unknown"
으로 명시 반환해, judge 단계가 "확인 안 됨"을 사실로 다룰 수 있게 한다.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional, Tuple

from .models import Company
from .parser import domain_of

if TYPE_CHECKING:
    import requests  # 타입 힌트 전용 (런타임 지연 import)

_TIMEOUT = 12
_UA = "company-verifier/0.1 (+recruiting-fraud-check)"


def _result(axis: str, status: str, summary: str,
            data: Optional[dict] = None, links: Optional[List[str]] = None) -> dict:
    return {
        "axis": axis, "status": status, "summary": summary,
        "data": data or {}, "links": links or [],
    }


def _get(url: str, **kw) -> Tuple[Optional["requests.Response"], Optional[str]]:
    import requests  # 지연 import
    try:
        r = requests.get(url, timeout=_TIMEOUT,
                         headers={"User-Agent": _UA}, **kw)
        return r, None
    except requests.RequestException as e:
        return None, str(e)


# --- 법인 실존: OpenCorporates ------------------------------------------------

def verify_corporate(company: Company) -> dict:
    """회사명·국가로 등기 존재 + 상태(active/dissolved) 확인."""
    key = os.environ.get("OPENCORPORATES_API_KEY")
    if not key:
        return _result("corporate", "unknown", "OpenCorporates 키 없음 — 미확인")
    if not company.company_name:
        return _result("corporate", "unknown", "회사명 없음 — 조회 불가")

    params = {"q": company.company_name, "api_token": key, "per_page": 5}
    if company.country_code:
        params["country_code"] = company.country_code.lower()

    r, err = _get("https://api.opencorporates.com/v0.4/companies/search",
                  params=params)
    if err or r is None:
        return _result("corporate", "error", f"요청 실패: {err}")
    if r.status_code != 200:
        return _result("corporate", "error",
                       f"HTTP {r.status_code}", data={"body": r.text[:300]})

    companies = (r.json().get("results", {}) or {}).get("companies", [])
    if not companies:
        return _result("corporate", "not_found",
                       f"'{company.company_name}' 등기 검색 결과 없음")

    top = companies[0]["company"]
    inactive = bool(top.get("inactive"))
    status_txt = top.get("current_status") or ("dissolved" if inactive else "active")
    return _result(
        "corporate", "ok",
        f"등기 확인: {top.get('name')} ({top.get('jurisdiction_code')}) "
        f"상태={status_txt}",
        data={
            "name": top.get("name"),
            "company_number": top.get("company_number"),
            "jurisdiction_code": top.get("jurisdiction_code"),
            "current_status": status_txt,
            "inactive": inactive,
            "incorporation_date": top.get("incorporation_date"),
            "match_count": len(companies),
        },
        links=[top.get("opencorporates_url")] if top.get("opencorporates_url") else [],
    )


# --- 도메인 신뢰: WHOIS (whoisfreaks) -----------------------------------------

def verify_domain(company: Company) -> dict:
    """도메인 등록일(연식)·등록자 확인."""
    domain = domain_of(company.homepage) or (company.email_domain())
    if not domain:
        return _result("domain", "unknown", "도메인 입력 없음(홈페이지/이메일) — 미확인")

    key = os.environ.get("WHOIS_API_KEY")
    if not key:
        return _result("domain", "unknown", "WHOIS 키 없음 — 미확인",
                       data={"domain": domain})

    r, err = _get(
        "https://api.whoisfreaks.com/v1.0/whois",
        params={"apiKey": key, "whois": "live", "domainName": domain},
    )
    if err or r is None:
        return _result("domain", "error", f"요청 실패: {err}")
    if r.status_code != 200:
        return _result("domain", "error", f"HTTP {r.status_code}")

    rec = r.json() or {}
    registrar = (rec.get("domain_registrar") or {}).get("registrar_name")
    created = rec.get("create_date")
    age_days = _days_since(created)
    return _result(
        "domain", "ok" if created else "not_found",
        f"도메인 {domain} 등록일={created or '미상'}"
        + (f" (~{age_days // 365}년)" if age_days is not None else ""),
        data={
            "domain": domain,
            "created_date": created,
            "age_days": age_days,
            "registrar": registrar,
            "expiry_date": rec.get("expiry_date"),
        },
    )


def _days_since(date_str: Optional[str]) -> Optional[int]:
    """ISO 날짜 문자열 → 오늘까지 경과일. 파싱 실패 시 None."""
    if not date_str:
        return None
    # WHOIS 응답의 흔한 포맷들
    s = date_str.strip().replace("Z", "+0000")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return (datetime.utcnow() - dt).days
        except ValueError:
            continue
    return None


# --- 웹 실존 · 채용 진정성: 홈페이지 HTTP + careers ----------------------------

_CAREER_HINTS = ("career", "careers", "jobs", "join-us", "join_us",
                 "we-are-hiring", "recruit", "채용", "인재")


def verify_web_and_hiring(company: Company) -> dict:
    """사이트 생존 + careers 페이지/채용 흔적 확인."""
    if not company.homepage:
        return _result("web_hiring", "unknown", "홈페이지 URL 없음 — 미확인")

    url = company.homepage
    if "://" not in url:
        url = "https://" + url

    r, err = _get(url, allow_redirects=True)
    if err or r is None:
        return _result("web_hiring", "error", f"사이트 접속 실패: {err}",
                       links=[company.homepage])
    if r.status_code >= 400:
        return _result("web_hiring", "not_found",
                       f"사이트 HTTP {r.status_code} — 생존 의심",
                       links=[r.url])

    html = r.text or ""
    low = html.lower()
    found_hints = sorted({h for h in _CAREER_HINTS if h in low})
    # 본문/링크에서 careers 경로 후보 추출
    career_links = []
    for m in re.finditer(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE):
        href = m.group(1)
        if any(h in href.lower() for h in _CAREER_HINTS):
            career_links.append(href)
    career_links = list(dict.fromkeys(career_links))[:5]

    has_careers = bool(found_hints or career_links)
    return _result(
        "web_hiring", "ok",
        f"사이트 생존(HTTP {r.status_code}), 채용 흔적="
        + ("있음" if has_careers else "미발견"),
        data={
            "final_url": r.url,
            "status_code": r.status_code,
            "career_hints": found_hints,
            "career_links": career_links,
            "has_careers": has_careers,
        },
        links=[r.url],
    )


# --- 운영 실태 · 평판: 웹검색 다각도 (실제 운영 중인지 깊게) --------------------

# 회사가 "실제 운영 중"임을 보여주는 외부 흔적을 여러 각도로 캔다.
SOCIAL_DOMAINS = {
    "linkedin.com": "linkedin", "facebook.com": "facebook",
    "instagram.com": "instagram", "twitter.com": "twitter", "x.com": "twitter",
    "youtube.com": "youtube", "crunchbase.com": "crunchbase",
    "github.com": "github", "medium.com": "medium",
}
REVIEW_DOMAINS = ("glassdoor.", "trustpilot.", "indeed.", "kununu.",
                  "jobplanet.co.kr", "teamblind.com")
JOB_DOMAINS = ("linkedin.com/jobs", "indeed.", "glassdoor.", "wellfound.com",
               "ziprecruiter.", "wanted.co.kr", "saramin.co.kr", "jobkorea.co.kr")

_FOLLOWERS_RE = re.compile(r"([\d,]+)\s*(?:followers|팔로워)", re.IGNORECASE)
_EMPLOYEES_RE = re.compile(r"([\d,]+)\s*(?:employees|명의?\s*직원)", re.IGNORECASE)


def _serper(query: str, key: str, kind: str = "search",
            num: int = 8, gl: Optional[str] = None) -> Tuple[dict, Optional[str]]:
    """Serper 호출. kind=search|news. 결과 JSON 전체 반환."""
    import requests  # 지연 import
    payload = {"q": query, "num": num}
    if gl:
        payload["gl"] = gl
    try:
        r = requests.post(
            f"https://google.serper.dev/{kind}",
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json=payload, timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            return {}, f"HTTP {r.status_code}"
        return r.json(), None
    except requests.RequestException as e:
        return {}, str(e)


def _host(url: Optional[str]) -> str:
    return (domain_of(url) or "") if url else ""


def verify_reputation(company: Company) -> dict:
    """실제 운영 중인지 다각도 웹검색 (운영 흔적 + 평판/부정신호).

    검색 축(각 1 크레딧): 일반 · LinkedIn · 채용공고 · 리뷰 · 부정신호 · 뉴스.
    국가코드가 있으면 지역(gl)으로 좁혀 "그 나라에서 실제 활동하는지" 본다.
    """
    name = company.company_name
    if not name:
        return _result("reputation", "unknown", "회사명 없음 — 검색 불가")

    key = os.environ.get("SEARCH_API_KEY")
    provider = os.environ.get("SEARCH_PROVIDER", "serper").lower()
    if not key:
        return _result("reputation", "unknown", "검색 키 없음 — 미확인")
    if provider != "serper":
        return _result("reputation", "unknown",
                       f"미지원 검색 제공자: {provider} (현재 serper만)")

    gl = company.country_code.lower() if company.country_code else None
    home_host = _host(company.homepage)

    # 검색 플랜 (실데이터로 조정 가능)
    plan = [
        ("general",  "search", f'"{name}"'),
        ("linkedin", "search", f'"{name}" site:linkedin.com/company'),
        ("hiring",   "search", f'"{name}" (jobs OR careers OR hiring OR 채용 OR 採用)'),
        ("reviews",  "search", f'"{name}" (glassdoor OR trustpilot OR review OR 평판 OR 리뷰)'),
        ("negative", "search", f'"{name}" (scam OR fraud OR 사기 OR 취업사기 OR complaint OR 피해)'),
        ("news",     "news",   f'"{name}"'),
    ]

    organic_all: List[dict] = []
    news_items: List[dict] = []
    errors: List[str] = []
    buckets: dict = {}

    for label, kind, q in plan:
        data, err = _serper(q, key, kind=kind, gl=gl)
        if err:
            errors.append(f"{label}:{err}")
            continue
        if kind == "news":
            news_items = data.get("news", []) or []
        else:
            hits = data.get("organic", []) or []
            buckets[label] = hits
            organic_all.extend(hits)

    if errors and not organic_all and not news_items:
        return _result("reputation", "error", f"검색 실패: {'; '.join(errors[:2])}")

    # --- 신호 추출 -------------------------------------------------------------
    # 1) LinkedIn 회사 페이지 + 팔로워/직원수
    linkedin = next((h for h in buckets.get("linkedin", [])
                     if "linkedin.com/company" in (h.get("link") or "")), None)
    li_snippet = (linkedin or {}).get("snippet", "") or ""
    followers = _FOLLOWERS_RE.search(li_snippet)
    employees = _EMPLOYEES_RE.search(li_snippet)

    # 2) 소셜/전문 프로필 (운영 흔적 폭)
    social = {}
    for h in organic_all:
        host = _host(h.get("link"))
        for dom, plat in SOCIAL_DOMAINS.items():
            if dom in host and plat not in social:
                social[plat] = h.get("link")

    # 3) 공식 사이트가 검색 상위에 실제로 보이는지
    official_site_match = bool(
        home_host and any(home_host in _host(h.get("link")) for h in organic_all)
    )

    # 4) 타 플랫폼 채용공고 (실제 채용 활동)
    hiring = [h.get("link") for h in buckets.get("hiring", [])
              if any(d in (h.get("link") or "") for d in JOB_DOMAINS)]

    # 5) 리뷰 사이트 존재 (실재 기업 흔적)
    reviews = [h.get("link") for h in (buckets.get("reviews", []) + organic_all)
               if any(d in (h.get("link") or "") for d in REVIEW_DOMAINS)]
    reviews = list(dict.fromkeys(reviews))[:5]

    # 6) 뉴스(보도) — 제목+스니펫까지 줘서 judge 가 회사와의 관련성을 판단
    #    (동명/무관 보도가 섞일 수 있어 raw count 는 신뢰하지 않음)
    news_recent = [{"title": n.get("title"), "date": n.get("date"),
                    "snippet": n.get("snippet"), "link": n.get("link")}
                   for n in news_items[:5]]

    # 7) 부정 신호 — 제목+스니펫. "자사 취업사기 주의 공지" vs "피해 제보" 는
    #    judge 가 맥락으로 구분 (단순 건수로 감점하지 않는다)
    neg = [{"title": h.get("title"), "snippet": h.get("snippet"),
            "link": h.get("link")} for h in buckets.get("negative", [])[:5]]

    # 신뢰 신호 = 실재/운영을 직접 가리키는 것 (이름 무관 노이즈에 강함)
    strong_presence = [s for s, ok in [
        ("linkedin_company", bool(linkedin)),
        ("official_site_ranked", official_site_match),
        ("crunchbase", "crunchbase" in social),
        ("review_site", bool(reviews)),
        ("hiring_postings", bool(hiring)),
    ] if ok]

    summary = (
        f"신뢰신호 {len(strong_presence)}개{strong_presence} | "
        f"LinkedIn={'O' if linkedin else 'X'}"
        + (f"(팔로워 {followers.group(1)})" if followers else "")
        + f", 공식사이트 상위노출={'O' if official_site_match else 'X'}"
        + f", 소셜 {len(social)}개, 뉴스후보 {len(news_items)}건"
        + f", 채용공고 {len(hiring)}건, 부정신호후보 {len(neg)}건 "
        "(뉴스·부정신호 관련성은 판정에서 검토)"
    )

    links = []
    if linkedin:
        links.append(linkedin.get("link"))
    links += list(social.values()) + hiring[:2] + reviews[:2]
    links += [n["link"] for n in news_recent if n.get("link")]

    return _result(
        "reputation", "ok", summary,
        data={
            "size_claimed": company.size,
            # 신뢰 신호 (이름 무관 노이즈에 강함)
            "strong_presence_signals": strong_presence,
            "linkedin_url": linkedin.get("link") if linkedin else None,
            "linkedin_followers": followers.group(1) if followers else None,
            "linkedin_employees": employees.group(1) if employees else None,
            "official_site_in_results": official_site_match,
            "social_profiles": social,
            "hiring_postings": hiring[:5],
            "review_sites": reviews,
            # 관련성 불확실 — judge 가 회사명·국가·도메인 정합으로 검토
            "news_candidates": news_recent,
            "negative_candidates": neg,
            "search_errors": errors,
        },
        links=[l for l in dict.fromkeys(links) if l][:10],
    )


def enrich(company: Company) -> dict:
    """검증축을 모아 judge 입력용 근거 JSON 으로 반환.

    법인등기(corporate)는 현재 비활성(비용/난이도). OPENCORPORATES_API_KEY 가
    있을 때만 포함해, 판정 사유에 '법인등기 미확인' 잡음이 끼지 않게 한다.
    (키를 넣으면 자동 복귀)
    """
    evidence = {
        "domain": verify_domain(company),
        "web_hiring": verify_web_and_hiring(company),
        "reputation": verify_reputation(company),
    }
    if os.environ.get("OPENCORPORATES_API_KEY"):
        evidence["corporate"] = verify_corporate(company)
    return evidence
