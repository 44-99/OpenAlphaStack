"""Portfolio management: add/remove/list stocks + P&L overview."""
import argparse
import json
import os
from alphaclaude.paths import PROJECT_ROOT
import sys
from datetime import datetime

DATA_FILE = os.path.join(str(PROJECT_ROOT),
                         "data", "state", "watchlist.json")


def _load() -> dict:
    """Load watchlist from disk."""
    if not os.path.exists(DATA_FILE):
        return {"stocks": {}, "updated_at": ""}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"stocks": {}, "updated_at": ""}


def _save(data: dict) -> None:
    """Save watchlist to disk."""
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def _get_quote(code: str) -> dict:
    """Get current price for a stock via Tencent Finance API."""
    import requests
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    try:
        resp = requests.get(
            f"http://qt.gtimg.cn/q={prefix}{code}",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.encoding = "gbk"
        line = resp.text.strip()
        if '="' not in line:
            return {}
        fields = line.split('="', 1)[1].strip().strip('"').split("~")
        if len(fields) < 50:
            return {}
        return {
            "name": fields[1],
            "price": float(fields[3]),
            "change_pct": float(fields[32]) if fields[32] else 0,
        }
    except Exception:
        return {}


def cmd_add(code: str, entry_price: float, shares: int, tags: str = "") -> dict:
    """Add a stock to watchlist."""
    data = _load()
    code = code.zfill(6)

    if code in data["stocks"]:
        return {"error": f"Stock already in watchlist: {code}", "code": code}

    quote = _get_quote(code)
    name = quote.get("name", code)

    data["stocks"][code] = {
        "name": name,
        "entry_price": entry_price,
        "shares": shares,
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save(data)
    return {"action": "added", "code": code, "name": name,
            "entry_price": entry_price, "shares": shares}


def cmd_remove(code: str) -> dict:
    """Remove a stock from watchlist."""
    data = _load()
    code = code.zfill(6)
    if code not in data["stocks"]:
        return {"error": f"Stock not in watchlist: {code}", "code": code}

    removed = data["stocks"].pop(code)
    _save(data)
    return {"action": "removed", "code": code, "name": removed["name"]}


def cmd_list(tag: str = "") -> dict:
    """List all watchlist stocks, optionally filtered by tag."""
    data = _load()
    stocks = data["stocks"]
    if tag:
        stocks = {c: s for c, s in stocks.items()
                  if tag in s.get("tags", [])}

    items = []
    for code, info in stocks.items():
        items.append({
            "code": code,
            "name": info["name"],
            "entry_price": info["entry_price"],
            "shares": info["shares"],
            "tags": info.get("tags", []),
            "added_at": info.get("added_at", ""),
        })

    return {"count": len(items), "stocks": items,
            "updated_at": data.get("updated_at", "")}


def cmd_overview() -> dict:
    """Portfolio overview with live prices and P&L."""
    data = _load()
    if not data["stocks"]:
        return {"count": 0, "positions": [],
                "total_cost": 0, "total_value": 0, "total_pnl": 0}

    positions = []
    total_cost = 0.0
    total_value = 0.0

    # Batch fetch all quotes via Tencent
    codes = list(data["stocks"].keys())
    quotes = {}
    if codes:
        tencent_codes = []
        for c in codes:
            prefix = "sh" if c.startswith(("6", "9")) else "sz"
            tencent_codes.append(f"{prefix}{c}")
        try:
            import requests
            resp = requests.get(
                f"http://qt.gtimg.cn/q={','.join(tencent_codes)}",
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.encoding = "gbk"
            for line in resp.text.strip().split("\n"):
                if '="' not in line:
                    continue
                try:
                    data_str = line.split('="', 1)[1].strip().strip('"')
                    fields = data_str.split("~")
                    if len(fields) < 50:
                        continue
                    raw_code = fields[2]
                    quotes[raw_code] = {
                        "name": fields[1],
                        "price": float(fields[3]),
                        "change_pct": float(fields[32]) if fields[32] else 0,
                    }
                except (IndexError, ValueError):
                    continue
        except Exception:
            pass

    for code, info in data["stocks"].items():
        entry = info["entry_price"]
        shares = info["shares"]
        cost = entry * shares
        total_cost += cost

        q = quotes.get(code, {})
        current_price = q.get("price", 0)
        market_value = current_price * shares
        pnl = market_value - cost
        pnl_pct = round((current_price - entry) / entry * 100, 2) if entry else 0
        total_value += market_value

        positions.append({
            "code": code,
            "name": q.get("name", info["name"]),
            "shares": shares,
            "entry_price": entry,
            "current_price": current_price,
            "change_pct": q.get("change_pct", 0),
            "cost": round(cost, 2),
            "market_value": round(market_value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": pnl_pct,
        })

    total_pnl = total_value - total_cost

    return {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "count": len(positions),
        "positions": positions,
        "total_cost": round(total_cost, 2),
        "total_value": round(total_value, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl / total_cost * 100, 2) if total_cost else 0,
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Portfolio and watchlist management")
    sub = parser.add_subparsers(dest="action", help="Action")

    sp_add = sub.add_parser("add", help="Add stock to watchlist")
    sp_add.add_argument("code", help="Stock code (6 digits)")
    sp_add.add_argument("--price", "-p", type=float, required=True, help="Entry price")
    sp_add.add_argument("--shares", "-s", type=int, required=True, help="Number of shares")
    sp_add.add_argument("--tags", "-t", default="", help="Comma-separated tags")

    sp_rm = sub.add_parser("remove", help="Remove from watchlist")
    sp_rm.add_argument("code", help="Stock code")

    sp_list = sub.add_parser("list", help="List watchlist")
    sp_list.add_argument("--tag", "-t", default="", help="Filter by tag")

    sub.add_parser("overview", help="Portfolio overview with live P&L")

    args = parser.parse_args()

    if args.action == "add":
        result = cmd_add(args.code, args.price, args.shares, args.tags)
    elif args.action == "remove":
        result = cmd_remove(args.code)
    elif args.action == "overview":
        result = cmd_overview()
    elif args.action == "list" or not args.action:
        result = cmd_list(args.tag)
    else:
        result = {"error": f"Unknown action: {args.action}"}

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
