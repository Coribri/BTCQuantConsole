# BTC Quant Console 1.0

**本地优先的数字资产交易与策略研究工作区。**  
**A local-first workspace for digital-asset trading and strategy research.**

## 一起把它做得更好 / Let's make it better

如果你喜欢 BTC Quant Console，或者它对你有帮助，欢迎在注册 Binance 时使用我的邀请码或邀请链接，支持项目继续维护。**这是作者的推荐链接，是否使用完全自愿，不影响软件使用。**

我会继续打磨体验、完善功能，并逐步支持更多 CEX（中心化交易所）和 DEX（去中心化交易所）。后续也计划推出更多语言版本，探索社区交流功能，和大家一起研究有趣的量化策略与交易机器人。

有建议、问题，或想看到的新功能，欢迎[提交 Issue](https://github.com/Coribri/BTCQuantConsole/issues)。一起交流，一起把它做得更好。

If you enjoy BTC Quant Console or find it useful, you're welcome to use my Binance referral code or link when signing up to support the project's continued development. **This is the maintainer's referral link. Using it is entirely optional and does not affect access to the software.**

I'll keep improving the experience and adding features, with plans to support more centralized exchanges (CEXs), decentralized exchanges (DEXs), and languages. I'd also like to explore community features so we can share ideas and experiment with interesting quantitative strategies and trading bots together.

Suggestions, questions, and feature ideas are always welcome—[open an issue](https://github.com/Coribri/BTCQuantConsole/issues) and help shape what comes next.

**Binance 邀请码 / Referral code：`CPA_006128T6CC`**  
[Binance 邀请链接 / Binance referral link](https://www.binance.com/activity/referral-entry/CPA?ref=CPA_006128T6CC)

<details>
<summary>查看邀请二维码 / Show referral QR code</summary>

[![Binance 邀请二维码 / Binance referral QR code](docs/images/binance-invite-qr.png)](https://www.binance.com/activity/referral-entry/CPA?ref=CPA_006128T6CC)

</details>

---

中文说明在前，English follows below.

[中文 API 教程](#api-获取与连接教程) · [English API tutorial](#api-setup-tutorial)  
分市场教程：[Binance 合约](#4-binance-合约) · [CoinW](#5-coinw-现货与合约) · [Hyperliquid](#6-hyperliquid)

## 界面预览 / Interface preview

![BTC Quant Console 1.0 — 现货模拟交易界面 / Spot paper-trading workspace](docs/images/spot-paper-workspace.png)

现货模拟交易界面（PAPER）：市场观察、K 线与成交量图、手动订单和账户概览。  
Spot paper-trading workspace: market watch, candlestick and volume charts, manual orders and account overview.

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

### API 获取与连接教程

教程核对日期：2026-11-07。下面按 **Binance/CoinW 现货与合约、Hyperliquid 和 DeepSeek** 的实际入口说明；官网菜单与权限以你的账户页面为准。

#### 1. 先确定需要哪一种 API

| 使用场景 | 是否需要密钥 | 在本平台哪里使用 |
| --- | --- | --- |
| 查看公开行情、使用现货/合约/网格本地模拟 | 不需要 | 运行 `Start-PAPER.bat`，进入模拟交易。 |
| 读取 Binance/CoinW 真实账户或手动订单 | 对应交易所的 API Key + API Secret | 运行 `Start-LIVE.bat`，在现货或合约市场分别配置。 |
| Hyperliquid 主网/测试网查询 | 实际账户的公开地址 | 合约 → Hyperliquid → 选择网络 → 查询。 |
| Hyperliquid 测试网手动订单或 AUTO | 浏览器钱包 + 临时交易代理授权 | 合约 → Hyperliquid → 测试网；见下文。 |
| 事件解析、AI 分析、模型中的 AI 功能 | DeepSeek API Key，可选 | 「事件 + ML」→「DeepSeek 连接设置」。 |

交易所密钥和 DeepSeek 密钥分别配置。只体验模拟交易时可以全部留空。

**1.0 合约能力速查**（代码实现范围；账户、地区、网络与权限仍须实际核验）：

| 市场 | 账户读取 | 手动订单 | AUTO | 合约网格 |
| --- | --- | --- | --- | --- |
| Binance USDⓈ-M 主网 | 已实现 | 已实现，需权限及预检 | 已实现，需独立预检 | 未实现 |
| CoinW 合约主网 | 已实现 | 已实现，需权限及预检 | 未实现 | 未实现 |
| Hyperliquid 主网 | 公开地址只读 | 不支持 | 不支持 | 未实现 |
| Hyperliquid 测试网 | 公开地址查询 | 钱包代理授权后可用 | 钱包代理授权并通过预检后可用 | 未实现 |

#### 2. 获取 Binance API

1. 登录 [Binance 官网](https://www.binance.com/)，在账户页面进入「API 管理 / API Management」，选择「创建 API」。账户须满足官网显示的身份验证、2FA 和开通条件。
2. **1.0 仅实现 HMAC 签名**：如果账户仍提供「系统生成 / System generated」，创建一组 API Key 与 Secret Key，可命名为 `BTCQuantConsole-local`。创建流程见 [Binance 官方教程](https://www.binance.com/en/support/faq/detail/360002502072)。
3. 首次连接先保留读取权限。需要真实现货下单时，再启用相应现货交易权限；**本教程不需要提现或资金划转权限**。
4. 配置 API 的可信 IPv4 白名单，使用运行本软件电脑的**公网出口 IP**；`127.0.0.1` 和局域网 IP 不能作为该出口地址。更换网络或代理后重新核对。权限受限时按官网要求配置 IP，保留账户安全控制。
5. 将配套的 Key 与 Secret 保存在自己的安全位置，随后填入本机软件。

**密钥类型兼容性：** [Binance 当前密钥类型文档](https://developers.binance.com/en/docs/products/spot/faqs/api_key_types)已将 HMAC 标记为弃用，并推荐 Ed25519。1.0 尚未实现 Ed25519/RSA 签名；如果账户只提供这些类型，当前版本无法直接连接，需等待适配。不要将 RSA/Ed25519 私钥填入 API Secret。

#### 3. 在平台连接 Binance 现货

1. 关闭正在运行的模拟进程，双击 `Start-LIVE.bat`，打开 `http://127.0.0.1:18787/?account=live`。
2. 选择「现货」「实盘交易」，交易所选择 **Binance**。
3. 在右侧账户区展开「API 连接设置」，分别填写 **API Key** 和 **API Secret**，点击「保存连接」。此入口使用主网密钥；测试网 / Demo 密钥不能混填。
4. 保存后软件会尝试读取账户；也可点击「读取账户」或「重新检测」。核对币种、可用余额、连接状态与 Binance 现货账户是否一致。**保存成功仅表示本地配置已写入，账户读取成功才说明读取链路可用。**
5. 仅开启读取权限时，下单保持锁定。确需手动下单时，先在 Binance 授予相应权限，再重新检测，确认界面提示交易权限已核验。
6. 核对「手动下单上限」，保持测试模式，先用订单预览检查交易对、方向、金额、数量与交易所限制。完成最终确认会向主网提交真实订单；保存连接和读取账户本身不会下单。

合约、AUTO 和真实网格须在各自市场页面分别核验能力、账户与权限。现货连接成功不能替代其他市场的验收；首次配置先完成上面的账户读取流程。

#### 4. Binance 合约

本节覆盖普通 **USDⓈ-M / USDT 保证金合约账户**，连接入口与现货分开。

1. 在 Binance 开通合约账户，再到 API 管理为兼容的 HMAC Key 配置合约读取及所需的 **Futures** 交易权限、可信公网 IP。若 Key 创建早于合约开户，或账户使用 Portfolio Margin，请先按 [Binance 官方说明](https://www.binance.com/en/support/faq/detail/360002502072)核对权限资格；当前教程不覆盖币本位和 Portfolio Margin 账户。
2. 运行 `Start-LIVE.bat`，打开 `http://127.0.0.1:18787/futures?account=live`，交易所选择 **Binance**。
3. 展开「API 连接设置」，填写该账户的 Key/Secret，点击「保存连接」「读取账户」，核对合约可用保证金、持仓、杠杆与交易权限。即使同一组 Key 同时用于现货和合约，也需要在两个入口分别保存。
4. **从本平台开仓前，需要先在 Binance 将合约设置为双向持仓模式（Hedge Mode）。** 在交易所核对目标交易对的逐仓/全仓和杠杆；平台会读取并校验当前配置。先处理好已有仓位、挂单与模式切换限制，再重新检测。
5. 在「手动下单」选择多/空、开仓/平仓、市价/限价，按界面单位填写数量并「预览订单」。核对市场、仓位方向、数量、保证金和杠杆上限后，再决定是否最终确认。平仓数量以交易所当前仓位为准。
6. 需要合约 AUTO 时，先选择执行模型和周期，并满足页面校验。1.0 要求 **逐仓、双向持仓，杠杆不超过当前有效 AUTO 上限（最高 10×，还受本地与交易所限制）**。启动与手动订单分别确认；暂停 AUTO 后仍须核对交易所仓位、挂单和保护单。

**「测试模式」是本地操作限制，不会将 Binance/CoinW 的主网订单切到测试网。** 主网手动订单或 AUTO 最终确认后使用真实账户；无需提交订单也可以完成读取验证。

#### 5. CoinW 现货与合约

操作顺序同样是「获取密钥 → 选择市场 → 保存 → 读取 → 预览」，但应使用 **CoinW 自己的密钥和市场权限**。

1. 登录 [CoinW 官网](https://www.coinw.com/)，在用户菜单进入 **API Management / API 管理**，创建 Key/Secret，完成页面要求的验证，保存 Secret。具体菜单见 [CoinW 官方创建教程](https://www.coinw.com/api-doc/en/common/account-api-creation)。
2. 首次先使用读取权限。需要下单时，在后台分别选择 **Spot Trading** 或 **Futures Trading**，不要把现货权限当作合约权限；交易密钥按官网要求完成 2FA，并配置可信公网 IP。上述流程不需要提现权限。
3. 合约用户须先完成 CoinW 合约账户开通及页面要求的风险评估，参考 [官方合约开通说明](https://www.coinw.com/api-doc/en/common/precautions)。
4. 运行 `Start-LIVE.bat`。现货进入 `http://127.0.0.1:18787/?account=live`；合约进入 `http://127.0.0.1:18787/futures?account=live`。交易所选择 **CoinW**，在当前市场的「API 连接设置」保存，再「读取账户」。
5. 核对该市场的资产或合约保证金与持仓。**1.0 对 CoinW 的交易权限会保留「未核验 / unknown」状态**，读取成功不表示已独立确认交易权限；需自行在 CoinW 后台核对授权。手动订单仍要经过平台预览与交易所实际校验。
6. 合约下单时核对多/空、开/平、杠杆、保证金模式及数量单位。平仓要选择对应的真实仓位；CoinW 合约张数与币数量的换算由交易规则校验，不能直接照搬其他交易所的数量。
7. 提交后到「实盘委托」及 CoinW 核对订单号和成交情况。收到订单号只说明交易所接受请求；状态未知时按原确认号核对回执。

1.0 的 **CoinW 合约提供手动订单，不支持 AUTO 或合约网格**。现货与合约连接独立保存；现货网格是否可用，仍以该市场能力和连接校验为准。

#### 6. Hyperliquid

Hyperliquid 采用 **公开地址查询 + 钱包签名授权**，不在 Binance/CoinW 的 Key/Secret 输入框配置，也不需要填写助记词或主钱包私钥。

**主网：查看行情、账户、持仓与委托**

1. 运行 `Start-LIVE.bat`，打开 `http://127.0.0.1:18787/futures?account=live&venue=hyperliquid`，或在合约交易所列表选择「Hyperliquid · 链上」。
2. 在「网络」中明确选择 **主网**；页面初始选择的是测试网，切勿仅凭交易对判断网络。
3. 在「公开钱包地址」填入实际交易账户的 `0x` 开头、共 42 字符地址，点击「查询持仓与委托」。也可在装有兼容钱包扩展的浏览器中，点击「连接钱包」，授权页面读取公开地址后再查询。
4. 查询实际主账户或子账户地址，**不要使用交易代理 / API wallet 的地址**。地址查询的含义见 [Hyperliquid 官方 Info 文档](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)。
5. 核对权益、占用保证金、持仓与挂单。**1.0 的 Hyperliquid 主网仅只读，不支持主网下单、AUTO 或网格。**

**测试网：授权代理、试单与 AUTO**

1. 将「网络」切到 **测试网**。在 [Hyperliquid 官方测试网](https://app.hyperliquid-testnet.xyz/)用同一账户准备测试 USDC，并回到本平台查询确认合约权益。测试水龙头有资格要求，先阅读 [官方说明](https://hyperliquid.gitbook.io/hyperliquid-docs/onboarding/testnet-faucet)；不是每个新地址都能直接领取。钱包里的普通代币余额或本地 PAPER 余额不等于测试网合约保证金。
2. 使用带钱包扩展的 Chrome/Edge 等浏览器打开本机地址，点击「连接钱包」。手动填写公开地址只能查询，交易授权需要连接可签名的钱包；硬件钱包需经兼容浏览器扩展连接。
3. 点击「授权交易代理」，在钱包中核对 **Hyperliquid Testnet** 和代理地址后，由你确认签名。本机创建的是测试网交易代理，代理密钥仅保留在本次后端进程的内存中。
4. 在「永续下单」选择多/空、开/平、市价 IOC 或限价 GTC，填写数量与所需限价，点击「预览订单」。核对测试网与订单内容后，再确认提交；随后查看持仓、委托及「本机送单记录」。
5. 需要 AUTO 时，先按页面要求检查该交易对的逐仓与杠杆配置（当前提示为 1–10×），并确认无已有仓位和挂单，再选模型、1H/4H 周期与目标名义价值，点击「预览并启动 AUTO」并确认。Hyperliquid 网格尚未实现。

「暂停 AUTO」「停用本机代理」以及退出软件都**不会自动清除交易所现有仓位和挂单**；「停用本机代理」会停止本机签名并暂停 AUTO，链上授权仍需到 Hyperliquid 的 API 钱包管理中撤销。重启程序后需要重新授权本机代理。代理与账户地址的区别见 [官方 API wallets 文档](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets)。

#### 7. 获取并连接 DeepSeek（可选）

1. 登录 [DeepSeek 开放平台的 API Keys 页面](https://platform.deepseek.com/api_keys)，创建自己的 API Key。API 账号的可用额度在开放平台管理。
2. 打开本机 `http://127.0.0.1:18787/research`，或点击顶部「事件 + ML」，再点击「DeepSeek 连接设置」。
3. 填写 API Key，模型名称可先使用 `deepseek-flash`，点击「保存本地设置」。可用模型以 [DeepSeek 官方文档](https://api-docs.deepseek.com/)及你的 API 账户为准。
4. 页面显示「DeepSeek 已配置」表示 Key 已保存。点击 AI 多空展望区的「生成分析」，收到有效结果后才完成实际调用验证；该操作会使用 API 额度。
5. 需要事件解析时，先「采集公开事件」，再「DeepSeek 解析」。需要模型中的 AI 功能时，到「模型工坊」为对应模型选择 AI 模式；保存 Key 不会自动启动 AUTO。

1.0 的此项连接使用官方 `https://api.deepseek.com/chat/completions`，不提供自定义 Base URL。其他服务商的 Key 不能直接填在这里。DeepSeek 的配置与模拟/实盘模式独立，PAPER 下调用 AI 也会使用 API 额度。

#### 8. 密钥保存在什么地方

各连接的本地保存位置如下；`readonly` 是历史文件名，实际交易能力取决于权限与市场。文件配置**不是加密密钥库**。

| 连接 | 保存位置 |
| --- | --- |
| DeepSeek | `.env` |
| Binance 现货 / 合约 | `runtime/binance_readonly.json` / `runtime/binance_futures_readonly.json` |
| CoinW 现货 / 合约 | `runtime/coinw_spot_readonly.json` / `runtime/coinw_readonly.json` |
| Hyperliquid 测试网代理密钥 | 当前后端进程内存；重启后本机不再持有该密钥，需重新授权。 |

仓库和发行包不附带个人密钥；`.gitignore` 已排除 `.env` 与 `runtime`。分享日志、截图、备份或重新打包前，检查其中没有自己的 Key/Secret。需要移除 Binance 本地连接时，使用「清除本机连接」；密钥撤销由交易所或 DeepSeek 开放平台管理。

#### 9. 常见问题

| 现象 | 检查方法 |
| --- | --- |
| 行情正常，但账户未连接 | 公开行情与私有账户独立；核对是否从 LIVE 启动、选择了正确交易所和市场，并保存该市场的密钥。 |
| Binance `-2015` | 检查 Key、主网/测试网、出口 IP 白名单和 API 权限。 |
| Binance `-1022` / `-1021` | 前者核对配套 Secret 与 HMAC 类型；后者先同步 Windows 系统时间，再重新检测。 |
| 账户可读，但无法下单 | 检查交易权限核验、平台下单上限、交易对最小金额/数量与可用余额。 |
| CoinW `6003` / `6001` / `29001` | 分别核对市场权限、配套 Key/Secret 与签名、请求频率；参见 [官方错误码](https://www.coinw.com/api-doc/en/common/error-codes)。 |
| Hyperliquid 查询为 0 / 无法授权 | 核对主网/测试网、实际账户地址和合约保证金；代理授权仅在测试网且连接兼容钱包后可用。 |
| DeepSeek `401` / `402` / `429` | 分别检查 API Key、可用余额、请求速率或并发；同时核对模型名称。 |
| 订单超时或状态未知 | 按原确认号核对交易所订单和本地回执，确认结果后再操作。 |

错误含义参考 [Binance 官方错误码](https://developers.binance.com/en/docs/products/spot/errors)与 [DeepSeek 官方错误码](https://api-docs.deepseek.com/quick_start/error_codes/)。

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
- 完善同API合约和现货之间转账，冷钱包之间转账。
- 完善交易机器人。

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

### API setup tutorial

Verified on 2026-11-07. This walkthrough covers **Binance/CoinW spot and futures, Hyperliquid, and DeepSeek**. Account-specific requirements and current provider menus take precedence.

#### 1. Choose the connection you need

| Workflow | Credentials | Platform entry |
| --- | --- | --- |
| Public charts and local spot/futures/grid simulation | None | Run `Start-PAPER.bat`. |
| Binance/CoinW account reads or manual orders | The exchange's API Key + API Secret | Run `Start-LIVE.bat`; configure each spot/futures market separately. |
| Hyperliquid mainnet/testnet queries | Actual account's public address | Futures → Hyperliquid → choose network → query. |
| Hyperliquid testnet orders or AUTO | Browser wallet + temporary agent authorization | Futures → Hyperliquid → testnet; see below. |
| Event extraction, AI analysis and model AI features | Optional DeepSeek API Key | Event + ML → DeepSeek connection settings. |

Exchange credentials and DeepSeek credentials are configured separately. Paper trading works without either key.

**Version 1.0 futures capabilities** describe implemented scope, not completed acceptance for every account, region or network:

| Market | Account reads | Manual orders | AUTO | Futures grids |
| --- | --- | --- | --- | --- |
| Binance USDⓈ-M mainnet | Implemented | Requires permission/preflight | Requires separate preflight | Unsupported |
| CoinW futures mainnet | Implemented | Requires permission/preflight | Unsupported | Unsupported |
| Hyperliquid mainnet | Public-address read-only | Unsupported | Unsupported | Unsupported |
| Hyperliquid testnet | Public-address queries | Requires wallet-agent approval | Requires approval/preflight | Unsupported |

#### 2. Obtain a Binance API key

1. Sign in to [Binance](https://www.binance.com/), open account → **API Management** → **Create API**, and complete the account requirements shown there.
2. **Version 1.0 implements HMAC signing only.** If **System generated** is available, create an API Key / Secret Key pair and label it `BTCQuantConsole-local`. Follow the [official creation guide](https://www.binance.com/en/support/faq/detail/360002502072).
3. Start with reading access. Enable spot trading only when needed; withdrawals and fund transfers are unnecessary for this walkthrough.
4. Restrict access to your computer's public outbound IPv4 address. Recheck it after network/proxy changes; keep account security controls enabled.
5. Store the matching Key and Secret securely, then enter them in the local application.

**Compatibility:** Binance [marks HMAC as deprecated and recommends Ed25519](https://developers.binance.com/en/docs/products/spot/faqs/api_key_types). Version 1.0 does not implement Ed25519/RSA signing. If these are the only available types, an adapter update is required before connecting. Do not paste an asymmetric private key into API Secret.

#### 3. Connect Binance spot in the platform

1. Stop the running paper process, run `Start-LIVE.bat`, and open `http://127.0.0.1:18787/?account=live`.
2. Select spot, live trading and **Binance**.
3. Expand **API 连接设置** (API connection settings) in the account panel, enter the pair, and click **保存连接** (save connection). This entry uses mainnet credentials, not testnet/Demo keys.
4. Saving triggers an account read. You can also use **读取账户** (read account) or **重新检测** (recheck). Reconcile assets and available balances with Binance spot. A saved configuration alone does not confirm connectivity.
5. Reading-only access keeps orders locked. If you need trading, grant the relevant permission on Binance, recheck, and wait for confirmed trading permission in the UI.
6. Review manual-order limits and keep test mode enabled. Use order previews to inspect symbol, side, amount, quantity and exchange filters. Final confirmation sends a real mainnet order; saving credentials or reading an account does not.

Validate futures, AUTO and live grids separately on their market pages. A successful spot read does not establish another market's readiness.

#### 4. Binance futures

This workflow covers a standard **USDⓈ-M / USDT-margined futures account**, with credentials configured separately from spot.

1. Activate your futures account, then configure futures access and a trusted public IP for a compatible HMAC key. If the key predates futures activation or the account uses Portfolio Margin, consult the [official API FAQ](https://www.binance.com/en/support/faq/detail/360002502072). This walkthrough does not cover COIN-M or Portfolio Margin accounts.
2. Run `Start-LIVE.bat`, open `http://127.0.0.1:18787/futures?account=live`, and select **Binance**.
3. Save the pair in **API 连接设置**, read the account, and reconcile futures margin, positions, leverage and permissions. Even a shared spot/futures key must be saved in each market entry.
4. **Opening positions from version 1.0 requires Hedge Mode on Binance.** Review the symbol's margin mode and leverage on the exchange, resolve any existing-position/order restrictions, then recheck in this platform.
5. Select long/short, open/close and market/limit. Enter quantity in the displayed unit and preview. Review the market, position side, quantity and effective limits before final confirmation. Closing uses the actual exchange position.
6. Futures AUTO requires **isolated margin, Hedge Mode and leverage within the effective AUTO cap, at most 10×**. Exchange and local limits may lower it. Choose the model/interval, preview and confirm separately. After pausing, reconcile positions, outstanding orders and protective orders.

**The local “test mode” setting does not route Binance/CoinW mainnet orders to a testnet.** Final confirmation uses the real account. Account-read verification requires no order submission.

#### 5. CoinW spot and futures

The workflow is similar, using **CoinW credentials and the permissions for the selected market**.

1. Sign in to [CoinW](https://www.coinw.com/), open user menu → **API Management**, create a Key/Secret pair, complete verification and save the Secret securely. See the [official creation guide](https://www.coinw.com/api-doc/en/common/account-api-creation).
2. Start with reading. For orders, enable the relevant **Spot Trading** or **Futures Trading** permission, complete required 2FA and configure trusted public IPs. Withdrawals are unnecessary.
3. Activate futures and complete the required assessment before futures use, following [CoinW's account precautions](https://www.coinw.com/api-doc/en/common/precautions).
4. Run `Start-LIVE.bat`. Use `http://127.0.0.1:18787/?account=live` for spot or `http://127.0.0.1:18787/futures?account=live` for futures. Select **CoinW**, save credentials for that market, and read the account.
5. Reconcile assets, margin and positions. **Version 1.0 leaves CoinW trading permission as unknown**, rather than independently confirming it. Check authorization on CoinW; manual orders still require platform previews and exchange validation.
6. For futures, verify side, open/close, leverage, margin mode and quantity units. Select the actual position when closing. Contract counts and base-asset quantities are not interchangeable across exchanges.
7. Reconcile the returned order ID and subsequent fills in both places. An accepted request is not proof of a fill. Use the original confirmation ID if the outcome is unknown.

**CoinW futures supports manual orders in 1.0; futures AUTO and grids are unsupported.** Spot and futures credentials are stored separately. Spot-grid availability depends on that market's capability and connection checks.

#### 6. Hyperliquid wallets and testnet

Hyperliquid uses **public-address queries and wallet-signature authorization**. Do not enter its credentials in the Binance/CoinW fields or provide a seed phrase/main-wallet private key.

**Mainnet: market and account queries**

1. Run `Start-LIVE.bat` and open `http://127.0.0.1:18787/futures?account=live&venue=hyperliquid`, or select Hyperliquid in the futures exchange list.
2. Explicitly select **主网** (mainnet) in the network selector; the initial selection is testnet.
3. Enter the actual account's 42-character `0x` address and select **查询持仓与委托** (query positions/orders). Alternatively connect a compatible browser wallet to obtain its public address, then query.
4. Use the actual master/subaccount address, not an API/agent wallet address. See the [official Info documentation](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint).
5. Reconcile equity, used margin, positions and orders. **Hyperliquid mainnet is read-only in version 1.0: no mainnet orders, AUTO or grids.**

**Testnet: delegated trading and AUTO**

1. Select **测试网** (testnet). Prepare mock USDC for the same account through the [official testnet](https://app.hyperliquid-testnet.xyz/), then query its perpetual account equity. Read the [faucet eligibility requirements](https://hyperliquid.gitbook.io/hyperliquid-docs/onboarding/testnet-faucet); not every new address qualifies. Wallet-token or local PAPER balances do not establish testnet margin.
2. Open the local platform in a browser with a compatible wallet extension and connect it. An address pasted manually enables queries only. Hardware wallets require a compatible extension.
3. Select **授权交易代理** (authorize trading agent). Review Hyperliquid Testnet and the agent address in your wallet before signing. The generated testnet agent key stays in backend memory for the current process.
4. Choose long/short, open/close, market IOC/limit GTC, quantity and any required price. Preview, review the testnet order and confirm. Reconcile positions, open orders and local submission records.
5. For AUTO, follow the displayed isolated-margin/leverage requirements, currently 1–10×, with no existing positions or orders for the symbol. Select the model, 1H/4H interval and target notional, then preview and confirm. Grids are unsupported.

Pausing AUTO, disabling the local agent or exiting **does not clear exchange positions or outstanding orders**. Disabling the agent stops local signing and pauses AUTO; revoke onchain authorization through Hyperliquid API-wallet management. Restarting requires a new local authorization. See [API-wallet documentation](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets).

#### 7. Obtain and connect DeepSeek (optional)

1. Sign in to [DeepSeek API Keys](https://platform.deepseek.com/api_keys) and create your own key. Manage API credit in that platform.
2. Open `http://127.0.0.1:18787/research`, or **事件 + ML** (Event + ML), then **DeepSeek 连接设置** (connection settings).
3. Enter the key, start with model `deepseek-flash`, and select **保存本地设置** (save local settings). Check model availability in the [official documentation](https://api-docs.deepseek.com/) and your account.
4. “Configured” means saved. Use **生成分析** (generate analysis) in the AI outlook panel and verify a successful response to test the actual connection. Calls consume API credit.
5. For events, collect public events before selecting DeepSeek extraction. For model AI, choose an AI mode for the relevant model in Model Workshop. Saving a key does not start AUTO.

This connection uses `https://api.deepseek.com/chat/completions`. Version 1.0 has no custom Base URL here and cannot use another provider's key in this field. AI calls consume API credit in PAPER mode too.

#### 8. Local credential storage

Storage is local. A legacy `readonly` filename does not determine trading permissions. **Configuration files are not an encrypted vault.**

| Connection | Storage |
| --- | --- |
| DeepSeek | `.env` |
| Binance spot / futures | `runtime/binance_readonly.json` / `runtime/binance_futures_readonly.json` |
| CoinW spot / futures | `runtime/coinw_spot_readonly.json` / `runtime/coinw_readonly.json` |
| Hyperliquid testnet agent key | Backend process memory; restart requires new authorization. |

Personal credentials are excluded from the repository and release. The ignore rules cover `.env` and `runtime`. Check logs, screenshots, backups and repackaged folders before sharing them. **清除本机连接** removes the local Binance connection; revoke keys through the respective provider.

#### 9. Troubleshooting

| Symptom | What to check |
| --- | --- |
| Charts work, account disconnected | Check LIVE startup, selected exchange/market and the credentials for that market. |
| Binance `-2015` | Key, mainnet/testnet, public-IP allowlist and permissions. |
| Binance `-1022` / `-1021` | Matching Secret and HMAC type / Windows clock synchronization, respectively. |
| Readable account, orders locked | Confirmed trading permission, local limits, exchange filters and available balances. |
| CoinW `6003` / `6001` / `29001` | Market permissions / matching key and signature / rate limits; see [official errors](https://www.coinw.com/api-doc/en/common/error-codes). |
| Hyperliquid zero balance / agent unavailable | Check selected network, actual account address and perpetual margin; authorization requires testnet and a compatible connected wallet. |
| DeepSeek `401` / `402` / `429` | Key / credit / request rate or concurrency, respectively; also check the model name. |
| Order timed out or outcome unknown | Reconcile the original confirmation ID with exchange orders and local receipts before proceeding. |

See [Binance error codes](https://developers.binance.com/en/docs/products/spot/errors) and [DeepSeek error codes](https://api-docs.deepseek.com/quick_start/error_codes/).

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
-  Improve the transfer between API contracts and spot transactions, as well as the transfer between cold wallets.
- Improve the trading robots.

## Third-party components

Bundled components retain their own licenses and notices. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). No project-wide license has been selected for this initial publication.
