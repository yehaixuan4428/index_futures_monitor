import os
import threading
from collections import defaultdict
from functools import lru_cache
from typing import Any, Optional

import pandas as pd
import rqdatac
from rqdatac import LiveMarketDataClient
from flask import Flask, jsonify, render_template, request

from data_source import get_contract_expire_date, get_realtime_summary
from multi_factor_system.tools import load_parquet_files
from scipy.stats import percentileofscore


app = Flask(__name__)

UNDERLYING_LABELS = {
    "IF": "沪深300 (IF)",
    "IH": "上证50 (IH)",
    "IC": "中证500 (IC)",
    "IM": "中证1000 (IM)",
}

UNDERLYING_INDEX_IDS = {
    "IF": "000300.XSHG",
    "IH": "000016.XSHG",
    "IC": "000905.XSHG",
    "IM": "000852.XSHG",
}

FUTURE_TYPE_LABELS = {
    "front_month": "近月",
    "next_month": "次近月",
    "second_front_month": "次近月",
    "current_quarter": "季月",
    "front_quarter": "季月",
    "next_quarter": "次季月",
    "second_front_quarter": "次季月",
}

DATA_STORE_DIR = os.path.join(
    os.environ["DATABASE_EXTRA"], "index_futures_database"
)
PRELOAD_UNDERLYINGS = ("IH", "IF", "IC", "IM")
PRELOAD_FUTURE_TYPES = (
    "front_month",
    "next_month",
    "current_quarter",
    "next_quarter",
)

FUTURE_TYPE_ORDER = (
    "front_month",
    "next_month",
    "current_quarter",
    "next_quarter",
)

_rqdatac_initialized = False


class IntradayBasisMonitor:
    def __init__(self) -> None:
        self.client: Optional[LiveMarketDataClient] = None
        self.is_listening = False
        self.lock = threading.RLock()
        self.latest_prices: dict[str, tuple[float, pd.Timestamp]] = {}
        self.pair_instruments: dict[tuple[str, str], tuple[str, str]] = {}
        self.instrument_pairs: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self.points: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        self.subscribed_channels: set[str] = set()
        self.trading_date: Optional[pd.Timestamp] = None
        self.last_error: Optional[str] = None

    def ensure_client(self) -> None:
        if self.client is not None and self.is_listening:
            return

        try:
            ensure_rqdatac_initialized()
            self.client = LiveMarketDataClient()
            self.client.listen(handler=self.handle_msg)
            self.is_listening = True
            self.last_error = None
        except Exception as exc:
            self.client = None
            self.is_listening = False
            self.last_error = str(exc)
            raise

    def resolve_pair(self, underlying: str, future_type: str) -> tuple[str, str]:
        index_id = UNDERLYING_INDEX_IDS[underlying]
        contracts = rqdatac.futures.get_contracts(underlying)
        contract_index = FUTURE_TYPE_ORDER.index(future_type)
        return index_id, contracts[contract_index]

    def subscribe_pair(self, underlying: str, future_type: str) -> None:
        key = (underlying, future_type)
        with self.lock:
            if key not in self.pair_instruments:
                self.pair_instruments[key] = self.resolve_pair(underlying, future_type)
                index_id, future_id = self.pair_instruments[key]
                self.instrument_pairs[index_id].add(key)
                self.instrument_pairs[future_id].add(key)

        try:
            self.ensure_client()
        except Exception:
            return
        index_id, future_id = self.pair_instruments[key]
        for instrument_id in (index_id, future_id):
            channel = f"tick_{instrument_id}"
            with self.lock:
                already_subscribed = channel in self.subscribed_channels
                if not already_subscribed:
                    self.subscribed_channels.add(channel)
            if not already_subscribed and self.client is not None:
                try:
                    self.client.subscribe(channel)
                    self.last_error = None
                except Exception as exc:
                    with self.lock:
                        self.subscribed_channels.discard(channel)
                        self.last_error = str(exc)
                    return

    def reset_if_new_trading_date(self, timestamp: pd.Timestamp) -> None:
        current_date = timestamp.normalize()
        if self.trading_date is None:
            self.trading_date = current_date
            return
        if current_date != self.trading_date:
            self.latest_prices.clear()
            self.points.clear()
            self.trading_date = current_date

    @staticmethod
    def parse_timestamp(value: Any) -> pd.Timestamp:
        if isinstance(value, pd.Timestamp):
            return value
        text = str(value)
        return pd.to_datetime(text, format="%Y%m%d%H%M%S%f")

    def handle_msg(self, tick_or_bar: dict[str, Any]) -> None:
        instrument_id = tick_or_bar.get("order_book_id")
        price = tick_or_bar.get("last")
        timestamp_value = tick_or_bar.get("datetime")
        if instrument_id is None or price is None or timestamp_value is None:
            return

        timestamp = self.parse_timestamp(timestamp_value)
        with self.lock:
            self.reset_if_new_trading_date(timestamp)
            self.latest_prices[instrument_id] = (float(price), timestamp)
            affected_pairs = list(self.instrument_pairs.get(instrument_id, set()))
            for key in affected_pairs:
                index_id, future_id = self.pair_instruments[key]
                if index_id not in self.latest_prices or future_id not in self.latest_prices:
                    continue

                index_price, index_time = self.latest_prices[index_id]
                future_price, future_time = self.latest_prices[future_id]
                if index_price <= 0:
                    continue

                point_time = max(index_time, future_time)
                basis_rate = (index_price - future_price) / index_price * 100
                point = {
                    "timestamp": point_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "basis_rate": round(float(basis_rate), 4),
                    "index_price": round(float(index_price), 2),
                    "future_price": round(float(future_price), 2),
                }
                pair_points = self.points[key]
                if pair_points and pair_points[-1]["timestamp"] == point["timestamp"]:
                    pair_points[-1] = point
                else:
                    pair_points.append(point)

    def get_points(self, underlying: str, future_type: str) -> dict[str, Any]:
        self.subscribe_pair(underlying, future_type)
        with self.lock:
            rows = list(self.points.get((underlying, future_type), []))
            if self.last_error:
                return {
                    "status": "unavailable",
                    "message": f"实时行情订阅暂不可用：{self.last_error}",
                    "rows": rows,
                }
            if not rows:
                return {
                    "status": "waiting",
                    "message": "等待实时 tick 数据；收盘后实时订阅可能不会返回新数据。",
                    "rows": rows,
                }
            return {"status": "ok", "message": "", "rows": rows}


intraday_monitor = IntradayBasisMonitor()


def ensure_rqdatac_initialized() -> None:
    """Initialize rqdatac lazily so importing the app stays test-friendly."""
    global _rqdatac_initialized
    if not _rqdatac_initialized:
        rqdatac.init()
        _rqdatac_initialized = True


def finite_or_none(value: Any, digits: Optional[int] = None) -> Optional[float]:
    if pd.isna(value):
        return None
    number = float(value)
    if digits is not None:
        return round(number, digits)
    return number


def integer_or_none(value: Any) -> Optional[int]:
    if pd.isna(value):
        return None
    return int(round(float(value)))


def contract_underlying(contract_code: str) -> str:
    return "".join(ch for ch in contract_code if ch.isalpha())


@lru_cache(maxsize=16)
def load_history(
    underlying: str,
    start_date: Optional[pd.Timestamp] = None,
    end_date: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    data_dir = os.path.join(DATA_STORE_DIR, underlying)
    if not os.path.isdir(data_dir):
        return pd.DataFrame()

    history_dates = sorted(
        pd.to_datetime(os.path.splitext(filename)[0], format="%Y%m%d", errors="coerce")
        for filename in os.listdir(data_dir)
        if filename.endswith(".pq")
    )
    history_dates = [date for date in history_dates if not pd.isna(date)]
    if not history_dates:
        return pd.DataFrame()

    effective_start = max(start_date or history_dates[0], history_dates[0])
    effective_end = min(end_date or history_dates[-1], history_dates[-1])
    if effective_start > effective_end:
        return pd.DataFrame()

    history_data = load_parquet_files(
        data_dir,
        start_date=effective_start,
        end_date=effective_end,
        columns=["future_type", "basis_annual_rate", "close_index"],
        postfix="pq",
        check=False,
    ).sort_index()
    history_data["basis_annual_rate"] *= -1.0
    return history_data


@lru_cache(maxsize=32)
def get_all_history_basis_rates(underlying: str, future_type: str) -> pd.Series:
    history = load_history(underlying)
    required_columns = {"future_type", "basis_annual_rate"}
    if history.empty or not required_columns.issubset(history.columns):
        return pd.Series(dtype=float)

    return history.loc[
        history["future_type"].eq(future_type),
        "basis_annual_rate",
    ].dropna()


def preload_history_cache() -> None:
    for underlying in PRELOAD_UNDERLYINGS:
        load_history(underlying)
        for future_type in PRELOAD_FUTURE_TYPES:
            get_all_history_basis_rates(underlying, future_type)


def percentile_rank(history: pd.Series, value: Any) -> Optional[float]:
    if pd.isna(value):
        return None

    percentile = percentileofscore(history.dropna(), float(value))
    return integer_or_none(percentile)


def basis_rate_percentiles(
    underlying: str,
    future_type: str,
    annual_basis_rate: Any,
    timestamp: pd.Timestamp,
) -> tuple[Optional[float], Optional[float]]:
    history = load_history(underlying)
    required_columns = {"future_type", "basis_annual_rate"}
    if history.empty or not required_columns.issubset(history.columns):
        return None, None

    matched_history = history.loc[history["future_type"] == future_type]
    if matched_history.empty:
        return None, None

    all_history = matched_history["basis_annual_rate"]
    one_year_start = timestamp.normalize() - pd.DateOffset(years=1)
    one_year_history = history.loc[history.index.get_level_values(1) >= one_year_start]

    if one_year_history.empty or not required_columns.issubset(
        one_year_history.columns
    ):
        one_year_matched_history = pd.Series(dtype=float)
    else:
        one_year_matched_history = one_year_history.loc[
            one_year_history["future_type"] == future_type,
            "basis_annual_rate",
        ]
    return (
        percentile_rank(one_year_matched_history, annual_basis_rate),
        percentile_rank(all_history, annual_basis_rate),
    )


def format_latest_rows(realtime_summary: pd.DataFrame) -> list[dict[str, Any]]:
    if realtime_summary is None or realtime_summary.empty:
        return []

    summary = realtime_summary.copy()
    summary.index = summary.index.astype(str)
    timestamps = pd.to_datetime(summary["datetime"])
    valuation_date = timestamps.max().normalize()
    expiry_days = get_contract_expire_date(summary.index.tolist(), valuation_date)

    rows = []
    for (contract_code, row), days_to_expiry in zip(summary.iterrows(), expiry_days):
        timestamp = pd.to_datetime(row["datetime"])
        underlying = contract_underlying(contract_code)
        one_year_percentile, all_history_percentile = basis_rate_percentiles(
            underlying=underlying,
            future_type=row["future_type"],
            annual_basis_rate=row["basis_annual_rate"],
            timestamp=timestamp,
        )

        rows.append(
            {
                "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "underlying": UNDERLYING_LABELS.get(underlying, underlying),
                "contract": contract_code,
                "future_price": finite_or_none(row["future_px"], 2),
                "spot_price": finite_or_none(row["index_px"], 2),
                "basis": finite_or_none(row["basis"], 2),
                "days_to_expiry": int(days_to_expiry)
                if not pd.isna(days_to_expiry)
                else None,
                "annual_basis_rate": finite_or_none(row["basis_annual_rate"], 2),
                "expiry_type": FUTURE_TYPE_LABELS.get(
                    row["future_type"], row["future_type"]
                ),
                "one_year_percentile": integer_or_none(one_year_percentile),
                "all_history_percentile": integer_or_none(all_history_percentile),
            }
        )

    return rows


def history_date_index(history: pd.DataFrame) -> pd.DatetimeIndex:
    if isinstance(history.index, pd.MultiIndex):
        return pd.to_datetime(history.index.get_level_values("date"))
    return pd.to_datetime(history.index)


def format_history_rows(
    history: pd.DataFrame,
    future_type: str,
    start_date: Optional[pd.Timestamp] = None,
    end_date: Optional[pd.Timestamp] = None,
    all_history_basis_rates: Optional[pd.Series] = None,
) -> list[dict[str, Any]]:
    required_columns = {"future_type", "basis_annual_rate"}
    if history.empty or not required_columns.issubset(history.columns):
        return []

    dates = history_date_index(history)
    mask = history["future_type"].eq(future_type)
    if end_date is not None:
        mask &= dates <= end_date

    value_columns = ["basis_annual_rate"]
    if "close_index" in history.columns:
        value_columns.append("close_index")

    filtered = history.loc[mask, value_columns].copy()
    if filtered.empty:
        return []

    filtered["_date"] = dates[mask]
    daily = filtered.groupby("_date", as_index=True).mean().sort_index()

    if all_history_basis_rates is None:
        all_history_basis_rates = pd.Series(dtype=float)

    display_daily = daily
    if start_date is not None:
        display_daily = display_daily.loc[display_daily.index >= start_date]
    if end_date is not None:
        display_daily = display_daily.loc[display_daily.index <= end_date]

    return [
        {
            "date": date.strftime("%Y-%m-%d"),
            "basis_annual_rate": finite_or_none(row["basis_annual_rate"], 2),
            "underlying_price": finite_or_none(row.get("close_index"), 2),
            "rolling_percentile_1y": percentile_rank(
                daily.loc[
                    (daily.index >= date - pd.DateOffset(years=1))
                    & (daily.index <= date),
                    "basis_annual_rate",
                ],
                row["basis_annual_rate"],
            ),
            "all_history_percentile": percentile_rank(
                all_history_basis_rates,
                row["basis_annual_rate"],
            ),
        }
        for date, row in display_daily.iterrows()
    ]


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/latest")
def latest_basis_rates():
    try:
        ensure_rqdatac_initialized()
        rows = format_latest_rows(get_realtime_summary())
        return jsonify({"status": "ok", "rows": rows})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc), "rows": []}), 500


@app.get("/api/history")
def historical_basis_rates():
    try:
        underlying = request.args.get("underlying", "IM").upper()
        future_type = request.args.get("future_type", "next_month")
        start_date_text = request.args.get("start_date")
        end_date_text = request.args.get("end_date")
        start_date = pd.to_datetime(start_date_text) if start_date_text else None
        end_date = pd.to_datetime(end_date_text) if end_date_text else None
        history_start_date = (
            start_date - pd.DateOffset(years=1) if start_date is not None else None
        )

        history = load_history(
            underlying,
            start_date=history_start_date,
            end_date=end_date,
        )
        all_history_basis_rates = get_all_history_basis_rates(underlying, future_type)
        rows = format_history_rows(
            history=history,
            future_type=future_type,
            start_date=start_date,
            end_date=end_date,
            all_history_basis_rates=all_history_basis_rates,
        )
        return jsonify({"status": "ok", "rows": rows})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc), "rows": []}), 500


@app.get("/api/intraday")
def intraday_basis_rates():
    underlying = request.args.get("underlying", "IM").upper()
    future_type = request.args.get("future_type", "next_month")
    payload = intraday_monitor.get_points(underlying, future_type)
    return jsonify(payload)


if __name__ == "__main__":
    if os.environ.get("WERKZEUG_RUN_MAIN") in (None, "true"):
        preload_history_cache()
    app.run(host="0.0.0.0", port=5000, debug=True)
