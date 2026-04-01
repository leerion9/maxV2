from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple

import requests
from zoneinfo import ZoneInfo

from config.settings import Settings


def _kis_json_payload_rate_limited(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    msg = str(data.get("msg1", "") or data.get("message", "") or "")
    return "EGW00201" in msg or "초당 거래건수" in msg


def _response_is_rate_limited(resp: requests.Response) -> bool:
    if resp.status_code == 429:
        return True
    try:
        data = resp.json()
    except Exception:
        txt = resp.text or ""
        return "EGW00201" in txt or "초당 거래건수" in txt
    return _kis_json_payload_rate_limited(data)


@dataclass
class Quote:
    symbol: str
    current_price: int
    open_price: int
    volume: int
    prev_high: int
    prev_low: int


class KISApiClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.token: str = ""
        self.token_expire_at: Optional[datetime] = None
        self._last_api_monotonic: float = 0.0
        self._server_time_offset_sec: float = 0.0
        self._server_time_offset_updated_at: float = 0.0

    def _token_is_valid(self) -> bool:
        return bool(self.token) and self.token_expire_at is not None and datetime.now() < self.token_expire_at

    def ensure_token(self) -> None:
        if self._token_is_valid():
            return

        url = f"{self.settings.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.settings.app_key,
            "appsecret": self.settings.app_secret,
        }
        resp = self.session.post(url, json=payload, timeout=self.settings.request_timeout_sec)
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(
                "KIS token request failed. Check APP_KEY/APP_SECRET, API 신청 상태, 접근 IP 허용 설정."
            ) from exc
        self._update_server_time_offset_from_response(resp)
        data = resp.json()
        self.token = data["access_token"]
        self.token_expire_at = datetime.now() + timedelta(hours=23)

    def server_time_offset_sec(self) -> float:
        return float(self._server_time_offset_sec or 0.0)

    def now_kst(self) -> datetime:
        ts = time.time() + self.server_time_offset_sec()
        return datetime.fromtimestamp(ts, tz=ZoneInfo("Asia/Seoul"))

    def _update_server_time_offset_from_response(self, resp: requests.Response) -> None:
        try:
            date_hdr = (resp.headers or {}).get("Date", "")
            if not date_hdr:
                return
            server_dt = parsedate_to_datetime(date_hdr)
            if server_dt.tzinfo is None:
                return
            server_ts = server_dt.timestamp()
            local_ts = time.time()
            self._server_time_offset_sec = float(server_ts - local_ts)
            self._server_time_offset_updated_at = local_ts
        except Exception:
            return

    def _kis_retry_max(self) -> int:
        return int(getattr(self.settings, "kis_api_retry_max", 8))

    def _kis_rate_sleep(self) -> float:
        return float(getattr(self.settings, "kis_rate_limit_retry_sleep_sec", 1.0))

    def _pace_api(self) -> None:
        gap = float(getattr(self.settings, "kis_min_request_interval_sec", 0.15))
        if gap <= 0:
            return
        now = time.monotonic()
        wait = gap - (now - self._last_api_monotonic)
        if wait > 0:
            time.sleep(wait)
        self._last_api_monotonic = time.monotonic()

    def _headers(self, tr_id: str) -> Dict[str, str]:
        self.ensure_token()
        self._pace_api()
        return {
            "authorization": f"Bearer {self.token}",
            "appkey": self.settings.app_key,
            "appsecret": self.settings.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
            "content-type": "application/json",
        }

    def _request_get_json(
        self,
        url: str,
        *,
        tr_id: str,
        params: Dict[str, str],
        error_prefix: str,
    ) -> dict:
        last_error: Optional[Exception] = None
        for _ in range(self._kis_retry_max()):
            try:
                resp = self.session.get(
                    url,
                    headers=self._headers(tr_id=tr_id),
                    params=params,
                    timeout=self.settings.request_timeout_sec,
                )
                self._update_server_time_offset_from_response(resp)
                if resp.status_code >= 400:
                    if _response_is_rate_limited(resp):
                        time.sleep(self._kis_rate_sleep())
                        last_error = RuntimeError(_http_error_detail(resp))
                        continue
                    detail = _http_error_detail(resp)
                    raise RuntimeError(f"{error_prefix}: {detail}")
                data = resp.json()
                if _kis_json_payload_rate_limited(data):
                    time.sleep(self._kis_rate_sleep())
                    last_error = RuntimeError("KIS rate limit in JSON body")
                    continue
                return data
            except ValueError as exc:
                last_error = exc
                time.sleep(0.35)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(0.35)
        raise RuntimeError(f"{error_prefix}: retries exhausted ({last_error})") from last_error

    def get_cash_balance(self) -> int:
        """
        KIS: 주문가능현금 조회.
        """
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-order"
        params = {
            "CANO": self.settings.cano,
            "ACNT_PRDT_CD": self.settings.acnt_prdt_cd,
            "PDNO": "005930",
            "ORD_UNPR": "0",
            "ORD_DVSN": "01",
            "CMA_EVLU_AMT_ICLD_YN": "N",
            "OVRS_ICLD_YN": "N",
        }
        tr_id = "VTTC8908R" if self.settings.is_paper_trading else "TTTC8908R"
        data = self._request_get_json(
            url, tr_id=tr_id, params=params, error_prefix="KIS CASH request failed"
        )
        rt = str(data.get("rt_cd", "0") or "0")
        if rt not in ("", "0"):
            msg = str(data.get("msg1", ""))
            raise RuntimeError(f"KIS CASH business error(rt_cd={rt}): {msg}")
        return int(data["output"]["ord_psbl_cash"])

    def get_domestic_balance_summary(self) -> Dict[str, object]:
        """
        KIS: 주식잔고조회 (요약 정보).

        Notes:
        - We intentionally keep a small, tolerant parser because field names can vary.
        - Returns a dict containing:
          - output2: account-level summary (first row if list)
          - raw: the full JSON payload (for logging / later tuning)
        """
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO": self.settings.cano,
            "ACNT_PRDT_CD": self.settings.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        tr_id = "VTTC8434R" if self.settings.is_paper_trading else "TTTC8434R"
        data = self._request_get_json(
            url,
            tr_id=tr_id,
            params=params,
            error_prefix="KIS balance summary request failed",
        )
        output2 = data.get("output2", {})
        if isinstance(output2, list):
            output2_row = output2[0] if output2 else {}
        elif isinstance(output2, dict):
            output2_row = output2
        else:
            output2_row = {}
        return {"output2": output2_row, "raw": data}

    def get_domestic_balance_positions(self) -> List[Dict[str, object]]:
        """
        KIS: 주식잔고조회 (종목별 보유 목록).

        Returns a list of dicts with at least:
          - symbol: str (6-digit code)
          - qty: int (holding quantity)
          - name: str (if provided by API; may be empty)
        """
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO": self.settings.cano,
            "ACNT_PRDT_CD": self.settings.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        tr_id = "VTTC8434R" if self.settings.is_paper_trading else "TTTC8434R"
        data = self._request_get_json(
            url,
            tr_id=tr_id,
            params=params,
            error_prefix="KIS balance positions request failed",
        )
        out = data.get("output1", [])
        if isinstance(out, dict):
            rows = [out]
        elif isinstance(out, list):
            rows = out
        else:
            rows = []

        positions: List[Dict[str, object]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            symbol = str(r.get("pdno", "") or r.get("PDNO", "") or "").strip()
            if not symbol:
                symbol = str(r.get("prdt_code", "") or "").strip()
            qty_raw = str(
                r.get("hldg_qty", "")
                or r.get("HLDG_QTY", "")
                or r.get("hldg_qty", "")
                or "0"
            ).strip()
            try:
                qty = abs(int(float(qty_raw))) if qty_raw else 0
            except Exception:
                qty = 0
            if qty <= 0:
                continue
            name = str(r.get("prdt_name", "") or r.get("PRDT_NAME", "") or "").strip()
            positions.append({"symbol": symbol, "qty": qty, "name": name})
        return positions

    def get_quote(self, symbol: str) -> Quote:
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol}
        last_error: Optional[Exception] = None
        output: Optional[dict] = None
        got_quote = False
        for _ in range(self._kis_retry_max()):
            try:
                resp = self.session.get(
                    url,
                    headers=self._headers("FHKST01010100"),
                    params=params,
                    timeout=self.settings.request_timeout_sec,
                )
                self._update_server_time_offset_from_response(resp)
                if resp.status_code >= 400 and _response_is_rate_limited(resp):
                    time.sleep(self._kis_rate_sleep())
                    last_error = RuntimeError(_http_error_detail(resp))
                    continue
                resp.raise_for_status()
                body = resp.json()
                if _kis_json_payload_rate_limited(body):
                    time.sleep(self._kis_rate_sleep())
                    last_error = RuntimeError("quote rate limited")
                    continue
                output = body["output"]
                got_quote = True
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                resp = getattr(exc, "response", None)
                if resp is not None and _response_is_rate_limited(resp):
                    time.sleep(self._kis_rate_sleep())
                else:
                    time.sleep(0.5)
        if not got_quote or output is None:
            raise RuntimeError(f"quote request failed for {symbol}: {last_error}") from last_error

        return Quote(
            symbol=symbol,
            current_price=abs(int(output["stck_prpr"])),
            open_price=abs(int(output["stck_oprc"])),
            volume=abs(int(output["acml_vol"])),
            prev_high=abs(int(output["stck_hgpr"])),
            prev_low=abs(int(output["stck_lwpr"])),
        )

    def get_daily_prices(self, symbol: str, days: int = 6) -> List[Dict[str, int]]:
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-price"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "1",
        }
        rows: List[Dict[str, str]] = []
        last_error: Optional[Exception] = None
        got_response = False
        for _ in range(self._kis_retry_max()):
            try:
                resp = self.session.get(
                    url,
                    headers=self._headers("FHKST01010400"),
                    params=params,
                    timeout=self.settings.request_timeout_sec,
                )
                self._update_server_time_offset_from_response(resp)
                if resp.status_code >= 400 and _response_is_rate_limited(resp):
                    time.sleep(self._kis_rate_sleep())
                    last_error = RuntimeError(_http_error_detail(resp))
                    continue
                resp.raise_for_status()
                body = resp.json()
                if _kis_json_payload_rate_limited(body):
                    time.sleep(self._kis_rate_sleep())
                    last_error = RuntimeError("daily-price rate limited")
                    continue
                rows = body.get("output", [])[:days]
                got_response = True
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                resp = getattr(exc, "response", None)
                if resp is not None and _response_is_rate_limited(resp):
                    time.sleep(self._kis_rate_sleep())
                else:
                    time.sleep(0.5)
        if not got_response:
            raise RuntimeError(f"daily-price request failed for {symbol}: {last_error}") from last_error

        parsed: List[Dict[str, int]] = []
        for r in rows:
            parsed.append(
                {
                    "close": abs(int(r["stck_clpr"])),
                    "high": abs(int(r["stck_hgpr"])),
                    "low": abs(int(r["stck_lwpr"])),
                    "volume": abs(int(r["acml_vol"])),
                }
            )
        return parsed

    def get_market_cap_rankings(self) -> List[str]:
        """
        KIS 국내주식 시가총액 상위 조회.
        시장별(KOSPI/KOSDAQ) 결과를 합쳐 시총 내림차순으로 반환한다.
        """
        merged: Dict[str, int] = {}
        for market in ("0001", "1001"):
            rows = self._get_market_cap_rows(fid_input_iscd=market)
            time.sleep(0.35)
            for row in rows:
                symbol = row.get("mksc_shrn_iscd", "").strip()
                if not symbol:
                    continue
                cap = abs(int(row.get("stck_avls", "0") or 0))
                if cap <= 0:
                    continue
                merged[symbol] = max(merged.get(symbol, 0), cap)

        sorted_symbols = [
            item[0] for item in sorted(merged.items(), key=lambda x: x[1], reverse=True)
        ]
        return sorted_symbols

    def _get_market_cap_rows(self, fid_input_iscd: str) -> List[Dict[str, str]]:
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/ranking/market-cap"
        tr_id = "FHPST01740000"
        param_candidates: Tuple[Dict[str, str], ...] = (
            {
                "FID_COND_SCR_DIV_CODE": "20174",
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_DIV_CLS_CODE": "1",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "000000",
                "FID_INPUT_PRICE_1": "0",
                "FID_INPUT_PRICE_2": "0",
                "FID_VOL_CNT": "0",
                "FID_BLNG_CLS_CODE": "0",
                "FID_INPUT_DATE_1": "0",
                "FID_INPUT_ISCD": fid_input_iscd,
            },
            {
                "fid_cond_scr_div_code": "20174",
                "fid_cond_mrkt_div_code": "J",
                "fid_div_cls_code": "1",
                "fid_trgt_cls_code": "111111111",
                "fid_trgt_exls_cls_code": "000000",
                "fid_input_price_1": "0",
                "fid_input_price_2": "0",
                "fid_vol_cnt": "0",
                "fid_blng_cls_code": "0",
                "fid_input_date_1": "0",
                "fid_input_iscd": fid_input_iscd,
            },
        )

        last_error: Optional[Exception] = None
        for params in param_candidates:
            try:
                resp = self.session.get(
                    url,
                    headers=self._headers(tr_id),
                    params=params,
                    timeout=self.settings.request_timeout_sec,
                )
                self._update_server_time_offset_from_response(resp)
                resp.raise_for_status()
                data = resp.json()
                rt_cd = str(data.get("rt_cd", ""))
                msg1 = str(data.get("msg1", ""))
                if rt_cd not in {"", "0"}:
                    raise RuntimeError(f"KIS ranking error(rt_cd={rt_cd}): {msg1}")

                output = data.get("output", [])
                output1 = data.get("output1", [])
                rows = output if isinstance(output, list) else output1 if isinstance(output1, list) else []
                if rows:
                    return rows
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                resp = getattr(exc, "response", None)
                if resp is not None and _response_is_rate_limited(resp):
                    time.sleep(self._kis_rate_sleep())
                continue

        raise RuntimeError(
            f"market-cap ranking request failed for market={fid_input_iscd}: {last_error}"
        )

    def get_holiday_info(self, base_date_yyyymmdd: str) -> List[Dict[str, str]]:
        """
        KIS: 국내휴장일조회.
        참고: 단시간 다회 호출을 피하기 위해 호출부에서 캐시 사용 권장.
        """
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/quotations/chk-holiday"
        params = {
            "BASS_DT": base_date_yyyymmdd,
            "CTX_AREA_FK": "",
            "CTX_AREA_NK": "",
        }
        data = self._request_get_json(
            url,
            tr_id="CTCA0903R",
            params=params,
            error_prefix="KIS holiday request failed",
        )
        output = data.get("output", [])
        if isinstance(output, dict):
            return [output]
        if isinstance(output, list):
            return output
        return []

    def is_open_trading_day(self, base_date_yyyymmdd: str) -> Optional[bool]:
        """
        Returns:
            True/False if KIS responds with open-day flag,
            None if the payload cannot be interpreted.
        """
        rows = self.get_holiday_info(base_date_yyyymmdd=base_date_yyyymmdd)
        if not rows:
            return None
        row = rows[0]
        open_flag = str(row.get("opnd_yn", "")).strip().upper()
        if open_flag == "Y":
            return True
        if open_flag == "N":
            return False
        return None

    def place_limit_buy(self, symbol: str, qty: int, price: int) -> Dict[str, str]:
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        body = {
            "CANO": self.settings.cano,
            "ACNT_PRDT_CD": self.settings.acnt_prdt_cd,
            "PDNO": symbol,
            "ORD_DVSN": "00",
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
        }
        tr_id = "VTTC0802U" if self.settings.is_paper_trading else "TTTC0802U"
        return self._post_order(url, body, tr_id)

    def place_market_sell(self, symbol: str, qty: int) -> Dict[str, str]:
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        body = {
            "CANO": self.settings.cano,
            "ACNT_PRDT_CD": self.settings.acnt_prdt_cd,
            "PDNO": symbol,
            "ORD_DVSN": "01",
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
        }
        tr_id = "VTTC0801U" if self.settings.is_paper_trading else "TTTC0801U"
        return self._post_order(url, body, tr_id)

    def _post_order(self, url: str, body: Dict[str, str], tr_id: str) -> Dict[str, str]:
        last_error: Optional[Exception] = None
        for _ in range(self._kis_retry_max()):
            try:
                resp = self.session.post(
                    url,
                    headers=self._headers(tr_id),
                    json=body,
                    timeout=self.settings.request_timeout_sec,
                )
                self._update_server_time_offset_from_response(resp)
                if resp.status_code >= 400 and _response_is_rate_limited(resp):
                    time.sleep(self._kis_rate_sleep())
                    last_error = RuntimeError(_http_error_detail(resp))
                    continue
                resp.raise_for_status()
                data = resp.json()
                if _kis_json_payload_rate_limited(data):
                    time.sleep(self._kis_rate_sleep())
                    last_error = RuntimeError("order rate limited")
                    continue
                return {
                    "rt_cd": data.get("rt_cd", ""),
                    "msg1": data.get("msg1", ""),
                    "ord_no": data.get("output", {}).get("ODNO", ""),
                }
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                resp = getattr(exc, "response", None)
                if resp is not None and _response_is_rate_limited(resp):
                    time.sleep(self._kis_rate_sleep())
                else:
                    time.sleep(0.7)
        raise RuntimeError(f"order failed after retry: {last_error}") from last_error


def _http_error_detail(resp: requests.Response) -> str:
    status = getattr(resp, "status_code", None)
    try:
        body_ct = (resp.headers or {}).get("content-type", "")
    except Exception:
        body_ct = ""
    preview = ""
    try:
        txt = resp.text or ""
        txt = txt.replace("\r", " ").replace("\n", " ").strip()
        preview = txt[:400]
    except Exception:
        preview = ""
    return f"status={status} content_type={body_ct!s} body_preview={preview!s}"
