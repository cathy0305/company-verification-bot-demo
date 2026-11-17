"""오케스트레이터 (스펙 6절).

흐름: state.load_ts → slack.fetch_new → parser → rules
      → (탈락 아니면) enrich → judge → sheets.append → slack.notify → state.save_ts

parser/rules/state 는 표준 라이브러리만 쓰므로 항상 import 한다. 외부 의존성
(requests/gspread/slack_sdk)을 쓰는 enrich/judge/sheets_store/slack_client 는
실제 사용 시점에 지연 import 해, 키·패키지 없이도 1차 드라이런이 돌게 한다.
"""

from __future__ import annotations

import os

from . import parser, rules, state


def process_message(text: str, ts: str | None = None) -> dict:
    """평문 메시지 1건 → 결과 dict (편의 래퍼)."""
    return process_company(parser.parse_message(text, slack_ts=ts))


def process_company(company) -> dict:
    """파싱된 Company → 1·2차 검증 결과 dict. 2차는 키 있을 때만 시도."""
    rule_result = rules.run_rules(company)

    record = {
        "company": company,
        "rule_result": rule_result,
        "grade": None,
        "reason": None,
        "confidence": "",
    }

    # 기준 #1: CountryCode KR → 국내 기업. 해외 검증 대상이 아니라 국내 가입 안내.
    if (company.country_code or "").strip().upper() == "KR":
        record["grade"] = "🇰🇷"
        record["reason"] = (
            "CountryCode가 KR인 국내 기업입니다. 해외기업 가입이 아니라 "
            "국내 기업 가입 절차로 안내해주세요."
        )
        return record

    # 1차에서 명백 🔴면 외부검증 생략 가능 (스펙 3-1).
    if rule_result.skip_external:
        record["grade"] = "🔴"
        record["confidence"] = "high"  # 룰베이스 명백
        record["reason"] = "1차 룰베이스 명백 위험: " + "; ".join(
            f.detail for f in rule_result.red_flags()
        )
        return record

    # 2차: 엔노이아 판정 키가 있을 때만 (없으면 1차 결과로 잠정 보고)
    if os.environ.get("ENNOIA_API_KEY"):
        from . import enrich, judge  # 지연 import (requests 필요)

        evidence = enrich.enrich(company)
        verdict = judge.judge(company, rule_result, evidence)
        record["grade"] = verdict.get("grade")
        record["reason"] = verdict.get("reason")
        record["confidence"] = verdict.get("confidence") or ""
        record["evidence"] = evidence
    else:
        # 드라이런: 1차 점수만으로 잠정 등급
        record["grade"] = {"likely_ok": "🟢", "needs_review": "🟡"}.get(
            rule_result.verdict, "🟡"
        )
        record["reason"] = f"(드라이런·1차만) score={rule_result.score}"

    return record


ACK_MESSAGE = "✅ 정보 확인 완료! 검증 시작중.."


def build_reply(record: dict, link: str) -> str:
    """원본 카드에 달 스레드 reply 본문 (등급 + 불릿 사유 + 시트 링크)."""
    from .textfmt import bulletize
    grade = record.get("grade") or "❓"
    head = "🇰🇷 *국내 기업 안내*" if grade == "🇰🇷" else f"{grade} *검증 결과*"
    body = bulletize(record.get("reason") or "")
    return f"{head}\n{body}\n\n📋 <{link}|시트에 기록됨>"


def run() -> None:
    """GitHub Actions 1회 실행 진입점.

    SLACK_SOURCE_APP_ID 가 설정돼 있으면 그 앱(source-bot)의 메시지에만 반응.
    각 카드의 검증 결과는 그 카드의 **스레드 reply** 로 남긴다.
    """
    from . import sheets_store, slack_client  # 지연 import (gspread/slack_sdk 필요)

    src = os.environ["SLACK_SOURCE_CHANNEL"]
    link = sheets_store.upload()

    last_ts = state.load_ts()
    if last_ts is None:
        # 첫 실행: 과거 카드 전체를 처리하지 않고 현재 시점만 워터마크로 기록.
        ts = slack_client.latest_ts(src)
        if ts:
            state.save_ts(ts)
        return

    messages = slack_client.fetch_new(src, last_ts)  # 발신자 필터는 fetch_new 내부
    if not messages:
        return

    from .mail import build_mail_reply

    newest_ts = last_ts
    for msg in messages:
        ts = msg.get("ts")
        # ① 접수 ack → ② 검증 결과 → ③ (🟢/🟡면) 메일 발송 안내, 모두 스레드 reply
        slack_client.notify(src, ACK_MESSAGE, thread_ts=ts)
        company = parser.parse_slack_message(msg)
        record = process_company(company)
        sheets_store.append_row(record)
        # 원본 카드에 등급 이모지 리액션(🟢/🟡/🔴/🇰🇷)
        slack_client.react(src, ts, record.get("grade"))
        slack_client.notify(src, build_reply(record, link), thread_ts=ts)
        mail = build_mail_reply(company, record["grade"])
        if mail:
            slack_client.notify(src, mail, thread_ts=ts)
        newest_ts = ts or newest_ts

    if newest_ts:
        state.save_ts(newest_ts)


if __name__ == "__main__":
    run()
