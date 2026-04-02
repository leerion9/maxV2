# MaxV (막스브이) 자동매매 시스템

한국 주식(KOSPI/KOSDAQ) 대상 변동성 돌파 자동매매 프로젝트입니다.

## 핵심 전략
- 유니버스: 전일 시총 상위 10% + 전일 종가 > MA5
- 진입: A(거래량 5일 평균 돌파) 이후 B(돌파가) 충족 시 지정가 매수
- 청산: 당일 08:50 기준 보유 종목 전량 시장가 매도
- 자금관리: 종목당 20%, 최대 5종목, 미수/신용 금지

## 대화 규칙 (중요)
- 이 저장소 작업 중 AI 응답은 **반드시 한국어 존댓말**로만 진행합니다. (반말 금지)
- 동일 요청을 반복하지 않도록, 합의된 요구사항/규칙은 문서(README/.cursorrules)에 명시된 내용을 우선합니다.

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
│   ├── naver_universe.py
│   └── universe_cache.py
├── data/
│   └── logs/
├── tests/
│   ├── test_strategy.py
│   ├── test_api_client.py
│   └── test_naver_universe.py
├── .cursorrules
├── .env.example
├── main.py
└── requirements.txt
```

- `config/settings.py`: 환경/전략 파라미터
- `core/api_client.py`: KIS 인증, 시세, 주문 API
- `core/naver_universe.py`: 네이버 시총+일봉 스크랩 기반 유니버스
- `core/strategy.py`: A->B 상태머신
- `core/order.py`: 호가 반올림, 수량 계산, 주문 실행
- `core/logger.py`: 로그/CSV 기록
- `main.py`: 실행 엔트리(기동 시간 3분기 로직)

## 실행 로직(기동 시간 기준)
- **00:00~08:49**: 네이버에서 유니버스를 준비하고 캐시(`data/universe_cache_YYYYMMDD.json`)를 생성/갱신합니다.
- **08:50~15:30**: 감시/매수 로직만 수행합니다. (장중에는 자동 매도 로직 없음)
- **15:30~24:00**: `"장 종료 이후 시간입니다."` 출력 후 종료합니다.

## 08:50 보유 전량 청산
- 개장 전 기동 케이스에서 **08:50(KST)** 에 현재 계좌의 **보유 종목 전량을 시장가로 매도 주문**합니다.
- 장중(08:50~15:30) 재기동 시에는 매도 로직을 수행하지 않습니다.

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
```

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

## 로그 확인
- `data/logs/system.log`: 스케줄, 유니버스 필터 건수
- `data/logs/trades.csv`: 체결 기록
- `data/logs/signals.csv`: 장중 시그널 기록(보유한도 도달로 주문 스킵된 케이스 포함)

## 중요 메모
- KIS 요청 제한을 피하기 위해 예수금 조회는 캐시를 사용합니다.
- 네이버 스크래핑은 약 1~2분 걸릴 수 있습니다.
- 장중 실행 시 네이버 일봉 첫 행이 당일(진행중 봉)일 수 있어, 최신 *완료된* 거래일 봉으로 보정하여 계산합니다.

## 형상관리
- 사용자가 로컬 전용으로 둘 수 있으므로, git 커밋/푸시는 요청이 있을 때만 진행하면 됩니다.
