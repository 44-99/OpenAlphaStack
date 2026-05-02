"""Shared HTTP session config — bypasses system proxy, adds retry, sets CN-friendly UA."""
import os
import time
import requests

# Bypass Windows system proxy (eastmoney blocks proxy/datacenter IPs)
os.environ.setdefault("no_proxy", "*")
os.environ.setdefault("NO_PROXY", "*")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_RETRIES = 3
_BACKOFF = 1.5  # seconds multiplier


def get_session() -> requests.Session:
    """Create a requests Session with proxy bypass and browser UA."""
    s = requests.Session()
    s.trust_env = False
    s.proxies = {"http": None, "https": None}
    s.headers.update({
        "User-Agent": _UA,
        "Referer": "https://quote.eastmoney.com/",
        "Accept": "*/*",
    })
    return s


def retry_get(session: requests.Session, url: str, params: dict = None,
              timeout: int = 15, retries: int = _RETRIES) -> requests.Response:
    """GET with exponential backoff. Raises last error on exhaustion."""
    last_err = None
    for attempt in range(retries):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            return resp
        except requests.ConnectionError as e:
            last_err = e
            if attempt < retries - 1:
                wait = _BACKOFF ** (attempt + 1)
                time.sleep(wait)
    raise last_err


def friendly_error(code: str, exc: Exception) -> str:
    """Return a user-friendly error message based on exception type."""
    msg = str(exc)
    if "RemoteDisconnected" in msg or "Remote end closed" in msg:
        return (
            f"无法连接东方财富API (股票代码: {code})。可能原因: "
            "1) 当前IP被东方财富屏蔽(云服务器/数据中心IP常见); "
            "2) 请求过于频繁被临时限流。建议使用家庭宽带的国内IP。"
            f" 原始错误: {msg[:100]}"
        )
    if "Timeout" in msg or "timed out" in msg:
        return f"数据请求超时 (股票代码: {code})。网络不稳定或东方财富API响应慢。请稍后重试。"
    if "ProxyError" in msg or "proxy" in msg.lower():
        return f"代理连接失败 (股票代码: {code})。已尝试绕过系统代理，仍失败。请检查网络设置。"
    return f"数据获取失败 (股票代码: {code}): {msg[:200]}"
