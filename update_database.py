import os
from data_source import get_daily_summary
import pandas as pd
import rqdatac
from multi_factor_system.tools import (
    get_trading_dates,
    load_parquet_files,
    is_trading_date,
    get_previous_trading_date,
)
from tqdm import tqdm
from typing import List

rqdatac.init()
root_dir = os.path.join(os.environ["DATABASE_EXTRA"], "index_futures_database")

today = pd.to_datetime(pd.to_datetime("today").strftime("%Y%m%d"))

if not is_trading_date(today):
    exit(0)

prev_date = get_previous_trading_date(today)


underlying = "IH"
os.makedirs(os.path.join(root_dir, underlying), exist_ok=True)
for date in tqdm(get_trading_dates(prev_date, prev_date)):
    info = get_daily_summary(underlying=underlying, date=date)
    info.to_parquet(os.path.join(root_dir, underlying, f"{date.strftime('%Y%m%d')}.pq"))

underlying = "IF"
os.makedirs(os.path.join(root_dir, underlying), exist_ok=True)
for date in tqdm(get_trading_dates(prev_date, prev_date)):
    info = get_daily_summary(underlying=underlying, date=date)
    info.to_parquet(os.path.join(root_dir, underlying, f"{date.strftime('%Y%m%d')}.pq"))

underlying = "IC"
os.makedirs(os.path.join(root_dir, underlying), exist_ok=True)
for date in tqdm(get_trading_dates(prev_date, prev_date)):
    info = get_daily_summary(underlying=underlying, date=date)
    info.to_parquet(os.path.join(root_dir, underlying, f"{date.strftime('%Y%m%d')}.pq"))

underlying = "IM"
os.makedirs(os.path.join(root_dir, underlying), exist_ok=True)
for date in tqdm(get_trading_dates(prev_date, prev_date)):
    info = get_daily_summary(underlying=underlying, date=date)
    info.to_parquet(os.path.join(root_dir, underlying, f"{date.strftime('%Y%m%d')}.pq"))
