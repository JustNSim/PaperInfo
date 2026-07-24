# PaperInfo

[![Tests](https://github.com/JustNSim/PaperInfo/actions/workflows/tests.yml/badge.svg)](https://github.com/JustNSim/PaperInfo/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

PaperInfo 是一套本地优先的论文情报与阅读管理工具。它围绕用户配置的研究领域，从 arXiv 和 DBLP 持续发现论文，使用大语言模型完成相关性与学术价值初筛，并把翻译、收藏、统计、PDF 下载和 Zotero 导出串成一条完整工作流。

> 项目当前定位为单用户本地工具。默认只监听 `127.0.0.1`，请勿未经鉴权直接暴露到公网。

## 主要功能

- 按研究领域配置关键词、arXiv 分类、DBLP 会场和专属 LLM Prompt
- 自动或手动抓取 arXiv、DBLP 论文，支持增量时间窗口
- 规则初筛、数据库去重、LLM 相关性/价值双评分
- 标题、摘要、会议搜索，以及领域、来源、年份、批次组合筛选
- Azure Translator、DeepSeek、MyMemory 多级翻译降级
- 作者单位、摘要和公开 PDF 地址的异步元数据补全
- 收藏、阅读历史、更新日志和统计图表
- CSV、Excel、BibTeX、PDF/ZIP 批量导出
- 写入本地 Zotero 资料库或分类，并尝试附加全文 PDF
- Windows 后台自启动、错过任务补执行和飞书更新通知

完整功能介绍见 [项目主要功能总结与介绍](doc/project_features_overview.md)。

## 工作流

```text
领域配置
  → arXiv / DBLP 抓取
  → 时间、分类、会场和关键词过滤
  → 去重 + LLM 双维度评分
  → SQLite 持久化 + 异步元数据补全
  → 搜索 / 翻译 / 收藏 / 阅读
  → PDF / BibTeX / Excel / Zotero
```

## 环境要求

- Python 3.11 或更高版本
- Windows、Linux 或 macOS
- 可访问 arXiv、DBLP 及所选外部服务的网络
- 可选：Zotero 桌面端

Windows 任务计划程序相关脚本仅适用于 Windows；Web 应用和核心测试可跨平台运行。

## 快速开始

```bash
git clone https://github.com/JustNSim/PaperInfo.git
cd PaperInfo
python -m venv venv
```

Windows PowerShell：

```powershell
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

Linux/macOS：

```bash
source venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python app.py
```

然后访问 <http://127.0.0.1:5000>。

首次启动会自动创建 `data/papers.db`，并初始化两个示例研究领域。数据库、日志、缓存、运行锁和 `.env` 都不会提交到 Git。

## 配置层级

PaperInfo 可以在不同配置程度下运行：

| 配置程度 | 可用能力 |
| --- | --- |
| 无外部 Key | Web 界面、arXiv/DBLP 抓取、筛选、收藏、历史、统计、导出；LLM 初始化失败时论文将不经过模型过滤 |
| 配置一个 LLM | 论文相关性/价值评分；也可作为翻译后备服务 |
| 配置 Azure Translator | 优先使用 Azure 进行学术文本翻译 |
| 配置 OpenAlex/Unpaywall | 补全作者单位、摘要和公开 PDF |
| 配置飞书机器人 | 更新完成后推送摘要和优先论文 |
| 运行 Zotero 桌面端 | 将题录和 PDF 保存到本地 Zotero |

复制 `.env.example` 后按需填写。密钥不要写入代码或提交到仓库；推荐将敏感 Key 存入操作系统环境变量。

常用配置：

```dotenv
SECRET_KEY=
PAPERINFO_HOST=127.0.0.1
PAPERINFO_PORT=5000
FLASK_DEBUG=0

LLM_FILTER_ENABLED=false
LLM_PROVIDER=openai
LLM_RELEVANCE_THRESHOLD=30
LLM_VALUE_THRESHOLD=30
```

如果需要局域网访问，可显式设置 `PAPERINFO_HOST=0.0.0.0`，同时应配置强随机 `SECRET_KEY`，并使用操作系统防火墙限制访问范围。当前版本没有多用户登录系统，不适合直接公网部署。

## 可选依赖

基础依赖安装自 `requirements.txt`。如果使用 Anthropic 或智谱 SDK：

```bash
python -m pip install -r requirements-optional.txt
```

不同 LLM 提供商只会在被选中时加载相应 SDK。

## 测试

项目使用 Python 标准库 `unittest`，普通测试不会调用真实外部服务：

```bash
python -m unittest discover -s tests -v
```

真实 DBLP 冒烟测试默认跳过，需要显式设置：

```powershell
$env:RUN_DBLP_LIVE_TESTS = "1"
python -m unittest tests.test_dblp_live -v
```

LLM 连通性检查是手动集成测试：

```bash
python scripts/check_llm_connectivity.py
```

## Windows 后台运行

以管理员身份打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
```

常用维护命令：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\restart_server.ps1
powershell -ExecutionPolicy Bypass -File scripts\uninstall_autostart.ps1
Get-ScheduledTask PaperInfo
```

后台入口为 `run_server.py`，始终关闭 Flask Debug 和 reloader。

## 安全说明

- 默认仅监听本机回环地址。
- 修改数据的 Web API 使用会话级 CSRF Token。
- 项目不提供公网身份认证或多用户权限控制。
- `/api/clear`、批量删除和领域删除等接口具有破坏性，只应在可信本机环境使用。
- 外部 PDF、翻译、LLM 和元数据服务返回的内容均应视为不可信输入。
- 发现安全问题时，请按 [SECURITY.md](SECURITY.md) 私下报告，不要直接创建公开 Issue。

## 项目结构

| 路径 | 职责 |
| --- | --- |
| `app.py` | Flask 应用、页面和 API |
| `models.py` | 领域、论文、更新日志和阅读历史模型 |
| `crawler/` | arXiv/DBLP 抓取、重试、缓存和标准化 |
| `scheduler.py` | 定时任务、补执行、LLM 筛选和更新日志 |
| `llm/` | 多提供商评分和主备降级 |
| `affiliation_service.py` | 作者单位、摘要和 PDF 元数据补全 |
| `translation_service.py` | 学术翻译和服务降级 |
| `zotero_service.py` | 本地 Zotero 写入 |
| `templates/`、`static/` | Web 页面与交互 |
| `tests/` | 离线单元测试 |

## 参与贡献

欢迎提交 Issue 和 Pull Request。开始之前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## 数据源与第三方服务

PaperInfo 只保存检索和用户工作流所需的论文元数据。使用者应遵守 arXiv、DBLP、OpenAlex、Crossref、Unpaywall、翻译服务、LLM 服务和出版商网站各自的服务条款、速率限制与内容许可。

## 许可证

本项目代码使用 [MIT License](LICENSE) 开源。第三方服务、论文元数据和论文全文仍适用其各自的条款与版权。
