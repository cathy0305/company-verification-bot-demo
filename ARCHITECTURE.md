# 아키텍처

> 공개용 데모 저장소의 설계 문서입니다. 운영 환경의 채널·시트·키 정보와 일부 운영 스크립트는 제외했습니다.

> 스펙이 아니라 **지금 코드가 실제로 하는 것**을 정리한 문서.
> 검증 절차 개선의 출발점.

## 1. 전체 흐름

```
Slack 테스트 채널 (사람/봇이 회사 카드 게시)
        │  ① conversations.history 폴링 (slack_client.fetch_new)
        ▼
   parse_slack_message ──→ Company (회사명/id/email/phone/cc/homepage/size)
        │  ② 1차 룰베이스 (rules.run_rules) — 외부호출 0
        ▼
   ┌─ skip_external? (명백 🔴) ──── 예 ──→ 🔴 확정 (외부검증 생략)
   │                                         │
   └─ 아니오                                  │
        │  ③ 2차 외부검증 (enrich.enrich)     │
        ▼                                     │
   OpenCorporates · WHOIS · 홈페이지 · 웹검색  │
        │  ④ 근거 JSON → 엔노이아 판정         │
        ▼  (judge.judge)                      │
   {grade, reason, confidence, links}         │
        │                                     │
        ├─────────────┬───────────────────────┘
        ▼             ▼
   ⑤ Google Sheets   ⑥ Slack 알림
   (sheets_store)    (시작 + 완료 요약)
        │
        ▼
   ⑦ state.save_ts (마지막 처리 ts)
```

실행 진입점:
- **운영용**: `python -m src.main` → `run()` (cron 1회 실행 가정)
- **데모용**: `python scripts/run_mock.py` (Slack·외부 API 키 없이 예시 데이터로 전체 흐름 확인)

---

## 2. 단계별 상세

### ① 수집 — `slack_client.py`
- `conversations.history` (cursor 페이지네이션), `oldest=last_ts` 이후만.
- **발신자 필터** (`passes_source_filter`): `SLACK_SOURCE_APP_ID`/`SLACK_SOURCE_BOT_ID`.
  - 비우면 전체 통과(테스트). 운영 시 `A0000000000`(source-bot)로 제한.
- 시스템 메시지(입퇴장 등) 제외.

### ② 파싱 — `parser.py`
- `parse_slack_message`: `text` 우선, 비면 `blocks`/`attachments` 수집.
- 회사명 = **첫 라벨 줄 바로 앞 줄** ('Company approval request' 헤더/푸터 무시).
- 라벨 **접두어 매칭**: 공백 유무(`Size1~4`)·마크다운(`*Size*`) 무관.
  - 인식 라벨 → 필드: `size, email, mobile/phone, countrycode, homepage/website/companyurl`.
- ⚠️ **한계**: Block Kit 필드가 라벨/값을 별도 줄로 분리해 보내면 깨질 수 있음.
  실제 봇 raw JSON 확보 후 `parse_blocks` 정밀화 필요 (현재 평문만 검증됨).

### ③ 1차 룰베이스 — `rules.py` (외부호출 0)
시작 점수 100, 룰별 가감 (0~100 클램프):

| 룰 | 심각도 | 점수 | 조건 |
|---|---|---|---|
| `free_email_domain` | 🔴 red | −40 | gmail/naver 등 무료 도메인 |
| `email_homepage_domain_mismatch` | 🔴 red | −25 | 이메일 도메인 ≠ 홈페이지 도메인 |
| `phone_country_mismatch` | 🟡 yellow | −15 | 전화 국가코드 ≠ CountryCode |
| `abnormal_company_name` | 🟡 yellow | −10 | 무모음/난수형 사명(약한 휴리스틱) |
| `missing_homepage` | ⚪ info | −5 | 홈페이지 URL 없음 |

**판정 게이트**:
- `obvious_fraud` = **무료/개인 이메일** 또는 red≥2
- `obvious_fraud` 또는 `score≤40` → **`likely_fraud` + skip_external=True** (외부호출 없이 🔴)
- red 없고 `score≥80` → `likely_ok`
- 그 외 → `needs_review`

> 기준 #2(도메인=회사주소): **개인/무료 이메일(gmail·naver 등)은 그 자리에서 즉시 🔴 컷**
> (외부검증 생략). 회사 도메인 메일만 2차 검증으로 진행.

### ④ 2차 외부검증 — `enrich.py`
검증축 4개, 각각 `{status: ok|not_found|unknown|error, summary, data, links}`.
**키 없으면 `unknown`** (= "확인 안 됨", 추정 금지 원칙):

| 축 | 소스 | 확인 내용 | 현재 상태 |
|---|---|---|---|
| `corporate` | OpenCorporates | 법인 등기·상태(active/dissolved) | ⏸️ **보류**(비용/난이도) → 항상 unknown |
| `domain` | WhoisFreaks | 도메인 등록일(연식)·등록자 | ✅ 키 있음 |
| `web_hiring` | 홈페이지 HTTP | 사이트 생존 + careers/채용 흔적 | ✅ (키 불필요) |
| `reputation` | Serper(웹검색) | **운영 실태 다각도** (아래) | ✅ 키 있음 (쿼리 6회) |

**`reputation` = "실제 운영 중인가" 깊은 검색** (법인등기 대신 강화한 축):
- 검색 6축: 일반 · LinkedIn · 채용공고 · 리뷰 · 부정신호 · 뉴스 (국가코드로 지역 `gl` 좁힘)
- **신뢰신호**(이름 무관 노이즈에 강함): LinkedIn 회사페이지 · 공식사이트 상위노출 ·
  Crunchbase · 리뷰사이트 · 타 플랫폼 채용공고 → `strong_presence_signals`
- **관련성 불확실**(judge가 검토): 뉴스/부정신호는 제목+스니펫까지 넘겨, 동명이인 보도나
  자사 '취업사기 주의' 공지를 부정 신호로 오인하지 않도록 함
- ⚠️ 단순 집계 점수는 가짜를 진짜보다 높게 매기는 오류가 있어 **제거**(judge가 맥락 판단)

### ⑤ 종합 판정 — `judge.py` (엔노이아 preset API)
- 엔노이아 = 자체 preset API (`project`/`apiKey` 헤더 + `hash` + `params` + `messages`).
- `build_params`로 **9개 param** 채워 전송: `company_name, platform_id, country_code,
  email, phone, homepage, size, rule_flags, evidence(JSON)`.
- 출력형식은 **preset 템플릿이 소유** (게이트웨이 모델 `anthropic/claude-opus-4-6`).
- 응답에서 추출: `grade(🟢/🟡/🔴), reason, evidence_links, confidence, red_flags`.
  - grade는 "🟢 normal" 형태도 이모지로 정규화.
- **키 없으면**: judge 생략, 1차 점수로 잠정 등급(드라이런).

### ⑥ 저장 — `sheets_store.py` (Google Sheets)
서비스계정으로 시트에 행 append. 2단 헤더(그룹 띠+컬럼명)로 블록 구분, 등급 색상·
줄바꿈·고정·열너비 서식 자동 적용(`_apply_layout`). 검증일시는 KST. 컬럼:
```
[결과] 등급 · 검증일시
[받은 정보] 회사명 · platform_id · 규모 · 이메일 · 연락처 · 국가 · 홈페이지
[찾은 정보] LinkedIn · 판정사유(불릿)
```
- 값은 RAW 저장(`+8210…` 의 + 보존).

### ⑥ 알림 — Slack
- **등급 리액션**: 원본 카드에 등급 이모지를 **리액션**으로 단다
  (🟢 `large_green_circle` / 🟡 `large_yellow_circle` / 🔴 `red_circle` / 🇰🇷 `kr`).
  → 봇 토큰에 **`reactions:write`** 스코프 필요. `slack_client.react`.
- **시작**: `✅ 정보 확인 완료! N건 검증 시작중..`
- **완료**: `📋 검증 완료 N건 🟢a 🟡b 🔴c` + 시트링크 + 회사별 등급·불릿 사유.

### ⑦ 상태 — `state.py`
- 마지막 처리 `ts`를 로컬 JSON에 저장 (중복 방지).
- 운영(Actions)에선 리포 커밋으로 영속화 예정.

---

## 3. 등급의 의미
| 등급 | 의미 | 나오는 경로 |
|---|---|---|
| 🟢 | 법인/도메인/평판 근거로 실존·채용 진정성 확인 | 2차+judge |
| 🟡 | 정보 부족·신호 충돌 → 사람 검토 필요 | 2차+judge, 또는 1차 드라이런 |
| 🔴 | 페이퍼컴퍼니·사기 패턴 | 1차 명백(외부호출 0) 또는 2차+judge |

---

## 4. 설정 (환경변수)
```
SLACK_BOT_TOKEN, SLACK_SOURCE_CHANNEL, SLACK_NOTIFY_CHANNEL, SLACK_SOURCE_APP_ID
OPENCORPORATES_API_KEY(미설정), WHOIS_API_KEY, SEARCH_API_KEY, SEARCH_PROVIDER=serper
ENNOIA_BASE_URL, ENNOIA_PROJECT, ENNOIA_API_KEY, ENNOIA_HASH
GOOGLE_SHEET_ID, GOOGLE_SHEET_TAB, GDRIVE_SA_JSON(credentials.json)
STATE_PATH
```
- 의존성은 **지연 import** → 키·패키지 없이도 1차 드라이런/테스트 가능.

---

## 5. 현재 약점 (개선 후보)
검증 절차를 더 잘 만들려면 여기를 손봐야 한다:

1. **법인등기 축 보류** — OpenCorporates 비용/난이도로 당장 제외. 대신 `reputation`
   운영검색을 강화함(위). 추후 국가별 무료 등기/검색 보강 여지.
2. **1차 룰이 단순** — 사명 비정상성은 약한 휴리스틱. 가중치·임계값이 실데이터로 튜닝 안 됨.
3. **웹검색 신뢰도** — 6축으로 깊어졌고 신뢰신호/관련성검토로 오탐 완화. 남은 여지:
   쿼리 추가(채용 진위 확인·동종업계 비교), 검색 제공자 다변화.
4. **judge가 preset 출력을 전적으로 신뢰** — 출력 스키마/판정 기준이 코드 밖(preset)에 있어 버전관리·재현이 어려움.
5. **🟡 처리 정책 없음** — 사람 검토 큐/재검증 루프 미구현. 지금은 알림만.
6. **Block Kit 파싱 미검증** — 실제 봇 메시지 raw JSON 확보 전까지 평문 가정.
7. **근거 링크/증적 빈약** — evidence_links만 저장. 판정 근거 스냅샷(원문)을 남기면 감사·재현에 유리.
