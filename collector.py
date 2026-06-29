import pandas as pd
from pykrx import stock
import time
import os
import datetime
import logging

# ==========================================
# 설정 옵션
# ==========================================
DATA_DIR = "data/raw"
CHECKPOINT_FILE = "checkpoint.txt"
START_DATE = "20160101"
END_DATE = datetime.datetime.today().strftime("%Y%m%d")
TEST_MODE = False  # True로 설정 시 2개의 종목만 테스트 형식으로 가져옵니다
TEST_TICKER_COUNT = 2

# ==========================================
# 로깅 설정
# ==========================================
os.makedirs(DATA_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("collector.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)


import urllib.request
import json

def get_target_tickers():
    """네이버 금융 모바일 API를 사용하여 현재 상장된 전체 종목코드(티커) 목록 추출"""
    logging.info("수집 대상 티커 목록을 네이버 금융 API에서 자동으로 가져옵니다.")
    
    if TEST_MODE:
        logging.info("KRX 서버 스크래핑 우회를 위해 테스트 모드에서는 고정된 종목(삼성전자, SK하이닉스)을 사용합니다.")
        return ['005930', '000660']

    all_tickers = set()
    
    try:
        # 네이버 금융 API 연동 (sosok 0: KOSPI, 1: KOSDAQ)
        # pageSize를 10000으로 충분히 크게 주어 한 번에 전체 목록을 가져옵니다.
        markets = {'KOSPI': 0, 'KOSDAQ': 1}
        
        for market_name, sosok in markets.items():
            logging.info(f"{market_name} 종목 목록을 요청합니다...")
            url = f"https://m.stock.naver.com/api/json/sise/siseListJson.nhn?menu=market_sum&sosok={sosok}&pageSize=10000&page=1"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                
                item_list = data.get('result', {}).get('itemList', [])
                if not item_list:
                    logging.warning(f"{market_name} 종목을 불러오지 못했습니다.")
                    continue
                    
                # 항목들에서 종목코드('cd') 추출
                market_tickers = [item['cd'] for item in item_list]
                all_tickers.update(market_tickers)
                logging.info(f"{market_name} 상장 종목: {len(market_tickers)}개 확보")
                
            time.sleep(0.5)
            
    except Exception as e:
        logging.error(f"네이버 금융 API에서 티커 목록 수집 중 오류: {e}")
        
    tickers = list(all_tickers)
    
    if not tickers:
        logging.error("❌ 티커 목록을 가져오는 데 완전히 실패했습니다. 인터넷 연결을 확인해주세요.")
        return []
        
    logging.info(f"총 {len(tickers)}개의 고유 티커를 성공적으로 확보했습니다. (현재 상장 기준)")
    return tickers

def load_checkpoint():
    """완료된 티커 로드"""
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r", encoding='utf-8') as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_checkpoint(ticker):
    """완료된 티커 저장"""
    with open(CHECKPOINT_FILE, "a", encoding='utf-8') as f:
        f.write(ticker + "\n")

def fetch_data_with_retry(fetch_func, *args, retries=3, delay=5):
    """API 요청 실패 시 재시도하는 래퍼 함수 (서버 차단 및 일시적 연결 오류 방어용)"""
    for attempt in range(retries):
        try:
            df = fetch_func(*args)
            return df
        except Exception as e:
            logging.warning(f"데이터 수집 에러 발생 (시도 {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                logging.error(f"최대 재시도 횟수 초과: {e}")
                return None

def process_ticker(ticker):
    """개별 티커 데이터 확보 및 가공 로직"""
    # 1. 주가 정보 (OHLCV, 등락률, 거래량, 거래대금 등) 수집
    df_ohlcv = fetch_data_with_retry(stock.get_market_ohlcv, START_DATE, END_DATE, ticker)
    time.sleep(1) # 차단 방지를 위한 요청 간 휴식 (필수)
    
    # 2. 거래대금/시가총액/상장주식수 수집
    df_cap = fetch_data_with_retry(stock.get_market_cap, START_DATE, END_DATE, ticker)
    time.sleep(1) # 차단 방지를 위한 요청 간 휴식 (필수)
    
    if df_ohlcv is None or df_ohlcv.empty:
        logging.warning(f"[{ticker}] 주가(OHLCV) 데이터가 존재하지 않습니다. 스킵합니다.")
        return False
        
    # 데이터 병합 (날짜 인덱스 기준으로 Left Join)
    if df_cap is None or df_cap.empty:
        logging.warning(f"[{ticker}] 시가총액 데이터가 없어 주가 데이터만 저장합니다.")
        df_merged = df_ohlcv
    else:
        # 중복 컬럼 (예: 종가, 거래량 등)을 걸러내고 병합
        cols_to_use = df_cap.columns.difference(df_ohlcv.columns)
        df_merged = df_ohlcv.join(df_cap[cols_to_use])
    
    # 3. 추가 지표 계산: 전일 노이즈 비율
    # 노이즈 비율 = 1 - abs(종가 - 시가) / (고가 - 저가)
    if all(col in df_merged.columns for col in ['시가', '고가', '저가', '종가']):
        high_low_diff = df_merged['고가'] - df_merged['저가']
        # 고가와 저가가 같은 날(점상, 점하 등)에는 0으로 나누어짐을 방지하고자 NA로 변환 계산
        # abs(종가 - 시가)를 통해 노이즈의 방향성과 무관하게 절대적인 비율 산출
        noise_ratio = 1 - abs(df_merged['종가'] - df_merged['시가']) / high_low_diff.replace(0, pd.NA)
        
        # '전일' 노이즈 비율 파생 변수 생성
        df_merged['전일_노이즈비율'] = noise_ratio.shift(1)

    # 4. CSV로 최종 저장
    save_path = os.path.join(DATA_DIR, f"{ticker}.csv")
    df_merged.to_csv(save_path, encoding='utf-8-sig')
    return True

def main():
    logging.info("========== Pengo 프로젝트 주식 데이터 수집기 시작 ==========")
    tickers = get_target_tickers()
    processed_tickers = load_checkpoint()
    
    logging.info(f"체크포인트 상태: 총 {len(processed_tickers)}개 종목 이미 완료됨.")
    
    if TEST_MODE:
        tickers = tickers[:TEST_TICKER_COUNT]
        logging.info("============================================================")
        logging.info(f"🛠️ 테스트 모드가 켜져 있습니다! {TEST_TICKER_COUNT}개의 종목만 수집합니다.")
        logging.info("============================================================")
        
    total = len(tickers)
    for count, ticker in enumerate(tickers, start=1):
        if ticker in processed_tickers:
            continue
            
        logging.info(f"진행 중: [{ticker}] ({count}/{total})")
        
        try:
            success = process_ticker(ticker)
            if success:
                save_checkpoint(ticker)
        except KeyboardInterrupt:
            logging.info("사용자 인터럽트로 인해 실행을 중단합니다. 체크포인트가 저장되었습니다.")
            break
        except Exception as e:
            logging.error(f"[{ticker}] 처리 중 예상치 못한 치명적 오류 발생: {e}")
            logging.info("⚠️ 수집 프로세스를 안전하게 종료합니다. 나중에 다시 실행하면 중단된 곳부터 이어서 수집됩니다.")
            break

    logging.info("========== 데이터 수집 프로세스 종료 ==========")

if __name__ == "__main__":
    main()
