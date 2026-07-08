# MaxV (막스브이) 자동매매 시스템

한국 주식(KOSPI/KOSDAQ) 대상 변동성 돌파 자동매매 프로젝트입니다.

> **2026-07-06 페이스 게이트 재설계**: 현재는 **페이퍼(관찰) 모드**(`PAPER_MODE=true`, 기본값)로 운용 중이며, 실주문은 발송되지 않습니다. 진입의 거래량 조건은 구 "조건 A(누적 거래량 ≥ 5일평균)"에서 **실시간 거래대금 페이스 게이트**로 전면 교체되었습니다. 명세 원본: `c:\cursor\04_fable5\WORK_ORDER_pace_gate.md`

## 핵심 전략 (2026-07-06 개정)
- 유니버스: **보통주+우선주** 중 전일 시총 상위 10% + 전일 종가 > MA5 (네이버 기반, 전일 확정)
  - **제외(2026-07-08 고정)**: ETF·ETN·펀드·스팩·리츠·인프라펀드 — 시총 랭킹 산정 전에 이름 규칙으로 필터 (`core/naver_universe.py`)
  - **포함**: 우선주 (예: LG전자우)
- 진입 (09:10~15:20 KST, 아래 전부 충족 시):
  - 현재가 ≥ 돌파가 (당일 시가 + 전일 (고가−저가) × K=0.7)
  - **페이스 게이트**: `pace_ratio = (당일 누적 거래대금 ÷ f(t)) ÷ 5일평균 거래대금 ≥ 3.0`
    - f(t) = 시간대별 하루 누적 거래대금 비중 계수표 (`config/pace_constants.py`, 사전 고정 — 임의 수정 금지)
  - 추격 제한: 현재가 ≤ 돌파가 × 1.02
  - 상한가 근접 금지: 현재가 < 전일 종가 × 1.25
  - 게이트 미달 종목은 폐기하지 않고 매 폴링마다 재판정 (추격 제한이 자연 만료 장치)
- 청산: 익일 시가 (페이퍼: 09:01 이후 당일 시가로 원장 소급 기입 / 실전: 08:50 보유 전량 시장가)
  - 비교 기록용: 당일 종가(15:20 직후 현재가 근사) 청산 손익 병행 기록 — 매매에는 미관여
- 자금관리: 종목당 20%, 최대 5종목, 미수/신용 금지 (페이퍼: `PAPER_CAPITAL` 5등분)
- **구 조건 A(거래량 5일 평균 돌파 선행) 및 "첫 관측 A&B 동시충족 스킵" 규칙은 제거됨** — 페이스 게이트가 유일한 거래량 조건

## 대화 규칙 (중요)
- 이 저장소 작업 중 AI 응답은 **반드시 한국어 존댓말**로만 진행합니다. (반말 금지)
- 동일 요청을 반복하지 않도록, 합의된 요구사항/규칙은 문서(README/.cursorrules)에 명시된 내용을 우선합니다.

## Handoff 문서

| 파일 | 내용 |
|------|------|
| [BACKTEST_PPT_HANDOFF.md](./BACKTEST_PPT_HANDOFF.md) | 백테스트·PPT 파이프라인 (repair_v13 엔진) |
| [BACKTEST_LIVE_COMPARE_HANDOFF.md](./BACKTEST_LIVE_COMPARE_HANDOFF.md) | **백테스트 ↔ 실매매** 전략 비교 · `main.py` 수정 체크리스트 (2026-07-04) |

## 디렉토리
```text
maxv/
├── config/
│   ├── settings.py
│   └── korea_market_holidays.txt
├── core/
│   ├── api_client.py
│   ├── logger.py
│   ├── order.py
│   ├── strategy.py
│   ├── trading_day.py
│   ├── naver_universe.py
│   ├── naver_symbol_master.py
│   ├── result_csv.py
│   └── universe_cache.py
├── scripts/
│   ├── build_result.py
│   └── update_symbol_master.py
├── data/
│   └── logs/
├── tests/
│   ├── test_strategy.py
│   ├── test_api_client.py
│   ├── test_naver_universe.py
│   └── test_trading_day.py
├── .cursorrules
├── .env.example
├── main.py
└── requirements.txt
```

- `config/settings.py`: 환경/전략 파라미터
- `config/pace_constants.py`: 페이스 게이트 f(t) 계수표 (사전 고정)
- `core/api_client.py`: KIS 인증, 시세(누적 거래대금 `acml_tr_pbmn` 포함), 주문 API
- `core/naver_universe.py`: 네이버 시총+일봉 스크랩 기반 유니버스 (`value_ma5`, `prev_close` 포함)
- `core/strategy.py`: 가격 돌파 판정 (거래량 조건 없음 — 페이스 게이트가 담당)
- `core/pace_gate.py`: f(t) 보간, 페이스 게이트 판정, block_reason 결정
- `core/pace_collectors.py`: gate/profile CSV 로거, 페이퍼 원장(PaperLedger)
- `core/order.py`: 호가 반올림, 수량 계산, 주문 실행 (`paper_mode` 시 주문 차단)
- `core/logger.py`: 로그/CSV 기록
- `core/result_csv.py`: KIS 일별체결 기반 `result.csv` 집계(FIFO)
- `core/naver_symbol_master.py`: 네이버 시총 페이지에서 종목코드·종목명 마스터
- `core/trading_day.py`: 주말·수동 휴장일 목록으로 기동 여부 판단
- `main.py`: 실행 엔트리(기동 시간 3분기 로직)

## 실행 로직(기동 시간 기준)
- **기동 직후**: 토요일·일요일이거나 `config/korea_market_holidays.txt`에 당일(`YYYYMMDD`)이 있으면 메시지 출력 후 종료합니다. 목록에 없는 평일은 개장일로 간주합니다. 임시공휴일·선거일 등은 매년 파일에 직접 추가하세요. 경로는 `HOLIDAY_DATES_PATH`로 바꿀 수 있습니다.
- **00:00~08:49**: 네이버에서 유니버스를 준비하고 캐시(`data/universe_cache_YYYYMMDD.json`)를 생성/갱신합니다.
- **08:50~15:30**: 감시/매수 로직만 수행합니다. (장중에는 자동 매도 로직 없음)
- **15:30~24:00**: `"장 종료 이후 시간입니다."` 출력 후 종료합니다.

## 08:50 보유 전량 청산 (실전 모드 전용)
- `PAPER_MODE=false`일 때만: 개장 전 기동 케이스에서 **08:50(KST)** 에 현재 계좌의 **보유 종목 전량을 시장가로 매도 주문**합니다.
- 장중(08:50~15:30) 재기동 시에는 매도 로직을 수행하지 않습니다.
- 페이퍼 모드에서는 이 대신 **09:01 이후** 전일 페이퍼 진입분의 익일 시가를 원장에 소급 기입합니다(아래 참조).

## 페이스 게이트 + 페이퍼 모드 (2026-07-06)

### 페이스 게이트
- 돌파가 도달 종목에 대해 KIS 시세의 당일 누적 거래대금(`acml_tr_pbmn`, 원 단위)을 시간대 계수 f(t)로 하루치 환산 → 5일평균 거래대금(`value_ma5`, 네이버 일봉의 `Σ(종가×거래량)/5` 근사) 대비 3.0배 이상일 때만 진입합니다.
- 판정 파라미터(임계 3.0, 시간창 09:10~15:20, 추격 1.02, 상한가 근접 1.25)와 f(t) 계수표는 **사전 고정(pre-registration)** 입니다. 결과를 보고 수정하지 않으며, f(t)는 profile CSV 10거래일 누적 후 1회만 실측 보정합니다.
- ⚠️ 첫 장중 실행 시 `gate_*.csv`의 `cum_value` 자릿수를 "누적거래량 × 대략적 주가"와 대조해 **원 단위임을 실측 확인**할 것 (아직 실응답 미검증).

### 페이퍼 모드 (`PAPER_MODE=true`, 기본값)
- 매수·매도 주문 API를 **이중 차단**합니다 (`core/order.py` + `core/api_client.py`).
- 가상 체결: 매수 = 진입 판정 시점의 현재가(돌파가 대비 슬리피지 별도 기록), 매도 = 익일 시가.
- 예산: `PAPER_CAPITAL`(기본 1,000만 원)의 5등분, 복리 없음.
- **익일 시가 소급 기입은 09:01 이후에 시도**합니다(08:50에는 당일 시가가 존재하지 않음). 실패 시 60초 간격으로 09:30까지 재시도하고, 그래도 못 채운 건(거래정지 등)은 이후 거래 재개일 시가로 채워지며 `exit_open_date` 컬럼으로 식별됩니다. 직전 거래일 진입이 아닌 건이 채워지면 이례 건으로 로그에 경고됩니다.

### 수집 로그 (`logs/`)
| 파일 | 내용 |
|---|---|
| `gate_YYYYMMDD.csv` | 돌파가 도달 전수 기록 (pace_ratio, gate_pass, block_reason; 종목당 최소 60초 간격, **진입 순간은 스로틀 무시·반드시 기록**) |
| `value_profile_YYYYMMDD.csv` | 유니버스 전 종목 5분 간격 누적 거래대금 스냅샷 (**행별 시세 수신 시각** 기록 — f(t) 보정용) |
| `paper_ledger.csv` | 가상 원장 (누적). 헤더 바로 아래 **한국어 설명 행** 포함(`#` 접두). `exit_open_date`로 청산 시가의 소속 세션 검증 가능 |

#### `paper_ledger.csv` 컬럼 (2행째 = 의미)

| date | symbol | entry_ts | entry_price | breakout_price | qty | exit_open_date | exit_open_next | pnl_open_next_bp | exit_close_same | pnl_close_same_bp | pace_ratio_at_entry | fees_bp | net_pnl_open_next_bp |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 진입일(YYYYMMDD) | 종목코드 | 가상 매수 시각(돌파+게이트 통과 판정 순간) | 가상 매수가=판정 순간 현재가 | 돌파가(시가+전일 고저폭×0.7), 매수가와의 차이=슬리피지 | 수량=종목당 예산÷매수가 내림 | 청산 시가의 소속 날짜(진입 익거래일이 아니면 이례 건) | **주 청산가=익일 시가** | 익일 시가 청산 총손익(bp, 비용 전. 100bp=1%) | 비교용 당일 종가(15:20 직후 근사, 매매 미관여) | 당일 종가 청산 가정 총손익(bp) | 진입 순간 페이스 비율(≥3.0) | 왕복 수수료+거래세(bp) | **최종 성적=익일 시가 총손익−비용(bp)** |

- bp = 0.01% (100bp = 1%). 100거래 판정은 `net_pnl_open_next_bp` 합·평균으로 합니다.
- Excel에서 한글이 깨지면: CSV는 **UTF-8 BOM**(`utf-8-sig`)으로 저장됩니다. 봇 기동 시 구 파일은 자동 변환됩니다. 그래도 깨지면 Excel **데이터 → 텍스트/CSV에서 UTF-8**로 가져오기를 사용하세요.

- `block_reason` ∈ {PACE_FAIL, TOO_EARLY, TOO_LATE, CHASE_LIMIT, NEAR_UPPER_LIMIT, FULL_CAP, HIGH_PRICE, ALREADY_ORDERED}. 게이트 통과 후 진입한 건은 빈 값.
- 원장은 CSV가 단일 진실 원천입니다: 장중 재시작해도 미기입 청산 대상은 CSV에서 복원되며, 스키마가 바뀌면 기동 시 헤더 마이그레이션이 자동 수행됩니다.

### 운용 규율 (사전 등록)
- 최소 **100거래** 도달까지 규칙 변경 금지. 100거래 시점 비용 차감 순수익이 음수면 전략 폐기.
- f(t) 보정은 10거래일 수집 후 1회(유니버스 중앙값 프로파일), 이후 동결. 보정 전후 성과 분리 집계.
- 게이트 탈락 신호의 전수 기록이 이 운용의 핵심 산출물입니다. 좋은 신호만 남기는 수정 금지.

## 설치
```bash
pip install -r requirements.txt
```

## 환경변수
`.env.example`을 복사해 `.env`를 만들고 값 입력:

```bash
APP_KEY=...
APP_SECRET=...
ACCOUNT_NO=12345678-01
ACCOUNT_PRDT_CD=01
IS_PAPER_TRADING=true
NAVER_HTTP_DELAY_SEC=0.05
HEARTBEAT_SEC=600
SHUTDOWN_HHMM=15:40
RESULT_CSV_ON_SHUTDOWN=true
RESULT_CSV_KIS_LOOKBACK_DAYS=30
SYMBOL_MASTER_AUTO_REFRESH=true
SYMBOL_MASTER_MAX_AGE_DAYS=7
# 페이스 게이트 + 페이퍼 모드 (기본값 = 작업 지시서 사전 고정값)
PAPER_MODE=true
PAPER_CAPITAL=10000000
PACE_THRESHOLD=3.0
PACE_ENTRY_START_HHMM=09:10
PACE_ENTRY_END_HHMM=15:20
PACE_CHASE_LIMIT_MULT=1.02
PACE_UPPER_LIMIT_MULT=1.25
GATE_LOG_MIN_INTERVAL_SEC=60
VALUE_PROFILE_INTERVAL_SEC=300
PAPER_OPEN_EXIT_FILL_START_HHMM=09:01
PAPER_OPEN_EXIT_FILL_DEADLINE_HHMM=09:30
```

- `PAPER_MODE`가 `IS_PAPER_TRADING`(KIS 모의투자 서버 선택)과 **다른 개념**임에 주의: `PAPER_MODE=true`는 어떤 서버로도 주문을 보내지 않는 관찰 모드입니다.

- `ACCOUNT_NO`는 `8자리-2자리` 형식을 권장합니다.
- 코드에서 자동으로 `CANO=앞 8자리`, `ACNT_PRDT_CD=뒤 2자리`로 분리합니다.
- `HEARTBEAT_SEC`는 상태 로그 출력 주기(초)입니다. 기본 600초(10분)입니다.
- `SHUTDOWN_HHMM` 시각(KST)에 자동 종료합니다. 기본 `15:40`입니다.
- 유니버스는 네이버 시총+일봉 기반으로 **당일 후보 및 전략 준비값(전일 range, 5일 거래량 평균)을 생성**하고, `data/universe_cache_YYYYMMDD.json`로 저장합니다.
- 같은 날 재실행 시에는 캐시를 우선 로드하여 **재스크랩 없이** 감시 후보로 사용합니다.

## 실행
```bash
python main.py
```

## 테스트
프로젝트 루트에서:

```bash
python -m pytest -q
```

(`pytest -q`만 실행하면 `ModuleNotFoundError: core`가 날 수 있습니다.)

## result.csv (매매 정리)
- **수동**: `python -m scripts.build_result` (당일 KST) 또는 `python -m scripts.build_result --date YYYYMMDD`
- **자동**: `SHUTDOWN_HHMM`(기본 15:40, KST)에 루프가 도달하면 **그날짜** 기준으로 KIS 일별체결을 조회해 `data/logs/<paper|live>/result.csv`에 **append**합니다. (`RESULT_CSV_ON_SHUTDOWN=false`로 끌 수 있음)
- 한 줄은 **청산 완료 시** 과거 매수(FIFO) + 당일(지정일) 매도를 합친 형태입니다. 당일 매수만 있고 매도가 없으면 **OPEN** 행(매도 칸 비움)으로 나갈 수 있습니다.
- 종목명: `data/kr_symbol_master.json`을 사용하고, 없거나 오래되면 네이버에서 갱신(`SYMBOL_MASTER_AUTO_REFRESH`, `SYMBOL_MASTER_MAX_AGE_DAYS`). 수동 갱신: `python -m scripts.update_symbol_master`
- 조회 구간: `RESULT_CSV_KIS_LOOKBACK_DAYS`(기본 30, 최대 90). **같은 영업일에 스크립트를 여러 번 실행하면 중복 행**이 생길 수 있습니다.

## 로그 확인
- 모의: `data/logs/paper/` / 실전: `data/logs/live/` 로 **자동 분리**됩니다. (`IS_PAPER_TRADING` 기준)
- `data/logs/<paper|live>/system.log`: 스케줄, 유니버스 필터 건수, `result.csv 갱신` 로그
- `data/logs/<paper|live>/trades.csv`: 체결 기록
- `data/logs/<paper|live>/signals.csv`: 장중 시그널 기록(보유한도 도달로 주문 스킵된 케이스 포함)
- `data/logs/<paper|live>/result.csv`: 일별 청산·OPEN 요약(로컬·`.gitignore`)

## 중요 메모
- KIS 요청 제한을 피하기 위해 예수금 조회는 캐시를 사용합니다.
- 네이버 스크래핑은 약 1~2분 걸릴 수 있습니다.
- 장중 실행 시 네이버 일봉 첫 행이 당일(진행중 봉)일 수 있어, 최신 *완료된* 거래일 봉으로 보정하여 계산합니다.

## 형상관리
- `data/`는 기본적으로 git에 포함하지 않습니다(로그·캐시·`result.csv` 등).
