# BTC Quant Console 1.0

**本地优先的数字资产交易与策略研究工作区。**  
**A local-first workspace for digital-asset trading and strategy research.**

中文说明在前，English follows below.

## 中文

### 项目目标

将行情图表、模拟交易、交易所连接、订单核对与策略研究放在同一个本地工作区。以清楚的账户状态、明确的订单确认和可追踪的执行过程改善个人操作体验。

### 主要功能

| 模块 | 功能 |
| --- | --- |
| 行情与图表 | BTC、ETH、BNB、XRP、TRX、DOGE 交易对；K 线、成交量、均线、绘图工具及多图布局。 |
| 模拟交易 | 本地现货、合约及网格模拟，支持在配置实盘连接前熟悉界面与流程。 |
| 现货操作 | Binance 现货连接、市价与限价订单、数量步长和金额校验、比例卖出、订单预览与确认。 |
| 执行与核对 | 提交期间锁定表单、按确认号跟踪回执、部分成交展示、状态未知时核对及重复提交防护。 |
| 交易所能力 | 将行情、账户读取、手动订单、AUTO 与网格的支持状态分别展示。支持不等于已连接或已通过实盘验收。 |
| 合约与网格 | 参数配置、模拟流程及市场状态展示；真实操作按适配器、权限和实际验证状态开放。 |
| 策略研究 | 趋势策略、事件研究、机器学习相关研究界面及本地模型接入接口。 |

### 当前边界

- 软件默认使用本机回环地址，适合个人本地运行。
- PAPER 启动方式会禁用实盘接口。实盘需要使用者自行配置交易所 API，并通过权限和连接核验。
- 各交易所、各市场的能力和可用性不同；合约及真实网格的接入与验收状态需分别确认。
- 测试结果来自隔离环境；完整的真实账户、持续运行和升级迁移验收需要在相应环境执行。
- 首次发布包含源码与重新构建的 Windows x64 发行包。不会随包提供任何个人 API 凭据、账户账本或交易日志。

### Windows 快速开始

1. 从 [Releases](https://github.com/Coribri/BTCQuantConsole/releases) 下载 `BTCQuantConsole-1.0-win64.zip`。
2. 完整解压，保留 `BTCQuantConsole.exe` 与 `_internal` 的目录关系。
3. 双击 `Start-PAPER.bat` 启动模拟模式，访问 `http://127.0.0.1:18787`。
4. 使用实盘工作区时，先关闭当前模拟进程，再运行 `Start-LIVE.bat`，打开 `http://127.0.0.1:18787/?account=live`。
5. 在软件中自行配置 API。先读取并核对账户，再预览具体订单。

每个运行目录拥有独立的配置和账本。升级或切换目录时，先确认数据归属，并保留自己的 `config.toml` 与 `runtime` 备份。

### 源码运行

需要 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python run_console.py web --paper --port 18787 --no-open
```

`config.toml` 中可调整交易对、行情周期和本地端口。开发与验证说明见 [开发文档](docs/DEVELOPMENT.md)。

### 后续目标

- 完善交易所能力与健康状态的统一表达。
- 加强账户核对、断线恢复与版本迁移验证。
- 补齐各市场的行情、保证金与订单语义。
- 扩展可复用的适配器、测试覆盖和本地使用说明。

## English

### Project goals

Bring market charts, paper trading, exchange connections, order reconciliation and strategy research into one local workspace. The project focuses on understandable account states, explicit order confirmation and traceable execution.

### Features

| Module | Capabilities |
| --- | --- |
| Market charts | BTC, ETH, BNB, XRP, TRX and DOGE pairs; candles, volume, moving averages, drawing tools and multiple chart panes. |
| Paper trading | Local spot, futures and grid simulations for exploring the interface and workflows. |
| Spot orders | Binance spot connections, market and limit orders, exchange-rule validation, percentage-based selling and explicit order previews. |
| Execution tracking | Locked inputs during submission, confirmation-based receipts, partial-fill display, unknown-outcome reconciliation and duplicate-submission protection. |
| Exchange capabilities | Separate status for market data, account reads, manual orders, AUTO and grids. Implementation support does not imply a connected or verified live account. |
| Futures and grids | Configuration, simulation and market-state views; live availability depends on the adapter, permissions and completed verification. |
| Research | Trend strategies, event and machine-learning research views, and interfaces for local external models. |

### Current scope

- Designed for personal use on a local loopback address.
- PAPER mode disables live interfaces. Live workflows require your own exchange API credentials and successful connection and permission checks.
- Exchange and market support varies. Futures and live-grid readiness must be evaluated separately.
- Automated tests use isolated fixtures. Real-account, long-running and migration checks require separate validation.
- This initial publication includes source code and a rebuilt Windows x64 package. No personal API credentials, account ledgers or trading logs are included.

### Windows quick start

1. Download `BTCQuantConsole-1.0-win64.zip` from [Releases](https://github.com/Coribri/BTCQuantConsole/releases).
2. Extract the complete archive and keep `BTCQuantConsole.exe` beside `_internal`.
3. Run `Start-PAPER.bat` and open `http://127.0.0.1:18787`.
4. To use the live workspace, close the paper process first, run `Start-LIVE.bat`, then open `http://127.0.0.1:18787/?account=live`.
5. Configure your own API connection in the application. Read and reconcile the account before previewing a specific order.

Each working directory has its own configuration and ledger. Preserve your own configuration and runtime backups when moving between versions or directories.

### Run from source

Python 3.11 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python run_console.py web --paper --port 18787 --no-open
```

See [Development](docs/DEVELOPMENT.md) for tests and packaging.

### Next steps

- Consistent capability and health reporting across exchange adapters.
- Stronger account reconciliation, connection recovery and migration checks.
- Clear market-data, margin and order semantics for each market.
- Reusable adapters, broader tests and improved local documentation.

## Third-party components

Bundled components retain their own licenses and notices. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). No project-wide license has been selected for this initial publication.
