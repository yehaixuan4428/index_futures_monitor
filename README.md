# Index Futures Basis Rate Monitor

股指期货基差率监控页面。当前版本复现线上页面的 `Latest Basis Rates` 和 `Historical Basis Rate Analysis` 区域，使用 `data_source.py` 中的数据接口展示最新与历史基差数据。

## 功能

- 展示 IH、IF、IC、IM 四类股指期货合约的最新基差信息。
- 页面加载后立即请求实时数据。
- 前端每 60 秒自动刷新一次 `/api/latest`。
- 若在历史数据目录（见下文「历史数据目录」）下对应品种子目录中存在 parquet 数据，会尝试计算过去一年分位数和全历史分位数。
- 支持按品种、合约类型和日期范围查看日度历史分析图，前端使用 Plotly.js 渲染交互式图表。
- 历史分析包含三张图：年化基差率与标的指数价格双轴图、1 年滚动分位数图、全历史分位数图。
- 支持分时模式，按品种和合约类型订阅实时 tick，绘制当日实时基差率曲线。

## 文件说明

- `data_source.py`：底层数据接口，包含 `get_realtime_summary()` 和历史数据生成逻辑。
- `app.py`：Flask Web 服务，负责页面渲染、实时数据接口、字段格式化和历史分位数计算。
- `templates/index.html`：Latest Basis Rates 页面，包含表格样式和自动刷新逻辑。
- `tests/test_app.py`：接口格式和页面关键行为测试。

## 历史数据目录

应用与更新脚本通过环境变量 **`DATABASE_EXTRA`** 定位历史 parquet 根路径，实际读取目录为：

```text
%DATABASE_EXTRA%\index_futures_database\<品种>\*.pq
```

其中 `<品种>` 为 `IF`、`IH`、`IC`、`IM` 之一（与 `app.py` 中 `DATA_STORE_DIR` 一致）。请在本机设置 `DATABASE_EXTRA` 为父目录路径；`index_futures_database` 子目录需自行创建或由 `update_database.py` 等脚本写入。

## 启动方式

请先设置环境变量 `DATABASE_EXTRA`（PowerShell 示例：`$env:DATABASE_EXTRA = "D:\data"`），然后在项目根目录运行：

```powershell
& "C:/Users/yehaixuan_xs/.conda/envs/py310/python.exe" app.py
```

启动后访问：

```text
http://127.0.0.1:5000/
```

## API

### `GET /api/latest`

返回实时 Latest Basis Rates 表格数据。

返回字段：

- `timestamp`：数据时间戳。
- `underlying`：品种中文名称和代码。
- `contract`：期货合约代码。
- `future_price`：期货价格。
- `spot_price`：现货指数价格。
- `basis`：基差。
- `days_to_expiry`：到期天数。
- `annual_basis_rate`：年化基差率，单位为百分比。
- `expiry_type`：到期类型，包含近月、次近月、季月、次季月。
- `one_year_percentile`：过去一年分位数；历史数据不足时为空。
- `all_history_percentile`：全历史分位数；历史数据不足时为空。

### `GET /api/history`

返回 Historical Basis Rate Analysis 图表数据。图中的 `Basis Rate` 使用原始 `basis_annual_rate` 字段，即年化基差率。

查询参数：

- `underlying`：品种代码，可选 `IF`、`IH`、`IC`、`IM`，默认 `IM`。
- `future_type`：合约类型，可选 `front_month`、`next_month`、`current_quarter`、`next_quarter`。
- `start_date`：开始日期，格式 `YYYY-MM-DD`，可选。
- `end_date`：结束日期，格式 `YYYY-MM-DD`，可选。

返回字段：

- `date`：历史交易日期。
- `basis_annual_rate`：年化基差率，单位为百分比。
- `underlying_price`：标的指数价格。
- `rolling_percentile_1y`：1 年滚动分位数。接口会在展示开始日期基础上额外向前读取 1 年历史数据，用于保证展示区间内每个点都有足够的滚动统计窗口。
- `all_history_percentile`：全历史分位数。展示区间内每个点都会与对应品种、对应合约类型的全历史年化基差率序列比较。

### `GET /api/intraday`

返回当日分时基差率数据。接口会按请求参数订阅对应指数和期货合约的 tick 行情，并在服务进程内缓存当天收到的增量 tick 计算结果。

查询参数：

- `underlying`：品种代码，可选 `IF`、`IH`、`IC`、`IM`，默认 `IM`。
- `future_type`：合约类型，可选 `front_month`、`next_month`、`current_quarter`、`next_quarter`。

计算口径：

```text
basis_rate = (index_realtime_price - future_realtime_price) / index_realtime_price
```

前端展示为百分比。

返回字段：

- `timestamp`：tick 更新时间。
- `basis_rate`：实时基差率，单位为百分比。
- `index_price`：指数实时价格。
- `future_price`：期货实时价格。

## 测试

运行前需已设置 **`DATABASE_EXTRA`**（与运行时一致或可指向任意有效父路径），否则导入 `app` 时会因缺少该变量失败。

```powershell
& "C:/Users/yehaixuan_xs/.conda/envs/py310/python.exe" -m pytest tests/test_app.py -q
```
