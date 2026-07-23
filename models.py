"""
PaperInfo 数据库模型
"""
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from datetime import datetime, timezone, timedelta

db = SQLAlchemy()


def get_beijing_time():
    """获取当前北京时间 (UTC+8)"""
    beijing_tz = timezone(timedelta(hours=8))
    return datetime.now(beijing_tz).replace(tzinfo=None)


class Domain(db.Model):
    """研究领域模型"""
    __tablename__ = 'domains'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False, index=True)
    keywords = db.Column(db.JSON, default=list, nullable=False)
    arxiv_categories = db.Column(db.JSON, default=list)
    ccf_venues = db.Column(db.JSON, default=list)
    enabled = db.Column(db.Boolean, default=True, index=True)
    llm_prompt = db.Column(db.Text, nullable=True)  # 领域专用的LLM评估prompt
    created_at = db.Column(db.DateTime, default=get_beijing_time)
    updated_at = db.Column(db.DateTime, default=get_beijing_time, onupdate=get_beijing_time)

    # 关联论文
    papers = db.relationship('Paper', backref='domain', lazy='dynamic', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Domain {self.name}>'

    def to_dict(self):
        """转换为字典格式"""
        return {
            'id': self.id,
            'name': self.name,
            'keywords': self.keywords,
            'arxiv_categories': self.arxiv_categories,
            'ccf_venues': self.ccf_venues,
            'enabled': self.enabled,
            'llm_prompt': self.llm_prompt,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'paper_count': self.papers.count()
        }


class UpdateLog(db.Model):
    """更新日志模型"""
    __tablename__ = 'update_logs'

    id = db.Column(db.Integer, primary_key=True)
    trigger_type = db.Column(db.String(20), nullable=False)  # scheduled/manual/catch_up/rescore 等
    trigger_time = db.Column(db.DateTime, default=get_beijing_time, index=True)

    # 更新结果统计
    total_new = db.Column(db.Integer, default=0)  # 新增论文总数
    arxiv_new = db.Column(db.Integer, default=0)  # arXiv 新增数量
    dblp_new = db.Column(db.Integer, default=0)   # DBLP 新增数量
    llm_filtered = db.Column(db.Integer, default=0)  # LLM 过滤掉的论文数

    # 按来源统计（JSON格式存储）
    source_stats = db.Column(db.JSON, default=dict)  # {'arxiv': 10, 'dblp': 5}

    # 处理的领域
    domains_processed = db.Column(db.JSON, default=list)  # [1, 2, 3]

    # 状态
    status = db.Column(db.String(20), default='success')  # 'success', 'failed', 'partial'
    error_message = db.Column(db.Text)  # 错误信息

    # 扩展字段（用于不同类型的操作）
    operation_details = db.Column(db.JSON, default=dict)  # 存储操作的额外详情
    papers_affected = db.Column(db.Integer, default=0)  # 受影响的论文数（用于rescore、delete等）

    def __repr__(self):
        return f'<UpdateLog {self.trigger_time} - {self.trigger_type} - {self.status}>'

    def to_dict(self):
        """转换为字典格式"""
        return {
            'id': self.id,
            'trigger_type': self.trigger_type,
            'trigger_time': self.trigger_time.isoformat() if self.trigger_time else None,
            'total_new': self.total_new,
            'arxiv_new': self.arxiv_new,
            'dblp_new': self.dblp_new,
            'llm_filtered': self.llm_filtered,
            'source_stats': self.source_stats,
            'domains_processed': self.domains_processed,
            'status': self.status,
            'error_message': self.error_message,
            'operation_details': self.operation_details,
            'papers_affected': self.papers_affected
        }


class Paper(db.Model):
    """论文模型"""
    __tablename__ = 'papers'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False, index=True)
    authors = db.Column(db.JSON, default=list, nullable=False)
    author_affiliations = db.Column(db.JSON, default=list)
    affiliations_source = db.Column(db.String(30))
    affiliations_status = db.Column(db.String(20))
    affiliations_fetched_at = db.Column(db.DateTime)
    abstract = db.Column(db.Text)
    abstract_source = db.Column(db.String(30))
    source = db.Column(db.String(50), nullable=False, index=True)  # arxiv, dblp, ccs等
    source_id = db.Column(db.String(100), index=True)  # 原始数据源的ID
    doi = db.Column(db.String(300))
    year = db.Column(db.Integer, index=True)
    venue = db.Column(db.String(200))  # 会议/期刊名称
    url = db.Column(db.String(500))
    pdf_url = db.Column(db.String(500))
    pdf_source = db.Column(db.String(30))
    metadata_fetched_at = db.Column(db.DateTime)
    metadata_retry_count = db.Column(db.Integer, default=0)
    metadata_next_retry_at = db.Column(db.DateTime, index=True)
    published_date = db.Column(db.DateTime, index=True)
    fetched_date = db.Column(db.DateTime, default=get_beijing_time, index=True)
    is_favorite = db.Column(db.Boolean, default=False, index=True)  # 是否收藏
    llm_score = db.Column(db.Integer, nullable=True)  # LLM 相关度评分 (0-100)
    llm_value_score = db.Column(db.Integer, nullable=True)  # LLM 价值评分 (0-100)

    # 外键关联
    domain_id = db.Column(db.Integer, db.ForeignKey('domains.id'), nullable=False, index=True)

    # 唯一约束：同一数据源下标题唯一
    __table_args__ = (
        db.UniqueConstraint('source', 'title', name='uq_source_title'),
        db.Index('idx_source_date', 'source', 'published_date'),
        db.Index('idx_favorite_date', 'is_favorite', 'published_date'),
        db.Index('idx_domain_source', 'domain_id', 'source'),
        db.Index('idx_year_source', 'year', 'source'),
    )

    def __repr__(self):
        return f'<Paper {self.title[:50]}>'

    def to_dict(self):
        """转换为字典格式"""
        return {
            'id': self.id,
            'title': self.title,
            'authors': self.authors,
            'author_affiliations': self.author_affiliations or [],
            'affiliations': self.affiliation_names,
            'affiliations_source': self.affiliations_source,
            'affiliations_status': self.affiliations_status,
            'affiliations_fetched_at': (
                self.affiliations_fetched_at.isoformat()
                if self.affiliations_fetched_at else None
            ),
            'abstract': self.abstract,
            'abstract_source': self.abstract_source,
            'source': self.source,
            'source_id': self.source_id,
            'doi': self.doi,
            'year': self.year,
            'venue': self.venue,
            'url': self.url,
            'pdf_url': self.pdf_url,
            'pdf_source': self.pdf_source,
            'metadata_fetched_at': (
                self.metadata_fetched_at.isoformat()
                if self.metadata_fetched_at else None
            ),
            'metadata_retry_count': self.metadata_retry_count or 0,
            'metadata_next_retry_at': (
                self.metadata_next_retry_at.isoformat()
                if self.metadata_next_retry_at else None
            ),
            'published_date': self.published_date.isoformat() if self.published_date else None,
            'fetched_date': self.fetched_date.isoformat() if self.fetched_date else None,
            'domain_id': self.domain_id,
            'is_favorite': self.is_favorite,
            'llm_score': self.llm_score,
            'llm_value_score': self.llm_value_score
        }

    @property
    def affiliation_names(self):
        """Return unique institution names while preserving author order."""
        names = []
        seen = set()
        for author in self.author_affiliations or []:
            for affiliation in author.get('affiliations', []) or []:
                normalized = str(affiliation).strip()
                key = normalized.casefold()
                if normalized and key not in seen:
                    seen.add(key)
                    names.append(normalized)
        return names

    @staticmethod
    def exists_by_source_and_title(source: str, title: str) -> bool:
        """检查指定数据源下是否已存在该标题的论文"""
        return db.session.query(
            db.exists().where(
                db.and_(Paper.source == source, Paper.title == title)
            )
        ).scalar()


def ensure_paper_schema():
    """Add affiliation columns to existing SQLite databases without data loss."""
    if 'papers' not in inspect(db.engine).get_table_names():
        return
    existing = {column['name'] for column in inspect(db.engine).get_columns('papers')}
    additions = {
        'author_affiliations': 'ALTER TABLE papers ADD COLUMN author_affiliations JSON',
        'affiliations_source': 'ALTER TABLE papers ADD COLUMN affiliations_source VARCHAR(30)',
        'affiliations_status': 'ALTER TABLE papers ADD COLUMN affiliations_status VARCHAR(20)',
        'affiliations_fetched_at': 'ALTER TABLE papers ADD COLUMN affiliations_fetched_at DATETIME',
        'doi': 'ALTER TABLE papers ADD COLUMN doi VARCHAR(300)',
        'abstract_source': 'ALTER TABLE papers ADD COLUMN abstract_source VARCHAR(30)',
        'pdf_source': 'ALTER TABLE papers ADD COLUMN pdf_source VARCHAR(30)',
        'metadata_fetched_at': 'ALTER TABLE papers ADD COLUMN metadata_fetched_at DATETIME',
        'metadata_retry_count': 'ALTER TABLE papers ADD COLUMN metadata_retry_count INTEGER DEFAULT 0',
        'metadata_next_retry_at': 'ALTER TABLE papers ADD COLUMN metadata_next_retry_at DATETIME',
    }
    with db.engine.begin() as connection:
        for column, statement in additions.items():
            if column not in existing:
                connection.execute(text(statement))
        connection.execute(text(
            'CREATE INDEX IF NOT EXISTS idx_papers_affiliations_status '
            'ON papers (affiliations_status)'
        ))
        connection.execute(text(
            'CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers (doi)'
        ))
        connection.execute(text(
            'CREATE INDEX IF NOT EXISTS idx_papers_metadata_next_retry_at '
            'ON papers (metadata_next_retry_at)'
        ))
        if {'source', 'abstract'}.issubset(existing):
            connection.execute(text(
                "UPDATE papers SET abstract_source = source "
                "WHERE abstract_source IS NULL AND abstract IS NOT NULL "
                "AND trim(abstract) <> ''"
            ))
        if {'source', 'pdf_url'}.issubset(existing):
            connection.execute(text(
                "UPDATE papers SET pdf_source = source "
                "WHERE pdf_source IS NULL AND pdf_url IS NOT NULL "
                "AND trim(pdf_url) <> ''"
            ))
        connection.execute(text(
            'UPDATE papers SET metadata_retry_count = 0 '
            'WHERE metadata_retry_count IS NULL'
        ))


class ReadHistory(db.Model):
    """论文点击/阅读历史（每篇论文一行，upsert 更新）"""
    __tablename__ = 'read_history'

    id = db.Column(db.Integer, primary_key=True)
    paper_id = db.Column(db.Integer, db.ForeignKey('papers.id'), unique=True, nullable=False, index=True)
    read_count = db.Column(db.Integer, default=1)  # 累计点击次数
    last_read_at = db.Column(db.DateTime, default=get_beijing_time, index=True)  # 最近点击时间

    # 关联论文
    paper = db.relationship('Paper', backref=db.backref('read_history', uselist=False))

    def __repr__(self):
        return f'<ReadHistory paper={self.paper_id} count={self.read_count}>'
