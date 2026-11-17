"""목업 메시지로 전체 파이프라인을 끝까지 돌려보는 러너.

Slack 토큰 없이도 parse → rules → judge → (시트 행 생성) 까지 배선이 맞는지 확인.
키 유무에 따라 자동으로 동작이 갈린다:
  - ENNOIA_* 있으면 → judge 실호출, 없으면 → 1차 점수로 잠정 등급(드라이런)
  - 외부검증 키(OPENCORPORATES_API_KEY 등) 있으면 → real enrich, 없으면 → 모의 근거

    python scripts/run_mock.py

라벨/필드/출력이 바뀌면: 아래 SAMPLES 만 갈아끼우고 다시 돌리면 된다.
(파서 라벨은 src/parser.py 의 _LABELS, 시트 컬럼은 src/sheets_store.py 의 COLUMNS)
"""

from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_dotenv(path: pathlib.Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.split("#", 1)[0].strip())


load_dotenv(ROOT / ".env")

from src.parser import parse_message      # noqa: E402
from src.rules import run_rules           # noqa: E402
from src.sheets_store import _row_from_record, COLUMNS  # noqa: E402

# --- 목업 메시지: 라벨/형식이 바뀌면 여기만 수정 -------------------------------
SAMPLES = [
    ("예시(gmail·소규모, 전화↔국가 정합)", """\
Company approval request
Sunrise Trading Co(10002)
Size 1~4
Email sunrise.owner@gmail.com
Mobile +61400000000
CountryCode AU
AdminConsole | Jun 3rd | Added by SOURCE BOT"""),

    ("정상형(도메인·국가 정합 + 홈페이지)", """\
Company approval request
acmeglobal(10001)
Size 11~50
Email hr@acme-global.example
Mobile +821012345678
CountryCode KR
CompanyUrl https://www.acme-global.example"""),

    ("명백 사기형(gmail + 전화 MY ↔ 국가 GB + 난수 사명)", """\
Company approval request
Xqzrtvbn Ltd(99999)
Size 1~4
Email xqzrtvbn@gmail.com
Mobile +60123456789
CountryCode GB"""),
]


def mock_evidence(company) -> dict:
    """외부검증 키가 없을 때 쓰는 모의 근거 (전부 '확인 안 됨')."""
    return {
        axis: {"axis": axis, "status": "unknown",
               "summary": "(목업) 외부검증 미실행", "links": []}
        for axis in ("corporate", "domain", "web_hiring", "reputation")
    }


def evidence_for(company) -> dict:
    if os.environ.get("OPENCORPORATES_API_KEY") or os.environ.get("SEARCH_API_KEY"):
        from src import enrich
        return enrich.enrich(company)
    return mock_evidence(company)


def grade_one(text: str) -> dict:
    company = parse_message(text)
    rr = run_rules(company)
    record = {"company": company, "rule_result": rr, "grade": None, "reason": None}

    if rr.skip_external:
        record["grade"] = "🔴"
        record["reason"] = "1차 룰베이스 명백 위험: " + "; ".join(
            f.detail for f in rr.red_flags())
        return record

    evidence = evidence_for(company)
    record["evidence"] = evidence
    if os.environ.get("ENNOIA_API_KEY"):
        from src import judge
        verdict = judge.judge(company, rr, evidence)
        record["grade"] = verdict.get("grade")
        record["reason"] = verdict.get("reason")
    else:
        record["grade"] = {"likely_ok": "🟢", "needs_review": "🟡"}.get(rr.verdict, "🟡")
        record["reason"] = f"(드라이런·1차만) score={rr.score}"
    return record


def main() -> None:
    live = "실호출" if os.environ.get("ENNOIA_API_KEY") else "드라이런"
    enr = "real" if os.environ.get("OPENCORPORATES_API_KEY") else "mock"
    print(f"[모드] judge={live}, enrich={enr}\n")

    for label, text in SAMPLES:
        record = grade_one(text)
        c = record["company"]
        print("=" * 70)
        print(f"▶ {label}")
        print(f"  파싱: {c.company_name!r} id={c.platform_id} "
              f"email={c.email} cc={c.country_code} home={c.homepage}")
        print(f"  결과: {record['grade']}  {record['reason']}")
        print("  시트행:")
        row = _row_from_record(record)
        for col, val in zip(COLUMNS, row):
            print(f"    {col:8} : {val}")
    print("=" * 70)


if __name__ == "__main__":
    main()
