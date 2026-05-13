"""Universe selection helpers for engine runs."""

from __future__ import annotations

import json
import os
import time

from alphaclaude.paths import DATA_DIR


def generate_universe(
    min_daily_volume: int = 5_000_000,
    exclude_st: bool = True,
    cache_path: str | None = None,
) -> list[str]:
    """Generate a filtered A-share main board universe."""
    if cache_path is None:
        cache_path = str(DATA_DIR / "cache" / "universe_cache.json")
    if os.path.exists(cache_path):
        try:
            mtime = os.path.getmtime(cache_path)
            if time.time() - mtime < 7 * 86400:
                with open(cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
    try:
        import akshare as ak

        df = ak.stock_info_a_code_name()
        codes = []
        for _, row in df.iterrows():
            code = str(row["code"]).zfill(6)
            name = str(row.get("name", ""))
            if not code.startswith(("00", "60")):
                continue
            if exclude_st and ("ST" in name or "*ST" in name):
                continue
            codes.append(code)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(codes, f)
        print(f"[Universe] Generated {len(codes)} main board stocks (cached)")
        return codes
    except Exception as e:
        print(f"[Universe] akshare failed: {e}, using fallback")
        return fallback_universe()


def fallback_universe() -> list[str]:
    """Fallback universe: major liquid stocks across sectors."""
    return [
        "000001", "002142", "600000", "600015", "600016", "600036", "601009",
        "601166", "601288", "601318", "601328", "601398", "601939", "601988",
        "000568", "000858", "002304", "600519", "600809", "600887", "603288",
        "000538", "002001", "300015", "300347", "300529", "300760", "600276",
        "603259",
        "000063", "000725", "002049", "002230", "002371", "002415", "300059",
        "300124", "600703", "603501",
        "002074", "002129", "300014", "300274", "300450", "300750", "600438",
        "601012", "601615",
        "000630", "002155", "600585", "600900", "601088", "601600", "601668",
        "601857", "601899",
        "000547", "000768", "002013", "600150", "600391", "600760", "600893",
        "001979", "600028", "600050", "600941", "601088", "601390", "601668",
        "601728", "601766", "601800", "601857",
    ]
