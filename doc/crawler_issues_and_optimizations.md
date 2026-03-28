# 论文抓取逻辑审查与优化建议

> 创建日期: 2026-03-28
> 审查范围: arXiv 爬虫、DBLP 爬虫、调度逻辑、配置参数

本文档记录 PaperInfo 论文抓取逻辑中发现的影响精确性和广泛性的问题，以及对应的优化建议。

---

## 问题优先级说明

- **P0 (严重)**: 严重影响功能或数据质量，必须尽快修复
- **P1 (重要)**: 显著影响数据覆盖率，应优先处理
- **P2 (次要)**: 有改善空间，但不影响核心功能

---

## 🔴 P0 - 严重问题

### P0-1: DBLP Venue 过滤在客户端执行

**文件**: `crawler/dblp.py:92-100`

**问题描述**:
venue 过滤在解析阶段进行，而不是在 API 请求时指定。API 返回的结果中很大一部分会被客户端过滤掉。

```python
# 当前实现：先获取所有结果，再过滤
if venues:
    venue_match = any(v.lower() in venue.lower() for v in venues)
    if not venue_match and venue:
        return None  # 丢弃不匹配的结果
```

**影响**:
- 如果指定了 20 个 CCF venues，API 返回的 100 篇论文可能大部分被过滤掉
- 最终可能只获得 10-20 篇论文，远少于 `MAX_PAPERS_PER_SOURCE = 100`
- 浪费网络请求和 API 配额

**DBLP API 支持的参数**:
```
?q=smart+contract&venue=IEEE+S&P  # 服务端过滤
```

**建议方案**:
1. 为每个 venue 分别发送请求，或者
2. 使用 DBLP API 的 venue 参数进行服务端过滤
3. 提高每 venue 的 max_results，然后汇总去重

**预计工作量**: 1-2 小时
**状态**: ⏸️ 待修复

---

### P0-2: 缺少时间范围控制

**文件**: `crawler/arxiv.py:60-74`, `crawler/dblp.py:42-47`

**问题描述**:
arXiv 和 DBLP 都没有时间范围过滤参数。

**arXiv 当前实现**:
```python
params = {
    'search_query': query,
    'start': 0,
    'max_results': max_results,
    'sortBy': 'submittedDate',  # 按提交时间排序
    'sortOrder': 'descending'    # 最新的在前
}
```

**DBLP 当前实现**: 完全没有时间过滤参数

**影响**:
- 首次运行时无法获取历史论文
- 每次都获取相同的"最新"论文
- 无法进行增量更新（只获取上次更新后的新论文）
- 无法回溯补充指定时间范围的数据

**建议方案**:

**arXiv API 支持**:
```python
# 按日期过滤
from_date = last_update_date.strftime('%Y%m%d')
to_date = datetime.now().strftime('%Y%m%d')
params.update({
    'submit-date': f'[{from_date} TO {to_date}]'
})
```

**DBLP API 支持**:
```python
# 添加年份参数
params.update({
    'year': '2023-2024'  # 指定年份范围
})
```

**配置选项**:
```python
# config.py
FETCH_DAYS_BACK = 30        # 首次运行获取最近 N 天的论文
INCREMENTAL_UPDATE = True   # 是否启用增量更新
```

**预计工作量**: 2 小时
**状态**: ⏸️ 待修复

---

### P0-3: 关键词数量限制导致遗漏

**文件**: `crawler/arxiv.py:21-44`, `config.py:39-55`

**问题描述**:
arXiv 爬虫限制只使用前 10 个关键词，但配置中有 35 个关键词。

```python
MAX_KEYWORDS = 10
effective_keywords = keywords[:self.MAX_KEYWORDS]  # 只使用前 10 个
```

**被遗漏的关键词** (11-35):
- `defi`, `decentralized finance`
- `nft`, `non-fungible token`
- `dao`, `decentralized autonomous organization`
- `zero-knowledge`, `zk-snark`, `zk-stark`
- `merkle tree`, `hash function`
- `byzantine fault`, `pbft`
- `proof of work`, `proof of stake`
- `sharding`, `layer 2`, `rollup`, `sidechain`
- `smart contract security`, `reentrancy`, `flash loan attack`
- `51% attack`, `double spending`, `selfish mining`

**影响**:
- 重要技术术语无法被搜索
- DeFi、NFT、DAO 等热门领域论文遗漏
- 安全相关论文覆盖不全

**建议方案**:

**方案 A - 分批查询**:
```python
BATCH_SIZE = 10
all_papers = []
for i in range(0, len(keywords), BATCH_SIZE):
    batch = keywords[i:i+BATCH_SIZE]
    papers = arxiv_crawler.search(keywords=batch)
    all_papers.extend(papers)
```

**方案 B - 提高限制并优化查询**:
```python
MAX_KEYWORDS = 20  # 提高到 20
# 使用更精确的查询语法
query = ' OR '.join([f'all:"{kw}"' for kw in keywords])
```

**方案 C - 多次查询去重**:
```python
# 按优先级分组查询
high_priority = ['blockchain', 'smart contract', 'decentralized']
medium_priority = ['bitcoin', 'ethereum', 'defi', 'nft']
security_keywords = ['zero-knowledge', 'reentrancy', 'flash loan attack']
```

**预计工作量**: 1.5 小时
**状态**: ⏸️ 待修复

---

## 🟡 P1 - 重要问题

### P1-1: 只获取第一页结果

**文件**: `crawler/arxiv.py:66-74`, `crawler/dblp.py:42-47`

**问题描述**:
两个爬虫都只获取第一页结果，没有分页逻辑。

**arXiv**:
```python
params = {
    'start': 0,           # 固定从 0 开始
    'max_results': 100    # arXiv API 最多支持 1000，但只用 100
}
```

**DBLP**:
```python
params = {
    'h': 100,  # 每页结果数
    'c': 0     # 固定从第 0 条开始
}
```

**影响**:
- 如果关键词匹配的论文超过 100 篇，只能获取前 100 篇
- 对于热门领域（如 blockchain），可能遗漏大量相关论文
- arXiv API 支持分页但未使用

**建议方案**:

**arXiv 分页**:
```python
MAX_TOTAL_RESULTS = 500  # 最多获取 500 篇
RESULTS_PER_PAGE = 100
all_papers = []

for start in range(0, MAX_TOTAL_RESULTS, RESULTS_PER_PAGE):
    params = {
        'search_query': query,
        'start': start,
        'max_results': RESULTS_PER_PAGE
    }
    papers = self._fetch_page(params)
    all_papers.extend(papers)
    if not papers:
        break  # 没有更多结果
```

**DBLP 分页**:
```python
params = {
    'q': query,
    'h': 100,
    'c': 0,  # 第一页
    'format': 'json'
}
# 获取第二页: c=100, 第三页: c=200
```

**预计工作量**: 1 小时
**状态**: ⏸️ 待修复

---

### P1-2: DBLP 组合查询可能返回不相关结果

**文件**: `crawler/dblp.py:38-40`

**问题描述**:
使用 OR 逻辑组合所有关键词，会匹配任何一个关键词，可能返回不相关论文。

```python
query = ' OR '.join([f'"{kw}"' for kw in keywords])
# 结果："blockchain" OR "smart contract" OR "decentralized" OR ...
```

**影响**:
- "bitcoin" 可能匹配到仅在例子中提及比特币的论文
- 没有相关性评分过滤
- 可能获取大量低相关性论文

**建议方案**:

**方案 A - 限制结果并添加相关性过滤**:
```python
# 获取更多结果，然后过滤
params = {'h': max_results * 2}  # 获取 2 倍结果
# 在客户端根据关键词出现频率过滤
```

**方案 B - 使用 AND 逻辑组合核心关键词**:
```python
# 前 3 个核心关键词用 AND
core_query = ' AND '.join([f'"{kw}"' for kw in keywords[:3]])
# 其他关键词用 OR
optional_query = ' OR '.join([f'"{kw}"' for kw in keywords[3:]])
query = f'({core_query}) OR ({optional_query})'
```

**方案 C - 分别查询核心和扩展关键词**:
```python
# 核心关键词（必须匹配）
core_keywords = ['blockchain', 'smart contract']
# 扩展关键词（可选）
optional_keywords = [...]
```

**预计工作量**: 1 小时
**状态**: ⏸️ 待修复

---

### P1-3: 配置中存在无效的 arXiv 分类

**文件**: `config.py:66`

**问题描述**:
配置中使用了不存在的 arXiv 分类。

```python
'cs.DL'   # Digital Libraries - 不存在
```

**arXiv 有效分类** (部分):
- `cs.CR` - Cryptography and Security ✓
- `cs.DC` - Distributed, Parallel, and Cluster Computing ✓
- `cs.Distributed` - Distributed Computing ✓ (但不是独立分类，属于 DC 的子类)
- `cs.GT` - Computer Science and Game Theory ✓
- `cs.CE` - Computational Engineering, Finance, and Science ✓
- `cs.DB` - Databases ✓
- `cs.AI` - Artificial Intelligence ✓
- `cs.LG` - Machine Learning ✓

**影响**:
- 查询时可能返回错误或无效结果
- `cs.Distributed` 可能无法正常工作

**建议方案**:
```python
# 移除无效分类
'arxiv_categories': [
    'cs.CR',      # Cryptography and Security
    'cs.DC',      # Distributed, Parallel, and Cluster Computing
    'cs.GT',      # Computer Science and Game Theory
    'cs.CE',      # Computational Engineering, Finance, and Science
    'cs.DB',      # Databases
    'cs.SC',      # Symbolic Computation (新增)
    'cs.IT'       # Information Theory (新增)
]
```

**预计工作量**: 15 分钟
**状态**: ⏸️ 待修复

---

## 🟢 P2 - 次要问题

### P2-1: 去重基于标题+来源，无法识别版本差异

**文件**: `models.py:107-108`

**问题描述**:
arXiv 论文有版本号（v1, v2, v3），标题相同但内容不同。当前去重策略会覆盖旧版本。

```python
db.UniqueConstraint('source', 'title', name='uq_source_title')
```

**影响**:
- arXiv 论文更新后，新版本会覆盖旧版本
- 无法保留论文的版本历史
- 可能丢失重要信息（如 v1 和 v5 有重大差异）

**建议方案**:

**方案 A - 包含版本号**:
```python
class Paper(db.Model):
    version = db.Column(db.String(10))  # arXiv 版本号
    __table_args__ = (
        db.UniqueConstraint('source', 'source_id', 'version', name='uq_paper_version'),
    )
```

**方案 B - 保留最新版本**:
```python
# 当前策略，但添加日志
logger.info(f"论文已存在，更新到新版本: {paper.title}")
```

**方案 C - 保留所有版本**:
```python
# 允许重复，通过 source_id + version 组合去重
```

**预计工作量**: 1 小时
**状态**: ⏸️ 技术债务（需要数据库变更）

---

### P2-2: arXiv 分类查询过于宽泛

**文件**: `config.py:56-67`, `crawler/arxiv.py:49-54`

**问题描述**:
使用了 9 个 arXiv 分类，部分与区块链智能合约关联度较低。

```python
'arxiv_categories': [
    'cs.CR',      # 密码学 - 高相关 ✓
    'cs.DC',      # 分布式计算 - 高相关 ✓
    'cs.Distributed',  # (DC 的子类，重复)
    'cs.GT',      # 博弈论 - 中相关 (共识机制)
    'cs.CE',      # 计算金融 - 中相关 (DeFi)
    'cs.DB',      # 数据库 - 低相关
    'cs.AI',      # 人工智能 - 低相关
    'cs.LG',      # 机器学习 - 低相关
    'cs.DL'       # 不存在
]
```

**影响**:
- `cs.AI` 和 `cs.LG` 可能返回大量不相关论文
- 查询范围过广导致结果质量下降

**建议方案**:
```python
# 精简到核心分类
'arxiv_categories': [
    'cs.CR',   # Cryptography and Security
    'cs.DC',   # Distributed, Parallel, and Cluster Computing
    'cs.SC',   # Symbolic Computation
    'cs.IT'    # Information Theory
]
```

**预计工作量**: 15 分钟
**状态**: ⏸️ 待修复

---

### P2-3: 缺少结果质量评分

**问题描述**:
没有对获取的论文进行相关性评分或排序。

**建议方案**:
```python
def calculate_relevance_score(paper, keywords):
    """计算论文与关键词的相关性分数"""
    score = 0
    title_lower = paper['title'].lower()
    abstract_lower = (paper.get('abstract') or '').lower()

    for kw in keywords:
        kw_lower = kw.lower()
        # 标题匹配权重更高
        if kw_lower in title_lower:
            score += 10
        # 摘要匹配
        if kw_lower in abstract_lower:
            score += 3

    return score

# 按相关性排序
papers_with_score = [(p, calculate_relevance_score(p, keywords)) for p in all_papers]
papers_with_score.sort(key=lambda x: x[1], reverse=True)
```

**预计工作量**: 1 小时
**状态**: ⏸️ 待修复

---

## 优化路线图

### Phase 1: 紧急修复 (P0)

| ID | 问题 | 预计时间 | 优先级 |
|----|------|----------|--------|
| P0-1 | DBLP venue 服务端过滤 | 1-2 小时 | 🔴 最高 |
| P0-2 | 添加时间范围控制 | 2 小时 | 🔴 最高 |
| P0-3 | 关键词分批查询 | 1.5 小时 | 🔴 高 |

### Phase 2: 重要优化 (P1)

| ID | 问题 | 预计时间 | 优先级 |
|----|------|----------|--------|
| P1-1 | 分页获取结果 | 1 小时 | 🟡 高 |
| P1-2 | DBLP 查询优化 | 1 小时 | 🟡 中 |
| P1-3 | 移除无效分类 | 15 分钟 | 🟡 中 |

### Phase 3: 增强功能 (P2)

| ID | 问题 | 预计时间 | 优先级 |
|----|------|----------|--------|
| P2-1 | 版本号管理 | 1 小时 | 🟢 低 |
| P2-2 | 精简 arXiv 分类 | 15 分钟 | 🟢 低 |
| P2-3 | 相关性评分 | 1 小时 | 🟢 低 |

**总预计工作量**: 约 10-12 小时

---

## 配置增强建议

建议添加以下配置选项到 `config.py`:

```python
# 抓取范围配置
FETCH_DAYS_BACK = 30            # 首次运行获取最近 N 天的论文
INCREMENTAL_UPDATE = True       # 是否启用增量更新
MAX_TOTAL_RESULTS = 500         # 每个数据源最多获取的论文总数
KEYWORD_BATCH_SIZE = 10         # 每批查询的关键词数量

# 质量控制
MIN_RELEVANCE_SCORE = 5         # 最低相关性分数（如果启用评分）
ENABLE_VERSION_TRACKING = True  # 是否跟踪 arXiv 版本

# DBLP 特定配置
DBLP_QUERY_PER_VENUE = True     # 是否按 venue 分别查询
DBLP_RESULTS_PER_VENUE = 50     # 每个 venue 获取的论文数
```

---

## 参考文档

- [arXiv API 文档](https://info.arxiv.org/help/api/index.html)
- [DBLP API 文档](https://dblp.org/faq/1350131.html)
- [arXiv 分类列表](https://arxiv.org/category_taxonomy)
