# MaxV (막스브이) 자동매매 시스템

한국 주식(KOSPI/KOSDAQ) 대상 변동성 돌파 자동매매 프로젝트입니다.

## 핵심 전략
- 유니버스: 전일 시총 상위 10% + 전일 종가 > MA5
- 진입: A(거래량 5일 평균 돌파) 이후 B(돌파가) 충족 시 지정가 매수
- 청산: 익일 09:00 전량 시장가 매도
- 자금관리: 종목당 20%, 최대 5종목, 미수/신용 금지

## 디렉토리
```text
maxv/
├── config/
│   └── settings.py
├── core/
│   ├── api_client.py
│   ├── logger.py
│   ├── order.py
│   ├── strategy.py
│   ├── universe.py
│   └── naver_universe.py
├── data/
│   └── logs/
├── tests/
│   ├── test_strategy.py
│   ├── test_universe.py
│   └── test_naver_universe.py
├── .cursorrules
├── .env.example
├── main.py
└── requirements.txt
```

- `config/settings.py`: 환경/전략 파라미터
- `core/api_client.py`: KIS 인증, 시세, 주문 API
- `core/universe.py`: KIS 시총 랭킹 + MA5 유니버스
- `core/naver_universe.py`: 네이버 스크랩 기반 유니버스(개발/검증·KIS 비교용)
- `core/strategy.py`: A->B 상태머신
- `core/order.py`: 호가 반올림, 수량 계산, 주문 실행
- `core/logger.py`: 로그/CSV 기록
- `main.py`: 스케줄러 실행 엔트리

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
UNIVERSE_SOURCE=naver
COMPARE_UNIVERSE_NAVER=true
NAVER_HTTP_DELAY_SEC=0.05
HEARTBEAT_SEC=600
SHUTDOWN_HHMM=15:40
REQUIRE_LOCAL_KST=true
MARKET_HOLIDAYS=
MARKET_EXTRA_OPEN_DAYS=
```

- `ACCOUNT_NO`는 `8자리-2자리` 형식을 권장합니다.
- 코드에서 자동으로 `CANO=앞 8자리`, `ACNT_PRDT_CD=뒤 2자리`로 분리합니다.
- `COMPARE_UNIVERSE_NAVER=false`로 네이버 비교(스크랩)를 끌 수 있습니다.
- `HEARTBEAT_SEC`는 상태 로그 출력 주기(초)입니다. 기본 600초(10분)입니다.
- `SHUTDOWN_HHMM` 시각(KST)에 자동 종료합니다. 기본 `15:40`입니다.
- `REQUIRE_LOCAL_KST=true`면 실행 PC 로컬 시간대가 KST가 아닐 때 실행을 막습니다.
- 거래일 판정은 KIS `chk-holiday`(`CTCA0903R`)의 `opnd_yn` 값을 우선 사용합니다.
- `MARKET_HOLIDAYS`/`MARKET_EXTRA_OPEN_DAYS`는 `YYYYMMDD`를 쉼표로 넣는 수동 오버라이드(예외 보정)입니다.
- `UNIVERSE_SOURCE`는 `naver|kis|naver_then_kis` 중 하나입니다.
  - `naver`: 네이버 시총+일봉 기반으로 **당일 후보 및 전략 준비값(전일 range, 5일 거래량 평균)을 생성**하고, `data/universe_cache_YYYYMMDD.json`로 저장합니다.
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

## 로그 확인
- `data/logs/system.log`: 스케줄, 유니버스 필터 건수, (기본) KIS vs 네이버 비교 요약
- `data/logs/trades.csv`: 체결 기록
- `data/logs/signals.csv`: 장중 시그널 기록(보유한도 도달로 주문 스킵된 케이스 포함)

## 중요 메모
- 유니버스 시총 데이터는 KIS `ranking/market-cap` API(KOSPI+KOSDAQ)를 통합해 사용합니다.
- 모의투자 환경에서 시총 랭킹 응답이 소량(예: 수십 건)만 오는 경우가 있어, 상위 10% 종목 수가 기대보다 매우 작을 수 있습니다. 로그의 `market-cap ranked=` 숫자를 확인하세요.
- API 권한 또는 정책 변경으로 응답 형식이 바뀌면 `core/api_client.py`의 `get_market_cap_rankings()`를 점검하세요.
- KIS 요청 제한을 피하기 위해 예수금 조회는 캐시를 사용합니다.
- 네이버 비교는 스크래핑이며 약 1~2분 걸릴 수 있습니다. 실전 전환 전 모의투자에서 최소 수일 검증 후 전환하세요.

## 현재 진행 상태 (2026-03-27)
- 작업 구분: 집 PC 로컬 `maxv` (`picman`과 별개).
- 유니버스 MA5 인덱스 수정 및 유니버스 단계별 로그 추가.
- 평일 시작 시 즉시 유니버스 준비(장 중 실행 지원). 주말(KST)은 초기 유니버스 생략.
- 네이버 기반 후보 추출 및 KIS 후보와의 비교 로그 추가(`core/naver_universe.py`, 기본 활성).
- 알려진 이슈: KIS 시총 랭킹 건수가 적을 때 후보 종목 수가 비정상적으로 적음(로그로 확인).
- 최대 보유 도달 시에도 감시를 유지하고, 주문 스킵 시그널을 `signals.csv`에 기록.
- 15:40 자동 종료(`SHUTDOWN_HHMM`) 및 10분 하트비트 기본값(`HEARTBEAT_SEC=600`) 반영.
- 거래일 판정은 KIS `chk-holiday`(`CTCA0903R`)의 `opnd_yn` 우선 사용으로 변경.

## 다음에 이어서 할 일
- `system.log`로 KIS `ranked`/`top_n`과 네이버 `ranked`/`top_n`을 비교해 데이터 소스 문제를 구분합니다.
- KIS 시총 API 전체 구간·페이징·모의투자 제한 여부를 조사하고, 필요 시 네이버(또는 다른 소스)를 유니버스 폴백으로 연결할지 결정합니다.
- 월요일 재테스트에서 거래일 판정(KIS), `signals.csv` 누적, 15:40 자동종료 동작을 확인합니다.
- 장중 API 오류(CASH/QUOTE) 발생 빈도 점검 후 재시도/백오프 정책 보강 여부를 결정합니다.

## 형상관리
- 사용자가 로컬 전용으로 둘 수 있으므로, git 커밋/푸시는 요청이 있을 때만 진행하면 됩니다.
