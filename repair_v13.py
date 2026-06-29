import pandas as pd
import os
import numpy as np
from tqdm import tqdm

DATA_DIR = "data/raw"
OUTPUT_FILE = "backtest_v15_survival.csv"

def run_backtest_v15():
    file_list = [f for f in os.listdir(DATA_DIR) if f.endswith(".csv")]
    print(f"🚀 V15 생존 전략 테스트 시작 (2024-2025년)")
    print("📌 조건: K=0.7, 거래대금 200% 폭발, 이평 필터, 시총 상위 10%, 비용 1%")

    # 1. 시총 상위 10% 커트라인 산출
    caps = []
    for filename in file_list:
        try:
            temp_df = pd.read_csv(os.path.join(DATA_DIR, filename), encoding='utf-8-sig').iloc[-1:]
            if 'MarketCap' in temp_df.columns: caps.append(temp_df['MarketCap'].iloc[0])
        except: continue
    cap_cutoff = np.percentile(caps, 90) if caps else 0

    all_results = []
    col_map = {'날짜': 'Date', '시가': 'Open', '고가': 'High', '저가': 'Low', '종가': 'Close', '거래량': 'Volume'}

    for filename in tqdm(file_list):
        file_path = os.path.join(DATA_DIR, filename)
        ticker = filename.replace(".csv", "")
        
        try:
            df = pd.read_csv(file_path, encoding='utf-8-sig')
            df.rename(columns=col_map, inplace=True)
            
            # [필터 1] 시총 필터
            if 'MarketCap' not in df.columns or df['MarketCap'].iloc[-1] < cap_cutoff: continue

            # [필터 2] 기간 제한 (24-25년)
            df['Date'] = pd.to_datetime(df['Date'])
            df = df[(df['Date'] >= '2024-01-01') & (df['Date'] <= '2025-12-31')].copy()
            if len(df) < 10: continue

            # 데이터 정제
            cols = ['Open', 'High', 'Low', 'Close', 'Volume']
            df[cols] = df[cols].apply(pd.to_numeric, errors='coerce')
            df = df.dropna(subset=cols)

            # --- 지표 계산 ---
            # [조건 1] K값 0.7
            K = 0.7
            df['range'] = df['High'].shift(1) - df['Low'].shift(1)
            df['target'] = df['Open'] + df['range'] * K
            
            # [조건 2] 거래대금 폭발 (당일 거래대금 > 최근 5일 평균의 300%)
            df['Value'] = df['Close'] * df['Volume']
            df['Value_MA5'] = df['Value'].rolling(window=5).mean()
            df['vol_spike'] = df['Value'] >= (df['Value_MA5'].shift(1) * 3.0)

            # [조건 3] 지수 필터 (종목의 5일선으로 지수 필터 대용 시뮬레이션)
            # ※ 실제 지수 데이터를 넣으려면 지수 CSV를 따로 읽어 merge해야 함
            df['MA5'] = df['Close'].rolling(window=5).mean()
            df['market_ok'] = df['Close'].shift(1) > df['MA5'].shift(1)

            # --- 매매 로직 ---
            # 진입: 타겟가 돌파 + 거래대금 폭발 + 지수(5MA) 필터
            df['is_buy'] = (df['High'] >= df['target']) & \
                           (df['vol_spike']) & \
                           (df['market_ok'])

            # 수익률 계산 (익일 시가 매도 + 거래비용 1% 반영)
            df['ror'] = 1.0
            df.loc[df['is_buy'], 'ror'] = (df['Open'].shift(-1) / df['target']) * 0.99
            
            # 누적 수익률
            valid_ror = df['ror'].iloc[:-1].dropna()
            if not valid_ror.empty:
                hpr = valid_ror.prod()
                all_results.append({'ticker': ticker, 'final_ror': hpr, 'trade_count': df['is_buy'].sum()})

        except Exception: continue

    res_df = pd.DataFrame(all_results)
    if res_df.empty:
        print("❌ 조건에 부합하는 매매가 발생하지 않았습니다.")
        return

    print("\n" + "="*40)
    print(f"📊 V15 최종 결과")
    print(f"📈 평균 누적 수익률: {(res_df['final_ror'].mean() - 1) * 100:.2f}%")
    print(f"📉 승률 (종목 기준): {len(res_df[res_df['final_ror'] > 1]) / len(res_df) * 100:.2f}%")
    print(f"🔄 평균 매매 횟수: {res_df['trade_count'].mean():.1f}회")
    
    res_df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')

if __name__ == "__main__":
    run_backtest_v15()