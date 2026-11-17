"""소스 슬랙봇 메시지 → Company 구조체 (스펙 2절).

소스 봇이 어떤 형식으로 보내는지 아직 raw payload로 확정되지 않았으므로
(스펙 9-4), 일단 **plain text 라인 파싱**을 1차 구현으로 둔다.
Slack mrkdwn 링크(`<url|label>`, `<mailto:..|..>`) 도 함께 해제한다.

Block Kit 형태로 확정되면 `parse_blocks()` 쪽을 채워서 우선 사용할 것.
"""

from __future__ import annotations

import re
from typing import Dict, Optional
from urllib.parse import urlparse

from .models import Company

# 소스 봇(source-bot) 라벨. 실제 메시지는 라벨과 값이 같은 줄에 오고,
# 복붙 시 공백이 사라지기도 한다(예: "Size1~4"). 접두어 매칭으로 양쪽 다 처리.
# 'company url' 처럼 공백 포함 라벨이 먼저 매칭되도록 길이순(긴 것 우선)으로 시도.
_LABELS = {
    "company url": "homepage",
    "company_url": "homepage",
    "companyurl": "homepage",
    "countrycode": "country_code",
    "homepage": "homepage",
    "website": "homepage",
    "industry": "industry",
    "mobile": "mobile",
    "member": "member",
    "phone": "mobile",
    "email": "email",
    "size": "size",
}
_SORTED_LABELS = sorted(_LABELS.items(), key=lambda kv: -len(kv[0]))

# 라벨/값 양끝에서 떼어낼 장식 문자 (마크다운 *_·, 콜론, 공백, 탭, 불릿)
_LABEL_TRIM = " \t:*_·•|"


def _match_label(line: str):
    """줄이 알려진 라벨로 시작하면 (field, value) 반환. 아니면 (None, None).

    'CountryCodeAU' / '*Size* 1~4' / 'Email   x@y.com' 모두 처리.
    """
    s = line.strip().lstrip(_LABEL_TRIM)
    low = s.lower()
    for label, field in _SORTED_LABELS:
        if low.startswith(label):
            value = s[len(label):].strip().strip(_LABEL_TRIM)
            return field, value
    return None, None


# Slack 링크: <https://x|label> 또는 <https://x>
_SLACK_LINK_RE = re.compile(r"<(?P<url>[^|>]+)(?:\|(?P<label>[^>]*))?>")
# 헤더의 (10001) 또는 링크 속 /companies/10001
_PAREN_ID_RE = re.compile(r"\((\d{2,})\)")
_HQ_ID_RE = re.compile(r"/companies/(\d+)")
_URL_RE = re.compile(r"https?://\S+")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


def _unwrap_links(text: str) -> str:
    """Slack `<url|label>` 를 사람이 읽는 형태로 평탄화. label 우선, 없으면 url."""

    def repl(m: re.Match) -> str:
        url = m.group("url") or ""
        label = m.group("label")
        if url.startswith("mailto:"):
            # mailto:x@y.com|x@y.com → 이메일 주소만
            return url[len("mailto:"):]
        return label if label else url

    return _SLACK_LINK_RE.sub(repl, text)


def _first_url(text: str) -> Optional[str]:
    # 원본(언랩 전)에서 Slack 링크의 실제 url 우선 추출
    m = _SLACK_LINK_RE.search(text)
    if m and m.group("url") and not m.group("url").startswith("mailto:"):
        return m.group("url").strip()
    m2 = _URL_RE.search(text)
    return m2.group(0).strip() if m2 else None


def _parse_header(line: str, raw_line: str) -> Dict[str, object]:
    """헤더 라인에서 회사명 / platform_id 추출.

    회사명은 평문이면 충분하다. 링크가 섞여 와도 platform_id 만 보조로 뽑고
    URL 자체는 저장하지 않는다.
    """
    out: Dict[str, object] = {}

    url = _first_url(raw_line)
    if url:
        m = _HQ_ID_RE.search(url)
        if m:
            out["platform_id"] = int(m.group(1))

    # 회사명: 언랩된 텍스트에서 (id) 와 url 제거
    name = _URL_RE.sub("", line).strip()
    m = _PAREN_ID_RE.search(name)
    if m:
        if "platform_id" not in out:
            out["platform_id"] = int(m.group(1))
        name = name[: m.start()].strip()
    name = name.strip(" -|·\t")
    if name:
        out["company_name"] = name
    return out


def parse_message(text: str, slack_ts: Optional[str] = None) -> Company:
    """plain text(또는 mrkdwn) 메시지 1건을 Company 로 파싱한다.

    소스 봇 메시지 형식 예시 구조::

        Company approval request          ← 헤더(무시)
        Sunrise Trading Co(10002)          ← 회사명(첫 라벨 줄 바로 앞)
        Size 1~4
        Email sunrise.owner@gmail.com
        Mobile +61400000000
        CountryCode AU
        CompanyUrl https://...            ← 추가 예정
        AdminConsole | Jun 3rd | Added by ... ← 푸터(무시)

    회사명은 "첫 라벨 줄 바로 앞 줄" 로 잡아 헤더/푸터에 흔들리지 않게 한다.
    같은 카드가 두 번 와도(미리보기+본문) 라벨은 덮어쓰기되어 결과가 같다.
    """
    company = Company(slack_ts=slack_ts, raw_text=text)

    raw_lines = [ln for ln in text.splitlines() if ln.strip()]
    if not raw_lines:
        return company

    fields: Dict[str, str] = {}
    first_label_idx: Optional[int] = None

    i, n = 0, len(raw_lines)
    while i < n:
        raw_line = raw_lines[i]
        field, value = _match_label(_unwrap_links(raw_line))
        if not field:
            i += 1
            continue
        if first_label_idx is None:
            first_label_idx = i

        # 값이 같은 줄에 없으면(라벨만 있는 새 형식) 다음 줄을 값으로.
        # 단, 다음 줄이 또 라벨이면 값은 비운다.
        value_raw = raw_line
        if not value and i + 1 < n:
            nxt = raw_lines[i + 1]
            nxt_field, _ = _match_label(_unwrap_links(nxt))
            if not nxt_field:
                value = _unwrap_links(nxt).strip()
                value_raw = nxt
                i += 1  # 값 줄 소비

        if field == "homepage":
            value = _first_url(value_raw) or value
        elif field == "email":
            em = _EMAIL_RE.search(value)
            value = em.group(0) if em else value
        elif field == "country_code":
            parts = value.split()
            value = parts[0].upper() if parts else ""
        fields[field] = value  # 중복 카드면 마지막 값으로 덮어씀
        i += 1

    # 회사명 라인: 첫 라벨 줄 바로 앞 → 없으면 (id) 포함 줄 → 없으면 첫 줄
    if first_label_idx is not None and first_label_idx > 0:
        header_line = raw_lines[first_label_idx - 1]
    else:
        header_line = next(
            (ln for ln in raw_lines if _PAREN_ID_RE.search(ln)), raw_lines[0]
        )

    for k, v in _parse_header(_unwrap_links(header_line), header_line).items():
        setattr(company, k, v)
    for field, value in fields.items():
        setattr(company, field, value)

    return company


def parse_slack_message(msg: dict) -> Company:
    """Slack 메시지 dict → Company.

    실제 source-bot 카드는 text/blocks 가 비고 **attachments[].fields** 에 담겨온다
    (title="회사명(id)", title_link=HQ링크, fields=[{title:라벨, value:값}]).
    사람이 평문 붙여넣은 테스트는 text 로 처리.
    """
    ts = msg.get("ts")
    text = (msg.get("text") or "").strip()
    if text:
        return parse_message(text, slack_ts=ts)
    for att in (msg.get("attachments") or []):
        if att.get("fields") or att.get("title"):
            return parse_attachment(att, slack_ts=ts)
    return parse_blocks([msg.get("blocks"), msg.get("attachments")], slack_ts=ts)


def parse_attachment(att: dict, slack_ts: Optional[str] = None) -> Company:
    """source-bot attachment 카드 파싱.

    title="Sample Beauty Shop(10003)", title_link=".../companies/10003",
    fields=[{"title":"Size","value":"1~4"}, {"title":"Email","value":"<mailto:..>"}, ...]
    """
    company = Company(slack_ts=slack_ts, raw_text=att.get("fallback") or att.get("title"))

    title = att.get("title") or ""
    m = _HQ_ID_RE.search(att.get("title_link") or "")
    if m:
        company.platform_id = int(m.group(1))
    for k, v in _parse_header(_unwrap_links(title), title).items():
        setattr(company, k, v)

    for f in att.get("fields") or []:
        field, _ = _match_label(f.get("title") or "")
        if not field:
            continue
        raw_val = f.get("value") or ""
        val = _unwrap_links(raw_val).strip()
        if field == "homepage":
            val = _first_url(raw_val) or val
        elif field == "email":
            em = _EMAIL_RE.search(val)
            val = em.group(0) if em else val
        elif field == "country_code":
            parts = val.split()
            val = parts[0].upper() if parts else ""
        setattr(company, field, val)

    return company


def parse_blocks(blocks: list, slack_ts: Optional[str] = None) -> Company:
    """Block Kit 메시지 파싱 (소스 봇 형식 확정 후 구현 — 스펙 9-4).

    현재는 블록 내 text 를 모아 parse_message 로 위임한다. raw payload 1건을
    확보하면 section/fields 구조를 직접 매핑하도록 교체할 것.
    """
    texts: list = []

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("text"), str):
                texts.append(node["text"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(blocks)
    return parse_message("\n".join(texts), slack_ts=slack_ts)


def domain_of(url_or_email: Optional[str]) -> Optional[str]:
    """url 또는 email 에서 호스트(도메인)만 소문자로 추출. www. 제거."""
    if not url_or_email:
        return None
    s = url_or_email.strip()
    if "@" in s and "://" not in s:
        host = s.split("@", 1)[1]
    else:
        if "://" not in s:
            s = "http://" + s
        host = urlparse(s).netloc
    host = host.split(":")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None
