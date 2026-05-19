from datetime import datetime
import math
import os
import threading
import time

from flask import Flask, render_template_string, request

try:
    from curl_cffi import requests as http_requests

    HTTP_CLIENT = "curl_cffi"
except ImportError:
    http_requests = None
    HTTP_CLIENT = "missing curl_cffi"

try:
    from pnsea import NSE

    PNSEA_AVAILABLE = True
except ImportError:
    NSE = None
    PNSEA_AVAILABLE = False


app = Flask(__name__)

REFRESH_INTERVAL_SECONDS = 300
REQUEST_TIMEOUT_SECONDS = 10

URL_OC = "https://www.nseindia.com/option-chain"
URL_BNF = "https://www.nseindia.com/api/option-chain-indices?symbol=BANKNIFTY"
URL_NF = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_SYMBOLS = {
    "NIFTY": "%5ENSEI",
    "BANKNIFTY": "%5ENSEBANK",
}
VALID_TIMEFRAMES = ("1m", "3m", "5m")
DEFAULT_TIMEFRAME = "5m"
TIMEFRAME_REFRESH_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
}

API_HEADERS = {
    **BASE_HEADERS,
    "Accept": "application/json, text/plain, */*",
    "Referer": URL_OC,
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "X-Requested-With": "XMLHttpRequest",
}

PAGE_HEADERS = {
    **BASE_HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

if http_requests is not None:
    session = http_requests.Session(impersonate="chrome")
else:
    session = None

nse_client = NSE() if PNSEA_AVAILABLE else None
REQUEST_EXCEPTION = getattr(http_requests, "RequestException", Exception)
data_lock = threading.Lock()
refresh_thread_started = False


def empty_data(status="Loading data from NSE...", error=None):
    return {
        "status": status,
        "error": error,
        "last_updated": None,
        "nifty": empty_symbol_data("Nifty"),
        "bank_nifty": empty_symbol_data("Bank Nifty"),
    }


def empty_symbol_data(name):
    return {
        "name": name,
        "underlying": None,
        "nearest": None,
        "expiry": None,
        "oi_data": [],
        "support": None,
        "resistance": None,
        "signals": [],
        "signal": empty_signal(),
    }

def empty_signal():
    return {
        "bias": "Waiting",
        "score": 50,
        "confidence": 0,
        "summary": "Waiting for market data.",
        "reasons": [],
        "pcr": None,
        "change_pcr": None,
        "call_volume": 0,
        "put_volume": 0,
        "invalid_below": None,
        "invalid_above": None,
        "target_zone": None,
        "trade_action": "Wait / No Trade",
        "option_type": None,
        "strike_price": None,
        "entry_zone": None,
        "price_action": empty_price_action(),
        "trade_note": "Decision-support only. Not financial advice.",
    }


def empty_price_action():
    return {
        "score": 0,
        "trend": "Waiting",
        "ltp": None,
        "ema_fast": None,
        "ema_slow": None,
        "vwap": None,
        "previous_high": None,
        "previous_low": None,
        "day_high": None,
        "day_low": None,
        "range_position": None,
        "timeframe": DEFAULT_TIMEFRAME,
        "reasons": [],
    }


data_dict = empty_data()


def safe_float(value, default=0.0):
    if value is None:
        return default

    try:
        if isinstance(value, float) and math.isnan(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    return int(safe_float(value, default))


def clamp(value, lower, upper):
    return max(lower, min(upper, value))


def ratio(numerator, denominator):
    denominator = safe_float(denominator)
    if denominator == 0:
        return None

    return safe_float(numerator) / denominator


def average(values):
    cleaned = [safe_float(value) for value in values if value is not None]
    if not cleaned:
        return None

    return sum(cleaned) / len(cleaned)


def ema(values, period):
    cleaned = [safe_float(value) for value in values if value is not None]
    if len(cleaned) < period:
        return None

    multiplier = 2 / (period + 1)
    current = sum(cleaned[:period]) / period
    for value in cleaned[period:]:
        current = (value - current) * multiplier + current

    return current


def ema_series(values, period):
    cleaned = [safe_float(value) for value in values]
    if len(cleaned) < period:
        return [None for _ in cleaned]

    multiplier = 2 / (period + 1)
    series = [None for _ in cleaned]
    current = sum(cleaned[:period]) / period
    series[period - 1] = current

    for index in range(period, len(cleaned)):
        current = (cleaned[index] - current) * multiplier + current
        series[index] = current

    return series


def round_nearest(value, step):
    return int(math.ceil(float(value) / step) * step)


def option_chain_page_url(symbol=None):
    if symbol:
        return f"{URL_OC}?symbol={symbol}"

    return URL_OC


def symbol_from_url(url):
    if "symbol=BANKNIFTY" in url:
        return "BANKNIFTY"
    if "symbol=NIFTY" in url:
        return "NIFTY"

    return None


def set_cookie(symbol=None):
    if session is None:
        raise RuntimeError(
            "curl_cffi is required for NSE access. Install it with: "
            "pip install -r requirements.txt"
        )

    page_url = option_chain_page_url(symbol)
    page_headers = {**PAGE_HEADERS, "Sec-Fetch-Site": "none"}
    page_response = session.get(
        page_url, headers=page_headers, timeout=REQUEST_TIMEOUT_SECONDS
    )
    page_response.raise_for_status()


def request_json(url, referer=URL_OC):
    headers = {**API_HEADERS, "Referer": referer}
    response = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()

    if not response.content:
        return None

    return response.json()


def fetch_json(url):
    last_payload = None
    last_error = None
    symbol = symbol_from_url(url)
    referer = option_chain_page_url(symbol)

    for attempt in range(3):
        if attempt:
            session.cookies.clear()
            time.sleep(1)

        try:
            set_cookie(symbol)
            payload = request_json(url, referer)
        except (REQUEST_EXCEPTION, ValueError) as exc:
            last_error = exc
            continue

        last_payload = payload

        if isinstance(payload, dict) and payload:
            return payload
        if isinstance(payload, list) and payload:
            return payload

    if last_error is not None and last_payload is None:
        raise ValueError(f"NSE request failed for {url}: {last_error}")

    if isinstance(last_payload, dict):
        keys = ", ".join(sorted(last_payload.keys())) or "none"
        raise ValueError(
            f"NSE returned an empty JSON object for {url}. Top-level keys: {keys}. "
            "This usually means NSE blocked the API request or did not issue a valid session cookie."
        )

    raise ValueError(f"NSE returned an empty response for {url}")


def fetch_yahoo_chart(symbol, range_value, interval):
    if session is None:
        raise RuntimeError(
            "curl_cffi is required for price-action data. Install it with: "
            "pip install -r requirements.txt"
        )

    url = YAHOO_CHART_URL.format(symbol=symbol)
    response = session.get(
        url,
        params={"range": range_value, "interval": interval},
        headers=BASE_HEADERS,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("chart", {}).get("result") or []
    if not result:
        error = payload.get("chart", {}).get("error")
        raise ValueError(f"Yahoo chart returned no data for {symbol}: {error}")

    return result[0]


def candle_rows(chart_result):
    timestamps = chart_result.get("timestamp") or []
    quote = (chart_result.get("indicators", {}).get("quote") or [{}])[0]
    rows = []

    for index, timestamp in enumerate(timestamps):
        close = quote.get("close", [None] * len(timestamps))[index]
        high = quote.get("high", [None] * len(timestamps))[index]
        low = quote.get("low", [None] * len(timestamps))[index]
        volume = quote.get("volume", [None] * len(timestamps))[index]

        if close is None or high is None or low is None:
            continue

        rows.append(
            {
                "timestamp": timestamp,
                "close": safe_float(close),
                "high": safe_float(high),
                "low": safe_float(low),
                "volume": safe_float(volume),
            }
        )

    return rows


def aggregate_candles(rows, minutes):
    if minutes <= 1:
        return rows

    buckets = {}
    bucket_size = minutes * 60

    for row in rows:
        bucket = row["timestamp"] - (row["timestamp"] % bucket_size)
        if bucket not in buckets:
            buckets[bucket] = {
                "timestamp": bucket,
                "open": row["close"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
            }
        else:
            buckets[bucket]["high"] = max(buckets[bucket]["high"], row["high"])
            buckets[bucket]["low"] = min(buckets[bucket]["low"], row["low"])
            buckets[bucket]["close"] = row["close"]
            buckets[bucket]["volume"] += row["volume"]

    return [buckets[key] for key in sorted(buckets)]


def yahoo_intraday_config(timeframe):
    if timeframe == "1m":
        return "1d", "1m", 1
    if timeframe == "3m":
        return "1d", "1m", 3

    return "5d", "5m", 1


def build_price_action(symbol_key, fallback_ltp, timeframe=DEFAULT_TIMEFRAME):
    if timeframe not in VALID_TIMEFRAMES:
        timeframe = DEFAULT_TIMEFRAME

    yahoo_symbol = YAHOO_SYMBOLS[symbol_key]
    range_value, interval, aggregate_minutes = yahoo_intraday_config(timeframe)
    intraday = candle_rows(fetch_yahoo_chart(yahoo_symbol, range_value, interval))
    intraday = aggregate_candles(intraday, aggregate_minutes)
    daily = candle_rows(fetch_yahoo_chart(yahoo_symbol, "1mo", "1d"))

    if len(intraday) < 20 or len(daily) < 2:
        raise ValueError(f"Not enough price-action candles for {symbol_key}")

    closes = [row["close"] for row in intraday]
    volumes = [row["volume"] for row in intraday]
    highs = [row["high"] for row in intraday]
    lows = [row["low"] for row in intraday]

    ltp = closes[-1] or fallback_ltp
    ema_fast = ema(closes, 9)
    ema_slow = ema(closes, 21)
    total_volume = sum(volumes)
    vwap = (
        sum(row["close"] * row["volume"] for row in intraday) / total_volume
        if total_volume
        else average(closes)
    )

    previous_day = daily[-2]
    previous_high = previous_day["high"]
    previous_low = previous_day["low"]
    recent_window = intraday[-26:] if len(intraday) >= 26 else intraday
    day_high = max(highs[-26:])
    day_low = min(lows[-26:])
    range_size = day_high - day_low
    range_position = ((ltp - day_low) / range_size) if range_size else 0.5

    score = 0
    reasons = []

    if ema_fast is not None and ema_slow is not None:
        if ltp > ema_fast > ema_slow:
            score += 22
            reasons.append("Price is above 9/21 EMA alignment.")
        elif ltp < ema_fast < ema_slow:
            score -= 22
            reasons.append("Price is below 9/21 EMA alignment.")
        elif ema_fast > ema_slow:
            score += 8
            reasons.append("Fast EMA is above slow EMA, but price confirmation is incomplete.")
        elif ema_fast < ema_slow:
            score -= 8
            reasons.append("Fast EMA is below slow EMA, but price confirmation is incomplete.")

    if vwap is not None:
        if ltp > vwap:
            score += 14
            reasons.append("Price is holding above intraday VWAP.")
        elif ltp < vwap:
            score -= 14
            reasons.append("Price is trading below intraday VWAP.")

    if ltp > previous_high:
        score += 18
        reasons.append("Price is above the previous daily high.")
    elif ltp < previous_low:
        score -= 18
        reasons.append("Price is below the previous daily low.")
    else:
        reasons.append("Price is inside the previous daily range.")

    if range_position >= 0.7:
        score += 8
        reasons.append("Price is near the upper part of the recent intraday range.")
    elif range_position <= 0.3:
        score -= 8
        reasons.append("Price is near the lower part of the recent intraday range.")

    if score >= 24:
        trend = "Bullish"
    elif score <= -24:
        trend = "Bearish"
    else:
        trend = "Sideways"

    return {
        "score": round(clamp(score, -50, 50)),
        "trend": trend,
        "ltp": round(ltp, 2),
        "ema_fast": round(ema_fast, 2) if ema_fast is not None else None,
        "ema_slow": round(ema_slow, 2) if ema_slow is not None else None,
        "vwap": round(vwap, 2) if vwap is not None else None,
        "previous_high": round(previous_high, 2),
        "previous_low": round(previous_low, 2),
        "day_high": round(day_high, 2),
        "day_low": round(day_low, 2),
        "range_position": round(range_position * 100),
        "timeframe": timeframe,
        "reasons": reasons[:5],
        "candles": intraday,
    }


def get_option_rows(chain_data):
    records = chain_data.get("records") or {}
    filtered = chain_data.get("filtered") or {}
    rows = records.get("data") or filtered.get("data") or []

    if not isinstance(rows, list):
        return []

    return rows


def get_underlying_value(chain_data, rows, name):
    records = chain_data.get("records") or {}
    underlying = records.get("underlyingValue")

    if underlying is not None:
        return underlying

    for row in rows:
        for side in ("CE", "PE"):
            side_data = row.get(side) or {}
            underlying = side_data.get("underlyingValue")
            if underlying is not None:
                return underlying

    raise ValueError(f"{name}: NSE response did not include an underlying value")


def get_nearest_expiry(chain_data, rows, name):
    records = chain_data.get("records") or {}
    expiry_dates = records.get("expiryDates") or []

    if expiry_dates:
        return expiry_dates[0]

    for row in rows:
        expiry_date = row.get("expiryDate")
        if expiry_date:
            return expiry_date

    error_message = chain_data.get("message") or chain_data.get("error")
    if error_message:
        raise ValueError(f"{name}: NSE returned an error payload: {error_message}")

    keys = ", ".join(sorted(chain_data.keys())) or "none"
    raise ValueError(
        f"{name}: NSE response did not include expiry dates or option rows. "
        f"Top-level keys: {keys}"
    )


def choose_trade_contract(oi_data, underlying, bias):
    if bias not in ("Bullish", "Bearish") or not oi_data:
        return {
            "trade_action": "Wait / No Trade",
            "option_type": None,
            "strike_price": None,
            "entry_zone": None,
            "contract_reason": "No option selected because the model is not directional enough.",
        }

    option_type = "CALL" if bias == "Bullish" else "PUT"
    prefix = "CE" if option_type == "CALL" else "PE"
    candidates = []
    min_premium = max(5, underlying * 0.001)
    max_distance = underlying * 0.012

    for row in oi_data:
        strike = row["strikePrice"]
        if bias == "Bullish" and strike < underlying:
            continue
        if bias == "Bearish" and strike > underlying:
            continue

        oi = row[f"{prefix}_OI"]
        volume = row[f"{prefix}_volume"]
        ltp = row[f"{prefix}_LTP"]
        distance = abs(strike - underlying)
        if oi <= 0 or volume <= 0 or ltp < min_premium or distance > max_distance:
            continue

        liquidity_score = math.log10(max(oi, 1)) + math.log10(max(volume, 1))
        premium_score = min(ltp / max(min_premium, 1), 6)
        score = liquidity_score + premium_score - (distance / max_distance) * 2
        candidates.append((score, row))

    if not candidates:
        return {
            "trade_action": "Wait / No Trade",
            "option_type": None,
            "strike_price": None,
            "entry_zone": None,
            "contract_reason": "No nearby liquid option contract with meaningful premium passed the filter.",
        }

    selected = max(candidates, key=lambda item: item[0])[1]
    ltp = selected[f"{prefix}_LTP"]
    lower_entry = round(ltp * 0.97, 2)
    upper_entry = round(ltp * 1.03, 2)

    return {
        "trade_action": f"Buy {option_type}",
        "option_type": option_type,
        "strike_price": selected["strikePrice"],
        "entry_zone": f"{lower_entry} - {upper_entry}",
        "contract_reason": (
            f"Selected nearest liquid {option_type.lower()} with OI "
            f"{selected[f'{prefix}_OI']} and volume {selected[f'{prefix}_volume']}."
        ),
    }


def build_market_signal(oi_data, underlying, support, resistance, price_action=None):
    if not oi_data:
        return empty_signal()

    price_action = price_action or empty_price_action()

    total_ce_oi = sum(row["CE_OI"] for row in oi_data)
    total_pe_oi = sum(row["PE_OI"] for row in oi_data)
    total_ce_change = sum(row["CE_change_OI"] for row in oi_data)
    total_pe_change = sum(row["PE_change_OI"] for row in oi_data)
    total_call_volume = sum(row["CE_volume"] for row in oi_data)
    total_put_volume = sum(row["PE_volume"] for row in oi_data)

    ce_iv_values = [row["CE_IV"] for row in oi_data if row["CE_IV"] > 0]
    pe_iv_values = [row["PE_IV"] for row in oi_data if row["PE_IV"] > 0]
    avg_ce_iv = sum(ce_iv_values) / len(ce_iv_values) if ce_iv_values else 0
    avg_pe_iv = sum(pe_iv_values) / len(pe_iv_values) if pe_iv_values else 0

    oi_total = total_ce_oi + total_pe_oi
    static_pressure = (total_pe_oi - total_ce_oi) / oi_total if oi_total else 0

    change_total = abs(total_ce_change) + abs(total_pe_change)
    change_pressure = (
        (total_pe_change - total_ce_change) / change_total if change_total else 0
    )

    volume_total = total_call_volume + total_put_volume
    volume_pressure = (
        (total_put_volume - total_call_volume) / volume_total if volume_total else 0
    )

    iv_total = avg_ce_iv + avg_pe_iv
    iv_pressure = ((avg_pe_iv - avg_ce_iv) / iv_total) if iv_total else 0

    directional_score = 50
    directional_score += static_pressure * 26
    directional_score += change_pressure * 28
    directional_score += volume_pressure * 12
    directional_score += iv_pressure * 8
    directional_score += price_action["score"] * 0.45
    directional_score = round(clamp(directional_score, 0, 100))

    if directional_score >= 62:
        bias = "Bullish"
    elif directional_score <= 38:
        bias = "Bearish"
    else:
        bias = "Neutral"

    confidence = round(clamp(abs(directional_score - 50) * 1.8 + 35, 35, 92))
    if bias == "Neutral":
        confidence = round(clamp(70 - abs(directional_score - 50) * 2, 35, 70))

    pcr = ratio(total_pe_oi, total_ce_oi)
    change_pcr = ratio(total_pe_change, total_ce_change)

    reasons = []
    if pcr is not None:
        if pcr >= 1.15:
            reasons.append(f"Put OI is heavier than Call OI (PCR {pcr:.2f}).")
        elif pcr <= 0.85:
            reasons.append(f"Call OI is heavier than Put OI (PCR {pcr:.2f}).")
        else:
            reasons.append(f"Total OI is balanced (PCR {pcr:.2f}).")

    if total_pe_change > total_ce_change:
        reasons.append("Fresh Put OI buildup is stronger than Call OI buildup.")
    elif total_ce_change > total_pe_change:
        reasons.append("Fresh Call OI buildup is stronger than Put OI buildup.")
    else:
        reasons.append("Change in OI is balanced.")

    if total_put_volume > total_call_volume:
        reasons.append("Put-side traded volume is leading.")
    elif total_call_volume > total_put_volume:
        reasons.append("Call-side traded volume is leading.")

    if avg_pe_iv and avg_ce_iv:
        if avg_pe_iv > avg_ce_iv * 1.05:
            reasons.append("Put IV is elevated versus Call IV, showing downside hedging demand.")
        elif avg_ce_iv > avg_pe_iv * 1.05:
            reasons.append("Call IV is elevated versus Put IV, showing upside option demand.")

    if price_action["trend"] == bias:
        reasons.append(f"Price action confirms the {bias.lower()} option-chain bias.")
        confidence += 8
    elif price_action["trend"] in ("Bullish", "Bearish") and bias in ("Bullish", "Bearish"):
        reasons.append(
            f"Price action is {price_action['trend'].lower()}, which conflicts with option-chain pressure."
        )
        confidence -= 12
    elif price_action["trend"] == "Sideways":
        reasons.append("Price action is sideways, so confidence is capped.")
        confidence -= 6

    target_zone = None
    invalid_below = None
    invalid_above = None

    if bias == "Bullish":
        invalid_below = support
        target_zone = resistance
        summary = "Upside bias while price holds above the main put-OI support zone."
    elif bias == "Bearish":
        invalid_above = resistance
        target_zone = support
        summary = "Downside bias while price remains below the main call-OI resistance zone."
    else:
        invalid_below = support
        invalid_above = resistance
        target_zone = f"{support} - {resistance}" if support and resistance else None
        summary = "No-trade or range bias because the evidence is mixed."

    confidence = round(clamp(confidence, 35, 92))
    trade = choose_trade_contract(oi_data, underlying, bias)
    if confidence < 55:
        trade = {
            "trade_action": "Wait / No Trade",
            "option_type": None,
            "strike_price": None,
            "entry_zone": None,
            "contract_reason": "Confidence is below the minimum trade threshold.",
        }
    reasons.append(trade["contract_reason"])

    return {
        "bias": bias,
        "score": directional_score,
        "confidence": confidence,
        "summary": summary,
        "reasons": reasons[:5],
        "pcr": round(pcr, 2) if pcr is not None else None,
        "change_pcr": round(change_pcr, 2) if change_pcr is not None else None,
        "call_volume": total_call_volume,
        "put_volume": total_put_volume,
        "invalid_below": invalid_below,
        "invalid_above": invalid_above,
        "target_zone": target_zone,
        "trade_action": trade["trade_action"],
        "option_type": trade["option_type"],
        "strike_price": trade["strike_price"],
        "entry_zone": trade["entry_zone"],
        "price_action": price_action,
        "trade_note": "Use with price action, risk limits, and confirmation. Not financial advice.",
    }


def analyze_option_chain(name, chain_data, step, price_action=None, strike_count=10):
    items = get_option_rows(chain_data)
    expiry = get_nearest_expiry(chain_data, items, name)
    underlying = get_underlying_value(chain_data, items, name)

    nearest = round_nearest(underlying, step)
    start_strike = nearest - (step * strike_count)
    end_strike = nearest + (step * strike_count)
    wanted_strikes = set(range(start_strike, end_strike + step, step))

    oi_data = []
    ce_max_oi = -1
    pe_max_oi = -1
    resistance = None
    support = None
    signals = []

    for item in items:
        strike_price = item.get("strikePrice")
        if item.get("expiryDate") != expiry or strike_price not in wanted_strikes:
            continue

        ce = item.get("CE") or {}
        pe = item.get("PE") or {}
        ce_oi = safe_int(ce.get("openInterest"))
        pe_oi = safe_int(pe.get("openInterest"))
        ce_change_oi = safe_int(ce.get("changeinOpenInterest"))
        pe_change_oi = safe_int(pe.get("changeinOpenInterest"))
        ce_volume = safe_int(ce.get("totalTradedVolume"))
        pe_volume = safe_int(pe.get("totalTradedVolume"))
        ce_iv = safe_float(ce.get("impliedVolatility"))
        pe_iv = safe_float(pe.get("impliedVolatility"))
        ce_ltp = safe_float(ce.get("lastPrice"))
        pe_ltp = safe_float(pe.get("lastPrice"))

        oi_data.append(
            {
                "expiryDate": expiry,
                "strikePrice": strike_price,
                "CE_OI": ce_oi,
                "PE_OI": pe_oi,
                "CE_change_OI": ce_change_oi,
                "PE_change_OI": pe_change_oi,
                "CE_volume": ce_volume,
                "PE_volume": pe_volume,
                "CE_IV": ce_iv,
                "PE_IV": pe_iv,
                "CE_LTP": ce_ltp,
                "PE_LTP": pe_ltp,
            }
        )

        if ce_oi > ce_max_oi:
            ce_max_oi = ce_oi
            resistance = strike_price
        if pe_oi > pe_max_oi:
            pe_max_oi = pe_oi
            support = strike_price

        if ce_oi > 1.5 * pe_oi and ce_oi > 0:
            signals.append((strike_price, "Call OI Dominant"))
        elif pe_oi > 1.5 * ce_oi and pe_oi > 0:
            signals.append((strike_price, "Put OI Dominant"))

    oi_data.sort(key=lambda row: row["strikePrice"])
    signals.sort(key=lambda signal: signal[0])
    signal = build_market_signal(oi_data, underlying, support, resistance, price_action)

    return {
        "name": name,
        "underlying": underlying,
        "nearest": nearest,
        "expiry": expiry,
        "oi_data": oi_data,
        "support": support,
        "resistance": resistance,
        "signals": signals,
        "signal": signal,
    }


def build_chain_from_pnsea(symbol):
    if nse_client is None:
        raise RuntimeError(
            "pnsea is required for the working NSE data path. Install it with: "
            "pip install -r requirements.txt"
        )

    frame, expiries, underlying = nse_client.options.option_chain(symbol)
    if frame.empty:
        raise ValueError(f"PNSEA returned no option-chain rows for {symbol}")
    if not expiries:
        raise ValueError(f"PNSEA returned no expiry dates for {symbol}")

    rows = []
    expiry = expiries[0]

    for item in frame.to_dict("records"):
        rows.append(
            {
                "expiryDate": expiry,
                "strikePrice": item.get("strikePrice"),
                "CE": {
                    "openInterest": item.get("CE_openInterest") or 0,
                    "changeinOpenInterest": item.get("CE_changeinOpenInterest") or 0,
                    "totalTradedVolume": item.get("CE_totalTradedVolume") or 0,
                    "impliedVolatility": item.get("CE_impliedVolatility") or 0,
                    "lastPrice": item.get("CE_lastPrice") or 0,
                },
                "PE": {
                    "openInterest": item.get("PE_openInterest") or 0,
                    "changeinOpenInterest": item.get("PE_changeinOpenInterest") or 0,
                    "totalTradedVolume": item.get("PE_totalTradedVolume") or 0,
                    "impliedVolatility": item.get("PE_impliedVolatility") or 0,
                    "lastPrice": item.get("PE_lastPrice") or 0,
                },
            }
        )

    return {
        "records": {
            "expiryDates": expiries,
            "underlyingValue": underlying,
            "data": rows,
        }
    }


def load_market_data(timeframe=DEFAULT_TIMEFRAME):
    if timeframe not in VALID_TIMEFRAMES:
        timeframe = DEFAULT_TIMEFRAME

    nifty_chain = build_chain_from_pnsea("NIFTY")
    bank_nifty_chain = build_chain_from_pnsea("BANKNIFTY")
    nifty_price_action = build_price_action(
        "NIFTY",
        get_underlying_value(nifty_chain, get_option_rows(nifty_chain), "Nifty"),
        timeframe,
    )
    bank_nifty_price_action = build_price_action(
        "BANKNIFTY",
        get_underlying_value(bank_nifty_chain, get_option_rows(bank_nifty_chain), "Bank Nifty"),
        timeframe,
    )

    return {
        "status": "Live",
        "error": None,
        "timeframe": timeframe,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "nifty": analyze_option_chain(
            "Nifty", nifty_chain, step=50, price_action=nifty_price_action
        ),
        "bank_nifty": analyze_option_chain(
            "Bank Nifty",
            bank_nifty_chain,
            step=100,
            price_action=bank_nifty_price_action,
        ),
    }


@app.route("/diagnostics")
def diagnostics():
    return {
        "http_client": HTTP_CLIENT,
        "pnsea_available": PNSEA_AVAILABLE,
        "primary_data_source": "pnsea.options.option_chain",
        "has_http_session": session is not None,
        "nse_nifty_url": URL_NF,
        "nse_bank_nifty_url": URL_BNF,
    }


def refresh_data():
    global data_dict

    while True:
        try:
            latest_data = load_market_data()
        except (REQUEST_EXCEPTION, RuntimeError, ValueError, TypeError, KeyError) as exc:
            with data_lock:
                data_dict = {
                    **data_dict,
                    "status": "Stale" if data_dict.get("last_updated") else "Unavailable",
                    "error": str(exc),
                }
        else:
            with data_lock:
                data_dict = latest_data

        time.sleep(REFRESH_INTERVAL_SECONDS)


def start_refresh_thread():
    global refresh_thread_started

    if refresh_thread_started:
        return

    refresh_thread_started = True
    thread = threading.Thread(target=refresh_data, daemon=True)
    thread.start()


@app.route("/")
def index():
    selected_timeframe = request.args.get("timeframe", DEFAULT_TIMEFRAME)
    if selected_timeframe not in VALID_TIMEFRAMES:
        selected_timeframe = DEFAULT_TIMEFRAME
    refresh_interval = TIMEFRAME_REFRESH_SECONDS[selected_timeframe]

    try:
        data = load_market_data(selected_timeframe)
    except (REQUEST_EXCEPTION, RuntimeError, ValueError, TypeError, KeyError) as exc:
        with data_lock:
            data = {
                **data_dict,
                "status": "Stale" if data_dict.get("last_updated") else "Unavailable",
                "error": str(exc),
                "timeframe": selected_timeframe,
            }

    return render_template_string(
        """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <title>Option Chain Data</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css" rel="stylesheet">
        <style>
            :root {
                --ink: #172033;
                --muted: #64748b;
                --line: #d9e2ec;
                --panel: #ffffff;
                --page: #f4f7fb;
                --blue: #155e75;
                --green: #177245;
                --red: #b4233a;
                --amber: #9a5b00;
            }
            body {
                font-family: Arial, sans-serif;
                background: var(--page);
                color: var(--ink);
                padding: 0;
            }
            .container {
                max-width: 1320px;
                margin: auto;
                padding: 20px;
            }
            .header {
                background: #172033;
                color: #fff;
                padding: 18px 20px;
            }
            .header h1 {
                margin: 0;
                font-size: 1.9rem;
                font-weight: 700;
            }
            .header p {
                color: #cbd5e1;
                margin: 4px 0 0;
            }
            .status-bar {
                display: flex;
                flex-wrap: wrap;
                justify-content: space-between;
                gap: 12px;
                margin-bottom: 20px;
                color: var(--muted);
                font-size: 0.95rem;
            }
            .dashboard-grid {
                display: grid;
                gap: 18px;
                grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
                margin-bottom: 20px;
            }
            .panel {
                background: var(--panel);
                border: 1px solid var(--line);
                border-radius: 8px;
                padding: 16px;
                box-shadow: 0 6px 18px rgba(15, 23, 42, 0.06);
            }
            .panel-title {
                display: flex;
                align-items: flex-start;
                justify-content: space-between;
                gap: 12px;
                margin-bottom: 12px;
            }
            .panel-title h2 {
                font-size: 1.35rem;
                margin: 0;
                font-weight: 700;
            }
            .bias-badge {
                border-radius: 999px;
                color: #fff;
                display: inline-block;
                font-weight: bold;
                min-width: 92px;
                padding: 6px 10px;
                text-align: center;
            }
            .bias-bullish { background: var(--green); }
            .bias-bearish { background: var(--red); }
            .bias-neutral, .bias-waiting { background: var(--amber); }
            .trade-box {
                border: 2px solid var(--line);
                border-radius: 8px;
                margin: 12px 0 16px;
                padding: 14px;
                background: #f8fafc;
            }
            .trade-action {
                font-size: 1.7rem;
                font-weight: 800;
                margin-bottom: 8px;
            }
            .trade-call { color: var(--green); }
            .trade-put { color: var(--red); }
            .trade-wait { color: var(--amber); }
            .timeframe-form {
                align-items: center;
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
                margin-bottom: 16px;
            }
            .timeframe-form label {
                color: var(--muted);
                font-weight: 700;
                margin: 0;
            }
            .timeframe-form select,
            .timeframe-form button {
                border: 1px solid var(--line);
                border-radius: 6px;
                padding: 8px 10px;
            }
            .timeframe-form button {
                background: var(--blue);
                color: #fff;
                font-weight: 700;
            }
            .score-row {
                display: grid;
                grid-template-columns: 86px 1fr 64px;
                gap: 10px;
                align-items: center;
                margin: 12px 0;
            }
            .score-track {
                background: #e2e8f0;
                border-radius: 999px;
                height: 12px;
                overflow: hidden;
            }
            .score-fill {
                background: linear-gradient(90deg, var(--red), var(--amber), var(--green));
                height: 100%;
            }
            .metric-grid {
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 10px;
                margin: 14px 0;
            }
            .metric-box {
                border: 1px solid var(--line);
                border-radius: 6px;
                padding: 10px;
            }
            .metric-label {
                color: var(--muted);
                font-size: 0.78rem;
                text-transform: uppercase;
            }
            .metric-value {
                font-size: 1.1rem;
                font-weight: 700;
            }
            .reason-list {
                margin: 10px 0 0;
                padding-left: 18px;
            }
            .reason-list li {
                margin-bottom: 5px;
            }
            .note {
                color: #664d03;
                background: #fff3cd;
                border: 1px solid #ffecb5;
                padding: 10px;
                margin: 16px 0 20px;
                border-radius: 6px;
            }
            .error {
                color: #842029;
                background: #f8d7da;
                border: 1px solid #f5c2c7;
                padding: 10px;
                margin-bottom: 20px;
                border-radius: 6px;
            }
            .muted {
                color: var(--muted);
            }
            @media (max-width: 640px) {
                .container { padding: 14px; }
                .score-row { grid-template-columns: 1fr; }
                .metric-grid { grid-template-columns: 1fr; }
            }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Option Chain Data</h1>
            <p>Multi-factor OI dashboard for Nifty and Bank Nifty</p>
        </div>
        <div class="container">
            <div class="status-bar">
                <div>Status: {{ data['status'] }}</div>
                <div>Last updated: {{ data['last_updated'] or 'Waiting for first refresh' }}</div>
                <div>Refreshing in <span id="countdown"></span> seconds</div>
            </div>
            <form class="timeframe-form" method="get">
                <label for="timeframe">Price-action timeframe</label>
                <select id="timeframe" name="timeframe">
                    {% for tf in valid_timeframes %}
                    <option value="{{ tf }}" {% if tf == selected_timeframe %}selected{% endif %}>{{ tf }}</option>
                    {% endfor %}
                </select>
                <button type="submit">Apply</button>
            </form>

            {% if data['error'] %}
            <div class="error">NSE data refresh failed: {{ data['error'] }}</div>
            {% endif %}
            <div class="note">
                This dashboard gives a probability-style market bias, not a guaranteed prediction. Use the invalidation level and wait for price confirmation before taking risk.
            </div>

            <div class="dashboard-grid">
                {% for symbol in [data['nifty'], data['bank_nifty']] %}
                {% set signal = symbol['signal'] %}
                <section class="panel">
                    <div class="panel-title">
                        <h2>{{ symbol['name'] }}</h2>
                        <span class="bias-badge bias-{{ signal['bias']|lower }}">{{ signal['bias'] }}</span>
                    </div>
                    {% set trade_class = 'trade-call' if signal['option_type'] == 'CALL' else 'trade-put' if signal['option_type'] == 'PUT' else 'trade-wait' %}
                    <div class="trade-box">
                        <div class="metric-label">What to do</div>
                        <div class="trade-action {{ trade_class }}">{{ signal['trade_action'] }}</div>
                        <div class="metric-grid">
                            <div class="metric-box">
                                <div class="metric-label">Strike Price</div>
                                <div class="metric-value">{{ signal['strike_price'] or 'N/A' }}</div>
                            </div>
                            <div class="metric-box">
                                <div class="metric-label">Entry Zone</div>
                                <div class="metric-value">{{ signal['entry_zone'] or 'Wait' }}</div>
                            </div>
                        </div>
                    </div>

                    <div class="score-row">
                        <div class="muted">Bias score</div>
                        <div class="score-track">
                            <div class="score-fill" style="width: {{ signal['score'] }}%;"></div>
                        </div>
                        <strong>{{ signal['score'] }}/100</strong>
                    </div>
                    <div class="score-row">
                        <div class="muted">Confidence</div>
                        <div class="score-track">
                            <div class="score-fill" style="width: {{ signal['confidence'] }}%;"></div>
                        </div>
                        <strong>{{ signal['confidence'] }}/100</strong>
                    </div>

                    <p>{{ signal['summary'] }}</p>
                    {% set pa = signal['price_action'] %}

                    <div class="metric-grid">
                        <div class="metric-box">
                            <div class="metric-label">Current Price</div>
                            <div class="metric-value">{{ symbol['underlying'] or 'N/A' }}</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">Price Action</div>
                            <div class="metric-value">{{ pa['trend'] }} / {{ pa['timeframe'] }}</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">Support</div>
                            <div class="metric-value">{{ symbol['support'] or 'N/A' }}</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">Resistance</div>
                            <div class="metric-value">{{ symbol['resistance'] or 'N/A' }}</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">PCR</div>
                            <div class="metric-value">{{ signal['pcr'] or 'N/A' }}</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">Target Zone</div>
                            <div class="metric-value">{{ signal['target_zone'] or 'N/A' }}</div>
                        </div>
                    </div>

                    <div class="metric-grid">
                        <div class="metric-box">
                            <div class="metric-label">VWAP</div>
                            <div class="metric-value">{{ pa['vwap'] or 'N/A' }}</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">EMA 9 / EMA 21</div>
                            <div class="metric-value">{{ pa['ema_fast'] or 'N/A' }} / {{ pa['ema_slow'] or 'N/A' }}</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">Previous Range</div>
                            <div class="metric-value">{{ pa['previous_low'] or 'N/A' }} - {{ pa['previous_high'] or 'N/A' }}</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">Invalidation</div>
                            <div class="metric-value">
                                {% if signal['invalid_below'] %}Below {{ signal['invalid_below'] }}
                                {% elif signal['invalid_above'] %}Above {{ signal['invalid_above'] }}
                                {% else %}N/A{% endif %}
                            </div>
                        </div>
                    </div>

                    <ul class="reason-list">
                        {% for reason in signal['reasons'] %}
                        <li>{{ reason }}</li>
                        {% else %}
                        <li>Waiting for enough option-chain data.</li>
                        {% endfor %}
                    </ul>
                    <ul class="reason-list">
                        {% for reason in pa['reasons'] %}
                        <li>{{ reason }}</li>
                        {% endfor %}
                    </ul>
                    <p class="muted">{{ signal['trade_note'] }}</p>
                </section>
                {% endfor %}
            </div>
        </div>
        <script>
            function updateCountdown(seconds) {
                document.getElementById("countdown").textContent = seconds;
            }

            function startCountdown(seconds) {
                updateCountdown(seconds);
                var interval = setInterval(function() {
                    seconds -= 1;
                    updateCountdown(seconds);
                    if (seconds <= 0) {
                        clearInterval(interval);
                        var url = new URL(window.location.href);
                        url.searchParams.set("timeframe", "{{ selected_timeframe }}");
                        window.location.href = url.toString();
                    }
                }, 1000);
            }

            document.addEventListener("DOMContentLoaded", function() {
                startCountdown({{ refresh_interval }});
            });
        </script>
    </body>
    </html>
    """,
        data=data,
        refresh_interval=refresh_interval,
        selected_timeframe=selected_timeframe,
        valid_timeframes=VALID_TIMEFRAMES,
    )


start_refresh_thread()


if __name__ == "__main__":
    app.run(
        debug=True,
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5000")),
        use_reloader=False,
    )
