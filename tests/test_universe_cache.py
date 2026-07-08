import json
import tempfile
from pathlib import Path

from core.universe_cache import CACHE_VERSION, CachedSymbol, UniverseCache, load_cache, save_cache


def test_old_cache_without_value_ma5_is_invalidated():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "universe_cache_20260706.json"
        payload = {
            "cache_version": 1,
            "date_kst": "20260706",
            "source": "naver",
            "top_ratio": 0.1,
            "breakout_k": 0.7,
            "created_at_iso": "2026-07-06T09:00:00+09:00",
            "symbols": {
                "005930": {
                    "avg_volume_5d": 1000,
                    "prev_high": 70000,
                    "prev_low": 68000,
                }
            },
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert load_cache(path) is None


def test_v2_cache_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "universe_cache_20260706.json"
        sym = CachedSymbol(
            avg_volume_5d=1000,
            prev_high=70000,
            prev_low=68000,
            value_ma5=50_000_000_000,
            prev_close=69000,
        )
        cache = UniverseCache(
            date_kst="20260706",
            source="naver",
            top_ratio=0.1,
            breakout_k=0.7,
            created_at_iso="2026-07-06T09:00:00+09:00",
            symbols={"005930": sym},
            cache_version=CACHE_VERSION,
        )
        save_cache(path, cache)
        loaded = load_cache(path)
        assert loaded is not None
        assert loaded.symbols["005930"].value_ma5 == 50_000_000_000
        assert loaded.symbols["005930"].prev_close == 69000
