import os
import pandas as pd
import rqdatac
from multi_factor_system.tools import get_trading_dates, load_parquet_files
from tqdm import tqdm
from typing import List


UNDERLYINGS = ["IH", "IF", "IC", "IM"]


def get_contract_expire_date(contract_codes: List[str], date: pd.Timestamp):
    """
    获取合约到期天数
    """
    instrument_info = rqdatac.instruments(contract_codes)
    days = [(pd.to_datetime(i.maturity_date) - date).days for i in instrument_info]
    return days


def get_realtime_summary():
    contract_codes = []
    for underlying in UNDERLYINGS:
        contract_codes.extend(rqdatac.futures.get_contracts(underlying))
    future_info = rqdatac.futures.get_current_basis(contract_codes)
    future_info["basis"] *= -1.0
    future_info["basis_rate"] *= -1.0
    future_info["basis_annual_rate"] *= -1.0

    future_info["future_type"] = [
        "front_month",
        "next_month",
        "current_quarter",
        "next_quarter",
    ] * len(UNDERLYINGS)

    return future_info


def get_daily_summary(underlying: str, date: pd.Timestamp):
    """
    获取对应underlying的指标信息
    """
    contract_codes = rqdatac.futures.get_contracts(underlying, date)
    future_info = rqdatac.futures.get_basis(
        contract_codes,
        start_date=date,
        end_date=date,
        fields=["basis", "basis_rate", "basis_annual_rate", "close_index", "close"],
    )
    if future_info is None:
        return None

    future_info_adjusted = rqdatac.futures.get_basis(
        contract_codes,
        start_date=date,
        end_date=date,
        fields=["basis", "basis_rate", "basis_annual_rate"],
        dividend_adjusted=True,
    )
    future_info_adjusted.columns = [
        i + "_adjusted" for i in future_info_adjusted.columns
    ]
    future_info = pd.concat([future_info, future_info_adjusted], axis=1)
    if not future_info.empty:
        future_info["future_type"] = [
            "front_month",
            "next_month",
            "current_quarter",
            "next_quarter",
        ]
    return future_info


if __name__ == "__main__":
    rqdatac.init()
    root_dir = os.path.join(os.environ["DATABASE_EXTRA"], "index_futures_database")

    # underlying = "IH"
    # os.makedirs(os.path.join(root_dir, underlying), exist_ok=True)
    # for date in tqdm(
    #     get_trading_dates(pd.to_datetime("20150416"), pd.to_datetime("20260429"))
    # ):
    #     info = get_daily_summary(underlying=underlying, date=date)
    #     info.to_parquet(
    #         os.path.join(root_dir, underlying, f"{date.strftime('%Y%m%d')}.pq")
    #     )

    underlying = "IF"
    os.makedirs(os.path.join(root_dir, underlying), exist_ok=True)
    for date in tqdm(
        get_trading_dates(pd.to_datetime("20100416"), pd.to_datetime("20150415"))
    ):
        info = get_daily_summary(underlying=underlying, date=date)
        info.to_parquet(
            os.path.join(root_dir, underlying, f"{date.strftime('%Y%m%d')}.pq")
        )

    # underlying = "IC"
    # os.makedirs(os.path.join(root_dir, underlying), exist_ok=True)
    # for date in tqdm(
    #     get_trading_dates(pd.to_datetime("20150416"), pd.to_datetime("20260429"))
    # ):
    #     info = get_daily_summary(underlying=underlying, date=date)
    #     info.to_parquet(
    #         os.path.join(root_dir, underlying, f"{date.strftime('%Y%m%d')}.pq")
    #     )

    # underlying = "IM"
    # os.makedirs(os.path.join(root_dir, underlying), exist_ok=True)
    # for date in tqdm(
    #     get_trading_dates(pd.to_datetime("20220722"), pd.to_datetime("20260429"))
    # ):
    #     info = get_daily_summary(underlying=underlying, date=date)
    #     info.to_parquet(
    #         os.path.join(root_dir, underlying, f"{date.strftime('%Y%m%d')}.pq")
    #     )
    # print(get_realtime_summary())
