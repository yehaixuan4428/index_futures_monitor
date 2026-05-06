import importlib

import pandas as pd


def test_latest_api_formats_realtime_summary(monkeypatch):
    app_module = importlib.import_module("app")

    sample = pd.DataFrame(
        {
            "datetime": [pd.Timestamp("2026-04-30 13:51:22")],
            "index_px": [4804.8817],
            "future_px": [4790.2],
            "basis": [-14.6817],
            "basis_annual_rate": [-9.548687],
            "future_type": ["front_month"],
        },
        index=pd.Index(["IF2605"], name="order_book_id"),
    )

    monkeypatch.setattr(app_module, "ensure_rqdatac_initialized", lambda: None)
    monkeypatch.setattr(app_module, "get_realtime_summary", lambda: sample)
    monkeypatch.setattr(app_module, "get_contract_expire_date", lambda codes, date: [15])
    monkeypatch.setattr(app_module, "basis_rate_percentiles", lambda *args, **kwargs: (12.34, 56.78))

    client = app_module.app.test_client()
    response = client.get("/api/latest")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["rows"] == [
        {
            "timestamp": "2026-04-30 13:51:22",
            "underlying": "沪深300 (IF)",
            "contract": "IF2605",
            "future_price": 4790.2,
            "spot_price": 4804.88,
            "basis": -14.68,
            "days_to_expiry": 15,
            "annual_basis_rate": -9.55,
            "expiry_type": "近月",
            "one_year_percentile": 12,
            "all_history_percentile": 57,
        }
    ]


def test_index_page_contains_latest_table_and_auto_refresh():
    app_module = importlib.import_module("app")
    client = app_module.app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Index Futures Basis Rate Monitor" in html
    assert "Latest Basis Rates" in html
    assert "过去一年分位数" in html
    assert "全历史分位数" in html
    assert "group-start" in html
    assert "expiry-badge" in html
    assert "expiry-next-quarter" in html
    assert "percentile-bar" in html
    assert "percentileCell(row.one_year_percentile)" in html
    assert "percentileCell(row.all_history_percentile)" in html
    assert "setInterval(pollLatestBasisRatesIfTrading, 60000)" in html
    assert "Historical Basis Rate Analysis" in html
    assert "Commodity:" in html
    assert "Contract Type:" in html
    assert 'data-mode="intraday"' in html
    assert ">分时</button>" in html
    assert "startIntradayPolling" in html
    assert "Basis Rate" in html
    assert "Historical Basis Rate & Underlying Price" in html
    assert "Basis Rate distribution chart" in html
    assert "1-Year Rolling Percentile" in html
    assert "cdn.plot.ly" in html
    assert "Plotly.newPlot" in html
    assert "displaylogo: false" in html
    assert "fetchHistoricalBasisRates" in html


def test_load_history_uses_shared_parquet_loader(tmp_path, monkeypatch):
    app_module = importlib.import_module("app")
    data_dir = tmp_path / "IF"
    data_dir.mkdir()
    (data_dir / "20260429.pq").write_text("placeholder")
    (data_dir / "20260430.pq").write_text("placeholder")
    app_module.load_history.cache_clear()

    calls = []

    def fake_load_parquet_files(file_path, start_date, end_date, columns=None, postfix="pq", check=False):
        calls.append(
            {
                "file_path": file_path,
                "start_date": start_date,
                "end_date": end_date,
                "columns": columns,
                "postfix": postfix,
                "check": check,
            }
        )
        return pd.DataFrame(
            {
                "future_type": ["front_month"],
                "basis_annual_rate": [9.5],
            },
            index=pd.Index(["IF2605"], name="order_book_id"),
        )

    monkeypatch.setattr(app_module, "DATA_STORE_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "load_parquet_files", fake_load_parquet_files)

    history = app_module.load_history("IF")

    assert calls == [
        {
            "file_path": str(data_dir),
            "start_date": pd.Timestamp("2026-04-29"),
            "end_date": pd.Timestamp("2026-04-30"),
            "columns": ["future_type", "basis_annual_rate", "close_index"],
            "postfix": "pq",
            "check": False,
        }
    ]
    assert history["basis_annual_rate"].tolist() == [-9.5]


def test_history_api_filters_basis_annual_rate(monkeypatch):
    app_module = importlib.import_module("app")
    index = pd.MultiIndex.from_tuples(
        [
            ("IF2605", pd.Timestamp("2026-04-28")),
            ("IF2606", pd.Timestamp("2026-04-28")),
            ("IF2605", pd.Timestamp("2026-04-29")),
        ],
        names=["order_book_id", "date"],
    )
    history = pd.DataFrame(
        {
            "future_type": ["front_month", "next_month", "front_month"],
            "basis_annual_rate": [-6.123, -8.5, -7.456],
            "close_index": [4800.0, 4800.0, 4810.2],
        },
        index=index,
    )

    monkeypatch.setattr(app_module, "load_history", lambda *args, **kwargs: history)

    client = app_module.app.test_client()
    response = client.get(
        "/api/history?underlying=IF&future_type=front_month&start_date=2026-04-29&end_date=2026-04-30"
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert len(payload["rows"]) == 1
    row = payload["rows"][0]
    assert row["date"] == "2026-04-29"
    assert row["basis_annual_rate"] == -7.46
    assert row["underlying_price"] == 4810.2
    assert "rolling_percentile_1y" in row
    assert "all_history_percentile" in row


def test_history_api_returns_empty_rows_when_no_data(monkeypatch):
    app_module = importlib.import_module("app")
    monkeypatch.setattr(app_module, "load_history", lambda *args, **kwargs: pd.DataFrame())

    client = app_module.app.test_client()
    response = client.get("/api/history?underlying=IM&future_type=next_quarter")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "rows": []}
