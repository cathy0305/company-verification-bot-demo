"""검증 결과를 Google Sheets(드라이브)에 append + 보기 좋은 서식.

화면 구성(2단 헤더로 블록 구분):
  [결과] 등급 · 검증일시(KST)
  [받은 정보] 회사명 · platform_id · 규모 · 이메일 · 연락처 · 국가 · 홈페이지
  [찾은 정보] LinkedIn · 판정사유

필요 환경변수: GOOGLE_SHEET_ID / GDRIVE_SA_JSON / GOOGLE_SHEET_TAB
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import List

from .models import Company, RuleResult
from .textfmt import bulletize

KST = timezone(timedelta(hours=9))

# 컬럼(2번째 헤더 행). 그룹은 GROUPS 가 1번째 행에 띠로 표시.
COLUMNS = [
    "등급", "검증일시", "회사명", "platform_id", "규모", "이메일",
    "연락처", "국가", "홈페이지", "업종", "담당자", "LinkedIn", "판정사유",
]
# (그룹명, 시작열, 끝열)  — 끝열 포함
GROUPS = [("결과", 0, 1), ("받은 정보", 2, 10), ("찾은 정보", 11, 12)]

_COL_WIDTH = {0: 60, 1: 140, 2: 160, 3: 80, 4: 80, 5: 235, 6: 135,
              7: 55, 8: 205, 9: 140, 10: 110, 11: 205, 12: 470}
_CENTER_COLS = (0, 1, 3, 4, 7)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_ws_cache = None


def _load_sa_info() -> dict:
    raw = os.environ.get("GDRIVE_SA_JSON")
    if not raw:
        raise RuntimeError("GDRIVE_SA_JSON 미설정 (서비스계정 JSON 문자열 또는 경로)")
    if raw.strip().startswith("{"):
        return json.loads(raw)
    with open(raw, "r", encoding="utf-8") as f:
        return json.load(f)


def _header_rows() -> List[List[str]]:
    group_row = [""] * len(COLUMNS)
    for label, start, _end in GROUPS:
        group_row[start] = label
    return [group_row, COLUMNS]


def _worksheet():
    global _ws_cache
    if _ws_cache is not None:
        return _ws_cache

    import gspread
    from google.oauth2.service_account import Credentials

    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID 미설정")
    tab = os.environ.get("GOOGLE_SHEET_TAB", "검증결과")

    creds = Credentials.from_service_account_info(_load_sa_info(), scopes=_SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)
    created = False
    try:
        ws = sh.worksheet(tab)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab, rows=2000, cols=len(COLUMNS))
        created = True

    if created or not ws.row_values(1):
        ws.batch_update([{"range": "A1", "values": _header_rows()}])
        _apply_layout(sh, ws.id)

    _ws_cache = ws
    return ws


# --- 서식 --------------------------------------------------------------------

def _rgb(r: float, g: float, b: float) -> dict:
    return {"red": r, "green": g, "blue": b}


_GROUP_BG = {
    "결과": _rgb(0.17, 0.27, 0.40),       # 진한 남색
    "받은 정보": _rgb(0.81, 0.89, 0.97),   # 연한 파랑
    "찾은 정보": _rgb(0.82, 0.93, 0.83),   # 연한 초록
}
_GROUP_FG = {"결과": _rgb(1, 1, 1)}        # 남색 띠만 흰 글씨


def _apply_layout(sh, sheet_id: int) -> None:
    ncols = len(COLUMNS)
    reqs: list = []

    # 기존 조건부서식 제거(중복 방지)
    existing = 0
    for s in sh.fetch_sheet_metadata().get("sheets", []):
        if s["properties"]["sheetId"] == sheet_id:
            existing = len(s.get("conditionalFormats", []))
    reqs += [{"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": i}}
             for i in range(existing - 1, -1, -1)]

    # 헤더 2줄 고정
    reqs.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 2}},
        "fields": "gridProperties.frozenRowCount"}})

    # 그룹 띠(1행) 병합 초기화 후 재병합
    reqs.append({"unmergeCells": {"range": {
        "sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
        "startColumnIndex": 0, "endColumnIndex": ncols}}})
    for label, start, end in GROUPS:
        if end > start:
            reqs.append({"mergeCells": {"mergeType": "MERGE_ALL", "range": {
                "sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                "startColumnIndex": start, "endColumnIndex": end + 1}}})
        reqs.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": start, "endColumnIndex": end + 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": _GROUP_BG[label],
                "textFormat": {"bold": True,
                               "foregroundColor": _GROUP_FG.get(label, _rgb(0.1, 0.1, 0.1))},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)"}})

    # 컬럼명(2행): 남색 바 + 흰 굵은 글씨
    reqs.append({"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2},
        "cell": {"userEnteredFormat": {
            "backgroundColor": _rgb(0.30, 0.40, 0.52),
            "textFormat": {"bold": True, "foregroundColor": _rgb(1, 1, 1)},
            "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE"}},
        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)"}})

    # 데이터(3행~): 줄바꿈 + 상단정렬
    reqs.append({"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": 2,
                  "startColumnIndex": 0, "endColumnIndex": ncols},
        "cell": {"userEnteredFormat": {"verticalAlignment": "TOP", "wrapStrategy": "WRAP"}},
        "fields": "userEnteredFormat(verticalAlignment,wrapStrategy)"}})

    # 특정 열 가운데 정렬
    for col in _CENTER_COLS:
        reqs.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 2,
                      "startColumnIndex": col, "endColumnIndex": col + 1},
            "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat.horizontalAlignment"}})

    # 열 너비
    for col, w in _COL_WIDTH.items():
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": col, "endIndex": col + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"}})

    # 등급 열(0) 색상 — 🔴/🟡/🟢
    for emoji, color in (("🔴", _rgb(0.96, 0.80, 0.80)),
                         ("🟡", _rgb(1.0, 0.92, 0.69)),
                         ("🟢", _rgb(0.80, 0.92, 0.77))):
        reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [{"sheetId": sheet_id, "startRowIndex": 2,
                        "startColumnIndex": 0, "endColumnIndex": 1}],
            "booleanRule": {
                "condition": {"type": "TEXT_CONTAINS",
                              "values": [{"userEnteredValue": emoji}]},
                "format": {"backgroundColor": color, "textFormat": {"bold": True}}}}}})

    sh.batch_update({"requests": reqs})


def setup_sheet(reset: bool = False):
    """헤더(2줄)+서식 (재)적용. reset=True 면 데이터 비우고 새로."""
    ws = _worksheet()
    if reset:
        ws.clear()
        ws.batch_update([{"range": "A1", "values": _header_rows()}])
    elif not ws.row_values(1):
        ws.batch_update([{"range": "A1", "values": _header_rows()}])
    _apply_layout(ws.spreadsheet, ws.id)
    return ws


# --- 행 구성 ------------------------------------------------------------------

def _row_from_record(record: dict) -> List[str]:
    company: Company = record["company"]
    rep = ((record.get("evidence") or {}).get("reputation") or {}).get("data") or {}
    return [
        record.get("grade") or "",
        datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        company.company_name or "",
        str(company.platform_id or ""),
        company.size or "",
        company.email or "",            # 엑셀엔 전체 이메일 저장
        company.mobile or "",           # 엑셀엔 전체 전화번호 저장
        company.country_code or "",
        company.homepage or "",
        company.industry or "",
        company.member or "",
        rep.get("linkedin_url") or "",
        bulletize(record.get("reason") or ""),
    ]


def append_row(record: dict) -> None:
    ws = _worksheet()
    # RAW: '+8210...' 의 + 가 수식/숫자로 해석돼 사라지는 것 방지(문자열 그대로 저장)
    ws.append_row(_row_from_record(record), value_input_option="RAW")


def upload() -> str:
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}"
