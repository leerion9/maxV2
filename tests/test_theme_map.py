# -*- coding: utf-8 -*-
from pathlib import Path

from core.api_client import Quote
from core.theme_map import ThemeInfo, ThemeMapRegistry, ThemeMapStrategy


def _q(symbol: str, price: int, cum_value: int = 10_000_000) -> Quote:
    return Quote(
        symbol=symbol,
        current_price=price,
        open_price=price,
        volume=0,
        cum_value=cum_value,
        prev_high=0,
        prev_low=0,
    )


def _registry_four() -> ThemeMapRegistry:
    themes = {
        "1": ThemeInfo(
            theme_id="1",
            theme_name="테스트테마",
            symbols=["111111", "222222", "333333", "444444"],
            n_members=4,
            eligible=True,
        )
    }
    primary = {s: "1" for s in themes["1"].symbols}
    return ThemeMapRegistry(themes=themes, primary_theme=primary, updated_ymd="20260711")


def test_theme_map_laggard_break_and_stop(tmp_path: Path):
    reg = _registry_four()
    s = ThemeMapStrategy(
        registry=reg,
        hot_ret=0.02,
        hot_ratio=0.50,
        min_members=4,
        min_pace_ratio=0.0,
        entry_start_hhmm="09:10",
        entry_end_hhmm="14:30",
        force_exit_hhmm="14:50",
        stop_pct=0.02,
        trail_pct=0.02,
    )
    # Leaders up a lot; laggard flat then breaks day high.
    s.register("111111", 10000)
    s.register("222222", 10000)
    s.register("333333", 10000)
    s.register("444444", 10000)

    # Push three leaders to +3% so theme is hot (75% >= 2%).
    for sym, px in [("111111", 10300), ("222222", 10300), ("333333", 10300)]:
        s.on_quote(_q(sym, px), now_hhmm="10:00", value_ma5=1)

    # Laggard: establish day high 10050 then break to 10060 while still below median.
    s.on_quote(_q("444444", 10050), now_hhmm="10:00", value_ma5=1)
    entry, exit_ = s.on_quote(_q("444444", 10060), now_hhmm="10:01", value_ma5=1)
    assert exit_ is None
    assert entry is not None
    assert entry.theme_id == "1"
    assert entry.trigger_price == 10050
    s.confirm_entry("444444", entry.entry_price)

    # Stop -2%
    _, exit_ = s.on_quote(_q("444444", 9800), now_hhmm="10:10", value_ma5=1)
    assert exit_ is not None
    assert exit_.reason == "STOP"


def test_theme_map_registry_from_csv(tmp_path: Path):
    p = tmp_path / "theme_map.csv"
    p.write_text(
        "theme_id,theme_name,symbol,name,n_members,eligible,updated_ymd\n"
        "10,여행,039130,하나투어,3,1,20260711\n"
        "10,여행,034230,파라다이스,3,1,20260711\n"
        "10,여행,114090,GKL,3,1,20260711\n"
        "99,대형,005930,삼성전자,20,0,20260711\n"
        "99,대형,000660,SK하이닉스,20,0,20260711\n",
        encoding="utf-8-sig",
    )
    reg = ThemeMapRegistry.from_csv(p, max_members=12, min_members=4)
    # n=3 < min_members → not eligible for watch
    assert reg.eligible_themes() == []
    assert reg.watch_symbols() == []

    reg2 = ThemeMapRegistry.from_csv(p, max_members=12, min_members=3)
    assert len(reg2.eligible_themes()) == 1
    assert set(reg2.watch_symbols()) == {"039130", "034230", "114090"}


def test_theme_ledger_create(tmp_path: Path):
    from config.settings import settings
    from core.pace_collectors import ThemeMapLedger

    path = tmp_path / "paper_ledger_theme_map.csv"
    led = ThemeMapLedger(path=path, settings=settings)
    led.append_entry(
        ymd="20260711",
        theme_id="1",
        theme_name="테스트",
        symbol="039130",
        role="follower",
        entry_ts="2026-07-11T10:00:00",
        entry_price=10000,
        trigger_price=9990,
        qty=10,
        theme_score_at_entry=0.75,
        stock_ret_at_entry=0.01,
        theme_median_ret=0.03,
        pace_ratio_at_entry=2.0,
    )
    ok = led.fill_exit(
        ymd="20260711",
        symbol="039130",
        exit_ts="2026-07-11T14:50:00",
        exit_price=10100,
        exit_reason="TIME",
    )
    assert ok
    raw = path.read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf"
    text = path.read_text(encoding="utf-8-sig")
    assert "theme_id" in text
    assert "진입일" in text or "#" in text
