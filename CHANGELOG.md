# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 的结构，并从 `v0.1.0` 开始记录公开版本。

## [Unreleased]

### Planned

- 持续改进公开部署安全边界和可配置性
- 增加更多数据源与集成测试
- 引入正式数据库迁移工具

## [0.1.0] - 2026-07-24

### Added

- arXiv 与 DBLP 论文抓取、去重和增量更新
- LLM 相关性/价值双评分及主备服务降级
- 学术翻译、作者单位/摘要/PDF 元数据补全
- 收藏、历史、统计、更新日志和飞书通知
- CSV、XLSX、BibTeX、PDF/ZIP 与本地 Zotero 导出
- Windows 任务计划程序后台运行和漏跑补执行
- 开源文档、社区治理文件和持续集成

### Security

- 默认仅监听 `127.0.0.1`
- 默认关闭 Flask Debug
- 使用随机临时 Secret Key 代替固定开发密钥
- 为修改型 API 增加会话级 CSRF 校验

[Unreleased]: https://github.com/JustNSim/PaperInfo/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/JustNSim/PaperInfo/releases/tag/v0.1.0
