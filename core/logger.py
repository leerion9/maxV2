from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict
import sys


class TradeLogger:
    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self._logger = logging.getLogger("maxv")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

        self._file_handler = logging.FileHandler(
            self.log_dir / "system.log",
            encoding="utf-8",
            delay=False,
        )
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )
        self._file_handler.setFormatter(formatter)
        self._stream_handler = logging.StreamHandler(stream=sys.stdout)
        self._stream_handler.setFormatter(formatter)
        self._logger.handlers.clear()
        self._logger.addHandler(self._file_handler)
        self._logger.addHandler(self._stream_handler)

        self.trade_csv = self.log_dir / "trades.csv"
        self._trade_fields = [
            "ts",
            "symbol",
            "symbol_name",
            "side",
            "qty",
            "price",
            "reason",
            "fee",
            "tax",
            "order_id",
            "cash_psbl",
            "balance_tot_asset",
            "balance_dnca",
            "balance_json",
            "pnl_cash_delta",
        ]
        self._ensure_csv_header(self.trade_csv, self._trade_fields)

        self.signal_csv = self.log_dir / "signals.csv"
        self._signal_fields = [
            "ts",
            "symbol",
            "symbol_name",
            "breakout_price",
            "reason",
            "action",
            "note",
        ]
        self._ensure_csv_header(self.signal_csv, self._signal_fields)
        self.info(f"Logger ready: log_dir={self.log_dir.resolve()!s}")

    @staticmethod
    def _ensure_csv_header(path: Path, fieldnames: list[str]) -> None:
        if not path.exists():
            with path.open("w", newline="", encoding="utf-8") as fp:
                writer = csv.DictWriter(fp, fieldnames=fieldnames)
                writer.writeheader()
            return

        try:
            with path.open("r", newline="", encoding="utf-8") as fp:
                reader = csv.reader(fp)
                existing = next(reader, [])
        except Exception:
            existing = []

        if existing == fieldnames:
            return

        bak = path.with_suffix(path.suffix + ".bak")
        try:
            if not bak.exists():
                path.replace(bak)
            else:
                path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            # If backup fails, keep appending with runtime fieldnames.
            return

        with bak.open("r", newline="", encoding="utf-8") as src, path.open(
            "w", newline="", encoding="utf-8"
        ) as dst:
            reader = csv.DictReader(src)
            writer = csv.DictWriter(dst, fieldnames=fieldnames)
            writer.writeheader()
            for row in reader:
                writer.writerow(row)

    def info(self, message: str) -> None:
        self._logger.info(message)
        self._flush()

    def error(self, message: str) -> None:
        self._logger.error(message)
        self._flush()

    def _flush(self) -> None:
        for h in list(self._logger.handlers):
            try:
                h.flush()
            except Exception:
                continue

    def log_trade(self, row: Dict[str, object]) -> None:
        payload = {"ts": datetime.now().isoformat(timespec="seconds"), **row}
        with self.trade_csv.open("a", newline="", encoding="utf-8") as fp:
            writer = csv.DictWriter(fp, fieldnames=self._trade_fields, extrasaction="ignore")
            writer.writerow(payload)
        self._flush()

    def log_signal(self, row: Dict[str, object]) -> None:
        payload = {"ts": datetime.now().isoformat(timespec="seconds"), **row}
        with self.signal_csv.open("a", newline="", encoding="utf-8") as fp:
            writer = csv.DictWriter(fp, fieldnames=self._signal_fields, extrasaction="ignore")
            writer.writerow(payload)
        self._flush()
