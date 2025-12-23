# <img width="40" height="40" alt="logo_app" src="https://github.com/user-attachments/assets/911ba846-a08b-4e3e-b119-ec1e78347288" style="vertical-align: middle;" /> Hyper Alpha Arena

[English](./README.md) | **简体中文**

> 一个**生产就绪**的开源 AI 交易平台，让任何人——**无论是否有编程经验**——都能部署自主的 LLM 驱动的加密货币交易策略。内置 AI 助手用于信号创建和提示词生成。支持 Hyperliquid DEX（测试网模拟交易和主网真实交易），计划集成 Binance 和 Aster DEX。**支持英文和中文。**

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![GitHub stars](https://img.shields.io/github/stars/HammerGPT/Hyper-Alpha-Arena)](https://github.com/HammerGPT/Hyper-Alpha-Arena/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/HammerGPT/Hyper-Alpha-Arena)](https://github.com/HammerGPT/Hyper-Alpha-Arena/network)
[![Community](https://img.shields.io/badge/Telegram-Community-blue?logo=telegram)](https://t.me/+RqxjT7Gttm9hOGEx)
[![English](https://img.shields.io/badge/Lang-English-blue)](https://www.akooi.com/docs/)
[![中文](https://img.shields.io/badge/语言-中文-red)](https://www.akooi.com/docs/zh/)

## 🔥 立即开始交易 - 最高 30% 手续费折扣

准备好让你的 AI 交易策略发挥作用了吗？从这些顶级交易所开始：

### 🚀 **Hyperliquid** - 去中心化永续合约交易所
- **无需 KYC** | **低手续费** | **高性能**
- 与 Hyper Alpha Arena 直接集成
- [**开启合约交易 →**](https://app.hyperliquid.xyz/join/HYPERSVIP)

### 💰 **Binance** - 全球最大交易所
- **30% 手续费折扣** | **高流动性** | **高级工具**
- [**注册享 30% 折扣 →**](https://accounts.maxweb.red/register?ref=HYPERVIP)

### ⚡ **Aster DEX** - 兼容 Binance 的 DEX
- **更低手续费** | **多链支持** | **API 钱包安全**
- [**立即注册 →**](https://www.asterdex.com/zh-CN/referral/2b5924)

---

## 概述

Hyper Alpha Arena 是一个生产就绪的 AI 交易平台，大型语言模型（LLM）可以自主执行加密货币交易策略。受 [nof1 Alpha Arena](https://nof1.ai) 启发，该平台使 GPT-5、Claude 和 Deepseek 等 AI 模型能够基于实时市场数据做出智能交易决策并自动执行交易。

**官方网站：** https://www.akooi.com/

## 适用人群

- **非技术交易者**：内置 AI 助手通过自然对话帮助你创建交易信号和策略提示词——无需编程
- **量化研究员**：在部署真实资金之前，使用真实市场数据在测试网上测试 LLM 驱动的策略
- **Hyperliquid 用户**：原生集成测试网（免费模拟交易）和主网（1-50 倍杠杆永续合约）
- **AI 爱好者**：在真实交易场景中实验不同的 LLM（GPT、Claude、Deepseek）竞争

**交易模式：**
- **Hyperliquid 测试网（模拟交易）**：无风险测试，具有真实市场机制、免费测试资金和真实订单簿——卓越的模拟交易体验
- **Hyperliquid 主网**：在去中心化永续合约交易所进行实盘交易，支持 1-50 倍杠杆（真实资金有风险）

## 功能特性

### 核心交易功能
- **多模型 LLM 支持**：兼容 OpenAI API 的模型（GPT-5、Claude、Deepseek 等）
- **多钱包架构**：每个 AI Trader 可以为测试网和主网配置独立钱包
- **全局交易模式**：集中式环境切换，同时影响所有 AI Trader
- **AI 提示词生成器**：用于生成优化交易策略提示词的交互式聊天界面
  - 通过自然语言对话定义交易目标和风险偏好
  - 自动生成包含技术指标的结构化提示词
  - 对话历史管理，支持标题编辑
  - 一键应用到 AI Trader 配置
- **提示词模板管理**：
  - 可自定义的 AI 交易提示词，带可视化编辑器
  - 账户特定的提示词绑定系统，包含 Hyperliquid 专用模板
  - 默认、专业版和 Hyperliquid 模板，包含杠杆教育
  - 未绑定账户自动回退到默认模板
- **技术分析集成**：11 个内置技术指标
  - 趋势：SMA、EMA、MACD
  - 动量：RSI、随机振荡器
  - 波动性：布林带、ATR
  - 成交量：OBV、VWAP
  - 支撑/阻力：枢轴点、斐波那契回撤
- **实时市场数据**：通过 ccxt 从多个交易所获取实时加密货币价格
- **信号触发交易**：定义激活 AI 分析的市场条件
  - 创建自定义信号：OI 变化、资金费率飙升、价格突破
  - AI 信号生成器：自然语言转信号配置
  - 与定时触发器结合，实现全面覆盖
- **AI Trader 管理**：创建和管理多个具有独立配置的 AI 交易代理

### Hyperliquid 交易功能
- **永续合约交易**：在 Hyperliquid DEX 上真实执行订单
  - 市价单和限价单，支持 1-50 倍杠杆
  - 多空仓位，自动计算强平价格
  - 全仓模式，实时监控保证金使用率
- **环境隔离**：测试网和主网严格分离
  - 每个环境独立的钱包配置
  - 环境感知缓存，使用 `(account_id, environment)` 复合键
  - API 调用隔离，防止跨环境数据污染
- **风险管理**：内置安全机制
  - 最大杠杆限制（每个账户可配置，1-50 倍）
  - 保证金使用率警报（80% 使用率时自动暂停交易）
  - 强平价格显示和警告
- **AI 驱动交易**：LLM 驱动的永续合约交易
  - 杠杆感知的 AI 提示词，包含风险管理教育
  - 基于市场信心的自动杠杆选择
  - 与现有 AI 决策引擎完全集成

## 截图

### 仪表板概览
![Dashboard Overview](screenshots/dashboard-overview.png)

### AI 提示词生成器
![AI Prompt Generator](screenshots/ai-prompt-generator.png)

### 技术分析
![Technical Analysis](screenshots/ai-technical-analysis.png)

### Trader 配置
![Trader Configuration](screenshots/trader-configuration.png)

## 快速开始

### 前置要求

- **Docker Desktop** ([下载](https://www.docker.com/products/docker-desktop))
  - Windows：Docker Desktop for Windows
  - macOS：Docker Desktop for Mac
  - Linux：Docker Engine ([安装指南](https://docs.docker.com/engine/install/))

### 安装

```bash
# 克隆仓库
git clone https://github.com/HammerGPT/Hyper-Alpha-Arena.git
cd Hyper-Alpha-Arena

# 启动应用（根据你的 Docker 版本选择一个命令）
docker compose up -d --build        # 适用于较新的 Docker Desktop（推荐）
# 或
docker-compose up -d --build       # 适用于较旧的 Docker 版本或独立的 docker-compose
```

应用将在 **http://localhost:8802** 可用

### 管理应用

```bash
# 查看日志
docker compose logs -f        # (或 docker-compose logs -f)

# 停止应用
docker compose down          # (或 docker-compose down)

# 重启应用
docker compose restart       # (或 docker-compose restart)

# 更新到最新版本
git pull origin main
docker compose up -d --build # (或 docker-compose up -d --build)
```

**重要提示：**
- 所有数据（数据库、配置、交易历史）都持久化在 Docker 卷中
- 停止/重启容器时数据会被保留
- 只有 `docker-compose down -v` 会删除数据（除非你想重置所有内容，否则不要使用 `-v` 标志）

## 首次设置

详细的设置说明包括：
- Hyperliquid 钱包配置（测试网和主网）
- AI Trader 创建和 LLM API 设置
- 交易环境和杠杆设置
- 信号触发交易配置

**📖 查看我们的完整指南：[入门指南](https://www.akooi.com/docs/guide/getting-started.html)**

## 支持的模型

Hyper Alpha Arena 支持任何兼容 OpenAI API 的语言模型。**为获得最佳效果，我们推荐使用 Deepseek**，因其在交易场景中具有成本效益和强大性能。

支持的模型包括：
- **Deepseek**（推荐）：交易决策的卓越性价比
- **OpenAI**：GPT-5 系列、o1 系列、GPT-4o、GPT-4
- **Anthropic**：Claude（通过兼容端点）
- **自定义 API**：任何兼容 OpenAI 的端点

平台自动处理模型特定的配置和参数差异。

## 故障排除

### 常见问题

**问题**：端口 8802 已被占用
**解决方案**：
```bash
docker-compose down
docker-compose up -d --build
```

**问题**：无法连接到 Docker 守护进程
**解决方案**：确保 Docker Desktop 正在运行

**问题**：数据库连接错误
**解决方案**：等待 PostgreSQL 容器健康（使用 `docker-compose ps` 检查）

**问题**：想要重置所有数据
**解决方案**：
```bash
docker-compose down -v  # 这将删除所有数据！
docker-compose up -d --build
```

## 贡献

我们欢迎社区贡献！你可以通过以下方式帮助：

- 报告 bug 和问题
- 建议新功能
- 提交 pull request
- 改进文档
- 在不同平台上测试

请 star 和 fork 此仓库以保持对开发进度的关注。

## 资源

### Hyperliquid
- 官方文档：https://hyperliquid.gitbook.io/
- Python SDK：https://github.com/hyperliquid-dex/hyperliquid-python-sdk
- 测试网：https://api.hyperliquid-testnet.xyz

### 原始项目
- Open Alpha Arena：https://github.com/etrobot/open-alpha-arena

## 社区与支持

**🌐 官方网站**：[https://www.akooi.com/](https://www.akooi.com/)

**🐦 在 Twitter/X 上联系我**：[@GptHammer3309](https://x.com/GptHammer3309)
- Hyper Alpha Arena 开发的最新动态
- AI 交易见解和策略讨论
- 技术支持和问答

加入我们的 [Telegram 群组](https://t.me/+RqxjT7Gttm9hOGEx) 进行实时讨论和快速响应：
- 报告 bug（请尽可能包含日志、截图和步骤）
- 分享策略见解或产品反馈
- 提醒我关注 PR/Issue，以便我快速响应

友情提醒：Telegram 用于快速沟通，但最终的跟踪和修复仍通过 GitHub Issues/Pull Requests 进行。切勿在聊天中发布 API 密钥或其他敏感数据。

## 许可证

本项目采用 Apache License 2.0 许可。详见 [LICENSE](LICENSE) 文件。

## 致谢

- **etrobot** - 原始 open-alpha-arena 项目
- **nof1.ai** - Alpha Arena 的灵感来源
- **Hyperliquid** - 去中心化永续合约交易平台
- **OpenAI、Anthropic、Deepseek** - LLM 提供商

---

Star 此仓库以关注开发进度。
