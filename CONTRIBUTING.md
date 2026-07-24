# Contributing to PaperInfo

感谢你对 PaperInfo 的关注。Issue 和 Pull Request 都很欢迎。

## 开始之前

1. 搜索现有 Issue，避免重复问题。
2. 对较大的功能或架构调整，建议先创建 Issue 讨论范围。
3. 安全问题请按 `SECURITY.md` 私下报告。

## 开发环境

```bash
git clone https://github.com/JustNSim/PaperInfo.git
cd PaperInfo
python -m venv venv
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

复制 `.env.example` 为 `.env`。普通单元测试不需要真实 API Key。

## 运行测试

```bash
python -m unittest discover -s tests -v
python -m ruff check .
```

测试导入应用时不会启动 APScheduler。请继续保持测试离线、可重复，不要让默认测试调用付费 API 或真实第三方服务。

## 提交要求

- 保持改动聚焦，一个 Pull Request 解决一个问题。
- 新功能和 Bug 修复应补充测试。
- 不要提交密钥、个人数据库、日志、缓存、PDF 或第三方受限内容。
- 用户可见行为发生变化时同步更新 README 或相关文档。
- 提交信息建议采用 `feat:`、`fix:`、`docs:`、`test:`、`refactor:`、`chore:` 等前缀。

## Pull Request 检查

提交前确认：

- [ ] 单元测试通过
- [ ] Ruff 检查通过
- [ ] 没有新增敏感信息或生成文件
- [ ] 文档与行为保持一致
- [ ] 破坏性变更已明确说明
