# 백테스트 + PPT 리포트 파이프라인 handoff

> **마지막 갱신**: 2026-07-04 (장중 프록시 **보류** · 백테스트↔실매매 비교 handoff 추가)
> **다음 작업**: 백테스트 baseline 기준 **실매매 py 정합** — [BACKTEST_LIVE_COMPARE_HANDOFF.md](./BACKTEST_LIVE_COMPARE_HANDOFF.md) §5 체크리스트
> ~~(1) 거래대금 장중 누적 proxy~~ → **2026-07-04 진행 안 함** (일봉만으로 실전 재현 불가, C 포함)
> **프로젝트**: `c:\cursor\02_maxV2` (00_archive 와 별개. data/raw는 pengo CSV)

---

## 0. 이 handoff의 범위

이 문서는 **뱅크롤 1억 복리 + PPT 결과보고서** 작업 전용이다.
(기존 `BACKTEST_HANDOFF.md` = 00_archive archive 데이터 기반 검증 작업. 혼동 금지.)

이번 작업은 **`02_maxV2/data/raw` CSV** 만 사용한다 (archive 데이터 아님).

---

## 1. 스크립트 (모두 `c:\cursor\02_maxV2`)

| 파일 | 역할 | 단독 실행 결과 |
|------|------|------|
| **`build_backtest_ppt_sets.py`** | **세트 비교 드라이버** — PRESETS별 배치 실행 → 비교 요약 + 세트별 상세 PPT | 바탕화면 .pptx |
| **`build_backtest_ppt.py`** | **단일/공용 PPT** — 슬라이드 헬퍼(`add_detail_slides`, `make_charts`, `_build_cond_rows`, `_cost_pct`, `_vol_label` …) | 바탕화면 .pptx |
| **`repair_v13_baseline.py`** | **엔진(표준)** — point-in-time 시총, 전 파라미터화. `load_frames()` + `run_baseline(frames=…)` | 콘솔 |
| **`repair_v13_bankroll.py`** | **엔진(공용)** — `_simulate_bankroll(sell_mode)`, `_signals_to_fixed_trades`, `assemble_results()` | 콘솔 + CSV |

의존관계: `build_backtest_ppt_sets.py` → `load_frames()`(1회) → `run_baseline()`×N → `assemble_results()` → `add_detail_slides()` + 비교 슬라이드.

### 실행법

```powershell
cd c:\cursor\02_maxV2
$env:PYTHONIOENCODING='utf-8'

# 프리셋별 배치 (아래 PRESETS 표 참고)
python build_backtest_ppt_sets.py --preset vol_sweep    # 거래대금 100~600% 임계값
python build_backtest_ppt_sets.py --preset vol_bands  # 거래대금 50~100% … 600%+ 구간
python build_backtest_ppt_sets.py --preset close_buy  # 돌파+종가매수 vs 돌파없이 종가
python build_backtest_ppt_sets.py --preset entry_mcap # MA5/시총하위/종가매수

# 단일 조건
python build_backtest_ppt.py --mode baseline
python build_backtest_ppt.py --mode snapshot
```

### PRESETS (`build_backtest_ppt_sets.py`)

| `--preset` | 세트 구성 | 출력 파일 |
|------------|-----------|-----------|
| `volume` | 기준 · A1(전일300%) · A2(필터제거) · C(600%) | `repair_v13_volume_sets_compare.pptx` |
| `base_adj` | 기준 · 600% · 600%+0.5% | `repair_v13_base_adj_sets_compare.pptx` |
| `entry_mcap` | 기준 · 돌파+종가 · MA5제외 · 시총하위10% | `repair_v13_entry_mcap_sets_compare.pptx` |
| `close_buy` | 기준 · 돌파+종가매수 · 돌파X+종가매수 | `repair_v13_close_buy_sets_compare.pptx` |
| `vol_sweep` | 100% · 200% · 300%(기준) · 400~600% | `repair_v13_vol_sweep_sets_compare.pptx` |
| `vol_bands` | 기준(≥300%) · 50~100% … 500~600% · 600%+ | `repair_v13_vol_bands_sets_compare.pptx` |
| `prev_high_buy` | 기준 · 전일고가 · 전일고가+거래대금X · 시총필터X | `repair_v13_prev_high_buy_sets_compare.pptx` |

세트 추가: `PRESETS` dict에 항목 추가. 각 세트 `over` = `run_baseline()` 인자 중 **기준 대비 바뀌는 것만**.

### 검증 프로세스 (2026-07-10 개정)

**기존**: 전체 백테스트 완료 후 일부 결과만 사후 스팟체크.  
**변경**: 코드 수정 직후 **샘플 검증 → 전체 백테스트 → 사후 스팟체크** 3단계.

1. **샘플 검증 (수정 직후, 필수)** — `python sample_backtest_check.py` (랜덤 10종)  
   기준·변경 조건별 신호 건수·체결가 출력. 기준 세트 신호 0건이면 전체 백테스트 중단.
2. **전체 백테스트** — `build_backtest_ppt_sets.py --preset …`
3. **사후 스팟체크 (기존 유지)** — 전체 실행 후 대표 종목·일자 수동 대조

```python
{"key": "x", "name": "...", "short": "...", "sub": "...",
 "over": {"vol_mult": 6.0, "cost_mult": 0.995}}
```

첨부 원본 `repair_v13.py`는 건드리지 않음.

---

## 2. 데이터 (`02_maxV2/data/raw/*.csv`)

- pengo 수집 CSV **2,770 파일** → 로드 **2,768종**
- 컬럼: `날짜,시가,고가,저가,종가,거래량,등락률,회전율?,MarketCap`
- **MarketCap** 일자별 (결측 0%) → point-in-time 시총 가능
- ⚠️ **종료일 2026-03-06** (요청 2026-05-31이어도 캡)
- ⚠️ **생존편향** (상폐 종목 누락 가능)

---

## 3. 표준(baseline) 조건 — 모든 비교의 기준

| 항목 | 값 |
|------|-----|
| **매수 기준가** | **당일 시가 + (전일 고가 − 전일 저가) × K** ← 전일 종가 아님 |
| K | **0.7** |
| 매수 체결 | `High >= target` → target 가격 체결 가정 |
| 거래대금 | 당일 거래대금(종가×거래량) ≥ 5일평균 × **300%** ⚠️ 룩어헤드 |
| 시장 필터 | 전일 종가 > 전일 MA5 |
| 시총 | 매매 전날(D-1) 일별 cross-section **상위 10%** |
| 매도 | **익일 시가** |
| 비용 | **1%** (`cost_mult=0.99`, 매도금액×0.99) |
| 기간 | 2020-01-01 ~ 2026-05-31 (실제 2026-03-06) |

### 뱅크롤 복리 (고정)

- 시작 **1억**, 전량 복리
- 매일 아침 전일 매수분 **시가 전량 매도** → 뱅크롤 재정산
- 슬롯 = 뱅크롤 ÷ **10**, 일 최대 **10종**
- 10종 초과 → **랜덤 10종** (`seed=42`)
- MDD·equity = **아침 시가 매도 후 현금**
- 고정 1천만원 모드: 신호마다 1,000만원 독립 매수 (참고용)

---

## 4. PPT 구성

### 세트별 상세 (7장 × N세트)

1. 표지  2. 백테스트 조건  3. 고정 1천만  4. 뱅크롤 복리+년도별  5. 성장곡선  6. 년도별+비교  7. 주의사항

### 비교 PPT 앞부분 (공통)

1. **표지** (프리셋 부제)
2. **비교 요약 표** — 거래대금·**거래비용**·매매건수·매매일·일평균·**10종초과일%**·승률·뱅크롤·CAGR·MDD
3. **비교 차트 2×3** — 최종뱅크롤·CAGR·MDD·매매건수·일평균·10종초과일%

- 차트 PNG: `02_maxV2/_ppt_charts/` (`chart_sets_compare_{preset}.png`, `chart_growth_{key}.png` …)
- 저장: **바탕화면** (`Desktop` / `OneDrive\Desktop` / `바탕 화면`)
- 비용 표기: `_cost_pct()` — **0.5% 등 소수 비용 슬라이드 전체 통일** (2026-07-01 수정)

### 결과 dict

```python
{
  "params": {...},           # cost_mult, vol_spike_mult, sell_mode, vol_lag, use_volume ...
  "fixed": {...},            # trades, win_rate, net_pnl, avg_daily ...
  "bankroll": {...},         # final_bankroll, cagr, mdd ...
  "yearly": [...],
  "equity_curve": [...],
  "signal_freq": {           # run_baseline에서 산출
    "signal_days", "over_slot_days", "over_slot_pct", "avg_signals_per_day"
  }
}
```

---

## 5. 파라미터 전체 (`run_baseline`)

| 인자 | 기본 | 의미 | CLI |
|------|------|------|-----|
| `K` | 0.7 | 돌파 계수 | `--K` |
| `vol_mult` | 3.0 | 거래대금 하한 배수 (300%) | `--vol-mult` |
| `vol_mult_hi` | None | 거래대금 **상한** 배수 (구간 필터) | (코드 only) |
| `vol_lag` | 0 | `1`=전일 거래대금 폭발+당일 돌파 (PIT) | `--vol-lag` |
| `use_volume` | True | `False`=거래대금 필터 제거 | `--no-volume` |
| `buy_price_mode` | target | `close`=당일 종가 체결 | `--buy-close` |
| `require_breakout` | True | `False`=고가≥target 미적용 | `--no-breakout` |
| `mcap_side` | top | `bottom`=시총 하위 mcap_ratio% | `--mcap-bottom` |
| `mcap_ratio` / `mcap_ratio_hi` | 0.1 / None | 상위10% 또는 밴드(10~20%) | `--mcap-ratio` `--mcap-ratio-hi` |
| `sell_mode` | next_open | `same_close`=당일 종가 | `--sell-close` |
| `cost_mult` | 0.99 | 1% 비용. 0.995=0.5% | `--cost 0.5` |
| `use_ma5` | True | MA5 필터 | `--no-ma5` |
| `target_mode` | k_breakout | `prev_high`=전일 고가 매수·돌파 | (코드 only) |
| `use_mcap` | True | `False`=시총 필터 제거 | (코드 only) |
| `mcap_lag` | 1 | 시총 D-1 | `--mcap-lag` |
| `seed` | 42 | 랜덤 10종 | `--seed` |
| `initial_bankroll` | 1e8 | 시작 뱅크롤 | `--bankroll` |
| `max_daily_positions` | 10 | 슬롯 | `--slots` |
| `frames` | None | 재사용 프레임 dict | (코드 only) |

---

## 6. 2026-07-01 세션 작업 내역 (상세)

### 6-1. 코드 구현 (엔진·PPT)

| 파일 | 변경 내용 |
|------|-----------|
| `repair_v13_baseline.py` | `load_frames()` 분리 · `mcap_ratio_hi` 밴드 · `sell_mode` · `vol_lag` · `use_volume` · `signal_freq` 산출 · `_build_daily_cutoffs` DataFrame화 |
| `repair_v13_bankroll.py` | `_simulate_bankroll(sell_mode)` 당일종가/익일시가 분기 · `_signals_to_fixed_trades` sell_price · sell_price in signals |
| `build_backtest_ppt.py` | `add_detail_slides()` 분리 · `_vol_label` `_cost_pct` `_mcap_span` · 조건표/주의사항/표지 비용·거래대금 자동 반영 · `make_charts(tag=)` · `_table(row_h=)` |
| `build_backtest_ppt_sets.py` | **신규** → PRESETS(`volume`, `base_adj`) · `--preset` CLI · 비교표 빈도 지표 · 2×3 비교차트 |

### 6-2. 실행한 백테스트 & PPT 산출물

| # | 프리셋 / 내용 | 바탕화면 PPT | 슬라이드 |
|---|---------------|-------------|----------|
| 1 | **시총/K/매도** — 기준 vs 시총10~20% vs K=1 vs 당일종가 | `repair_v13_condition_sets_compare.pptx` | 31 |
| 2 | **거래대금** — 기준 vs A1(전일) vs A2(제거) vs C(600%) | `repair_v13_volume_sets_compare.pptx` | 31 |
| 3 | **base_adj** — 기준 vs 600% vs 600%+0.5% | `repair_v13_base_adj_sets_compare.pptx` | 24 |

※ 기존 PPT는 서로 **덮어쓰지 않음**. 파일명으로 구분.

### 6-3. 백테스트 결과 수치 (2020-01-01~2026-03-06, seed=42)

#### A) 시총 / K / 매도 (`--preset` 없음, 최초 condition 세트)

| 세트 | 변경 | 고정 매매/승률/순손익 | 뱅크롤 최종 | CAGR | MDD |
|------|------|----------------------|-------------|------|-----|
| 기준 | — | 5,378 / 69.11% / +13.4억 | 31.74조 | +677.5% | 7.08% |
| 세트1 | 시총 10~20% | 7,576 / 63.95% / +15.2억 | 117.03조 | +860.4% | 11.81% |
| 세트2 | K=1 | 4,856 / 63.14% / +9.1억 | 5,697.7억 | +305.6% | 7.07% |
| 세트3 | 당일 종가 | 5,386 / 67.19% / +10.5억 | 2.03조 | +398.2% | 7.28% |

#### B) 거래대금 (`--preset volume`)

| 세트 | 조건 | 고정 매매/승률 | 10종초과일 | 뱅크롤 최종 | CAGR | MDD |
|------|------|----------------|-----------|-------------|------|-----|
| 기준 | 당일 300% | 5,378 / 69.11% | 3.4% | 31.74조 | +677.5% | 7.08% |
| A1 | **전일** 300% (PIT) | 1,018 / 35.76% | ~0% | **0.5억(손실)** | **-10.7%** | 53.71% |
| A2 | 필터 제거 | 49,394 / 32.11% | **89.7%** | 0.0억 | -63.4% | 99.81% |
| C | 당일 600% | 1,415 / 76.18% | ~0% | 1,149.2억 | +213.0% | 3.73% |

#### C) base_adj (`--preset base_adj`) — **최종 확정 구성**

| 세트 | 거래대금 | 비용 | 고정 매매/승률/순손익 | 뱅크롤 최종 | CAGR | MDD |
|------|----------|------|----------------------|-------------|------|-----|
| **기준** | 당일 300% | 1% | 5,378 / 69.11% / +13.4억 | 31.74조 | +677.5% | 7.08% |
| **세트1** | **600%** | 1% | 1,415 / 76.18% / +7.1억 | 1,149.2억 | +213.0% | 3.73% |
| **세트2** | **600%** | **0.5%** | 1,415 / 79.08% / +7.9억 | 2,411.0억 | +252.8% | 3.60% |

- 세트1↔2 매매 **건수 동일**(1,415) — 거래대금 조건 같고 비용만 다름.
- 세트2 PPT 비용 표기 버그(0.5%↔1% 혼재) → `_cost_pct()` 수정 후 재생성 완료.

### 6-4. ★ 핵심 발견·합의 (세션 중 논의, 미구현 포함)

1. **거래대금 룩어헤드 (가장 중요)**  
   - 기준(당일 최종 거래대금) vs A1(전일) → **성과 +31.74조 vs 원금 손실**.  
   - 기준선 화려한 수치의 상당 부분은 **미래정보(당일 마감 거래량)** 에 기인.  
   - 실전 proxy: **돌파 시점 장중 누적 거래대금** (일봉만으로는 미구현).

2. **거래대금 필터 필수** — A2(제거) 전멸. 유동성/거래 확인 없는 돌파는 노이즈.

3. **600% 강화** — 신호↓·승률↑·MDD↓. 단 당일 기준이라 룩어헤드 여전히 포함.

4. **비용 0.5%** — 동일 신호에서 승률·뱅크롤 소폭 개선 (base_adj 세트2).

5. **시총/K/매도 (condition 세트)**  
   - K=0.7 > K=1 · 시총 10% > 10~20% 밴드(리스크) · 익일 시가 > 당일 종가(성과, 실전 08:50 청산과 일치).

6. **세트4 (갭업 미체결)** — **패스**  
   - target = **당일 시가 + range×K** 이므로 Open > target 불가(K>0). 전일 종가 기준이 아님.  
   - 사용자 착각 확인 후 보류.

7. **유동성 캡(B1/B2)** — 사용자 판단: 성과 비교 목적상 **패스** (1억·종목당~1천만은 고가주 floor=0 방지 목적).

8. **실전 전략 의견 (백테스트 아님)** — handoff 기록용  
   - 유지: 시총10%·MA5·K0.7·익일 시가(08:50)  
   - 거래대금: 장중 누적 기준 재정의 필요  
   - A1(전일만)은 실전 edge 약함 → watchlist+장중 누적 proxy 권장

### 6-5. 버그 수정 이력

| 일시 | 내용 |
|------|------|
| 2026-07-01 | `cost_mult=0.995`(0.5%) PPT 표지·조건표 `.0f` → 1%로 오표시. `_cost_pct()` 도입, 전 슬라이드 통일 |

---

## 7. 2026-07-02 세션 작업 내역

### 7-1. 코드 확장

| 파일 | 변경 |
|------|------|
| `repair_v13_baseline.py` | `buy_price_mode` · `require_breakout` · `mcap_side`(하위10%) · `vol_mult_hi`(거래대금 구간) |
| `repair_v13_bankroll.py` | `_sig_buy_price()` — `buy_price` 필드로 체결가 분리 |
| `build_backtest_ppt.py` | `_breakout_label` · `_buy_base_label` · `_vol_label` 구간 표기 |
| `build_backtest_ppt_sets.py` | PRESETS 4종 추가 · 8열 비교표 글꼴 축소 |

### 7-2. 백테스트 & PPT (바탕화면)

| 프리셋 | 내용 | PPT |
|--------|------|-----|
| `entry_mcap` | 기준 vs MA5제외 vs 시총하위10% vs (구)종가기준 | `repair_v13_entry_mcap_sets_compare.pptx` |
| `close_buy` | 기준 vs **돌파+종가매수** vs **돌파X+종가매수** | `repair_v13_close_buy_sets_compare.pptx` |
| `vol_sweep` | 100~600% **임계값** (5일평균×N% 이상) | `repair_v13_vol_sweep_sets_compare.pptx` |
| `vol_bands` | 50~100% … 600%+ **구간** + 기준(≥300%) | `repair_v13_vol_bands_sets_compare.pptx` |

### 7-3. 핵심 결과 수치 (seed=42, 2020-01-01~2026-03-06)

#### A) close_buy — 돌파 유지, **체결가만** 종가

| 세트 | 매매/승률 | 뱅크롤 | CAGR | MDD |
|------|-----------|--------|------|-----|
| 기준 (돌파가) | 5,378 / 69.11% | 31.74조 | +677.5% | 7.08% |
| 돌파+종가매수 | 5,378 / 30.22% | 0.1억 | -32.8% | 93.19% |
| 돌파X+종가매수 | 6,709 / 28.86% | 0.0억 | -41.2% | 96.91% |

→ 돌파 조건은 유지하고 **체결가를 종가**로 바꾸면 승률·성과 **급락** (고점 추격).

#### B) vol_sweep — 5일평균×N% **이상**

| N% | 매매/승률 | 뱅크롤 | CAGR |
|----|-----------|--------|------|
| 100 | 33,451 / 40% | 0.0억 | -40% |
| 200 | 11,703 / 60% | 8.33조 | +526% |
| **300 (기준)** | **5,378 / 69%** | **31.74조** | **+678%** |
| 400 | 3,089 / 73% | 4.65조 | +470% |
| 500 | 1,999 / 76% | 6,333억 | +313% |
| 600 | 1,415 / 76% | 1,149억 | +213% |

#### C) vol_bands — 5일평균× **구간**

| 구간 | 매매/승률 | 뱅크롤 | CAGR |
|------|-----------|--------|------|
| **기준 ≥300%** | 5,378 / 69.11% | 31.74조 | +677.5% |
| 50~100% | 14,600 / 16% | 0.0억 | -65% |
| 100~200% | 21,748 / 29% | 0.0억 | -65% |
| 200~300% | 6,325 / 52% | 4.0억 | +25% |
| 300~400% | 2,289 / 63% | 10.5억 | +46% |
| 400~500% | 1,090 / 69% | 8.1억 | +40% |
| 500~600% | 584 / 74% | 5.6억 | +32% |
| **600%+** | 1,415 / 76% | 1,149억 | +213% |

- 기준(≥300%) 매매 5,378 = 300~400 + 400~500 + 500~600 + 600%+ **합** (구간 분할 정합).
- **구간별 뱅크롤이 작은 이유**: 동일 5,378건을 **서로 배타적 구간으로 쪼개** 각각 **독립 1억 복리**를 돌린 결과. 매매 횟수·연속 복리 기회가 줄어듦. 기준은 ≥300% **전체를 한 포트**로 복리.

### 7-4. 논의·합의 (미구현 → 2026-07-04 프록시 **보류**)

1. **EOD 거래대금 룩어헤드** — vol_sweep·vol_bands 모두 당일 **최종** 거래대금 기준.
2. **장중 300% 조기진입** — 일봉으로 검증 불가. 분봉 없음 → **가격경로 비례(C) 포함 프록시 백테스트 진행 안 함**.
3. **종가 매수** — close_buy 세트로 실전 비권장 확인.
4. **백테스트↔실매매 비교** — [BACKTEST_LIVE_COMPARE_HANDOFF.md](./BACKTEST_LIVE_COMPARE_HANDOFF.md) (Desktop `repair_v13.py` vs `main.py`, 2026-07-04).

---

## 8. 이전 세션 결과 (참고, PPT 별도)

### 스냅샷 2024~2025
- PPT: `repair_v13_bankroll_backtest_2024_2025.pptx`

### 단일 baseline 2020~2026
- PPT: `repair_v13_baseline_backtest_2020_2026.pptx`
- 뱅크롤 최종 ~31.7조 · CAGR +677.55% · MDD 7.08%

### 회계 정합성
- `시작 1억 + Σ체결PnL ≈ 최종 뱅크롤` (스냅샷 2024-25: 차이 10원). 엔진 버그 없음.

---

## 9. 새 채팅 시작 문장 (예시)

```
c:\cursor\02_maxV2\BACKTEST_PPT_HANDOFF.md 읽고 이어서 진행.
아래 조건 세트들로 백테스트 + PPT 만들어줘.
(세트 1) ...
```

```powershell
python build_backtest_ppt_sets.py --preset {이름}
```

---

## 10. 바탕화면 PPT 파일 목록 (2026-07-02 기준)

| 파일 | 내용 |
|------|------|
| `repair_v13_baseline_backtest_2020_2026.pptx` | 단일 baseline (이전) |
| `repair_v13_bankroll_backtest_2024_2025.pptx` | 스냅샷 (이전) |
| `repair_v13_condition_sets_compare.pptx` | 시총/K/매도 4세트 (07-01) |
| `repair_v13_volume_sets_compare.pptx` | 거래대금 A1/A2/C (07-01) |
| `repair_v13_base_adj_sets_compare.pptx` | 기준 · 600% · 600%+0.5% (07-01) |
| `repair_v13_entry_mcap_sets_compare.pptx` | MA5/시총하위/종가 (07-02) |
| `repair_v13_close_buy_sets_compare.pptx` | 돌파+종가매수 (07-02) |
| `repair_v13_vol_sweep_sets_compare.pptx` | 거래대금 100~600% (07-02) |
| `repair_v13_vol_bands_sets_compare.pptx` | 거래대금 구간별 (07-02) |
