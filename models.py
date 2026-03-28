"""
PaperInfo 数据库模型
"""
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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
    trigger_type = db.Column(db.String(20), nullable=False)  # 'scheduled' 或 'manual'
    trigger_time = db.Column(db.DateTime, default=datetime.utcnow, index=True)

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

    def __repr__(self):
        return f'<UpdateLog {self.trigger_time} - {self.status}>'

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
            'error_message': self.error_message
        }


class Paper(db.Model):
    """论文模型"""
    __tablename__ = 'papers'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False, index=True)
    authors = db.Column(db.JSON, default=list, nullable=False)
    abstract = db.Column(db.Text)
    source = db.Column(db.String(50), nullable=False, index=True)  # arxiv, dblp, ccs等
    source_id = db.Column(db.String(100), index=True)  # 原始数据源的ID
    year = db.Column(db.Integer, index=True)
    venue = db.Column(db.String(200))  # 会议/期刊名称
    url = db.Column(db.String(500))
    pdf_url = db.Column(db.String(500))
    published_date = db.Column(db.DateTime, index=True)
    fetched_date = db.Column(db.DateTime, default=datetime.utcnow, index=True)
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
            'abstract': self.abstract,
            'source': self.source,
            'source_id': self.source_id,
            'year': self.year,
            'venue': self.venue,
            'url': self.url,
            'pdf_url': self.pdf_url,
            'published_date': self.published_date.isoformat() if self.published_date else None,
            'fetched_date': self.fetched_date.isoformat() if self.fetched_date else None,
            'domain_id': self.domain_id,
            'is_favorite': self.is_favorite,
            'llm_score': self.llm_score,
            'llm_value_score': self.llm_value_score
        }

    @staticmethod
    def exists_by_source_and_title(source: str, title: str) -> bool:
        """检查指定数据源下是否已存在该标题的论文"""
        return db.session.query(
            db.exists().where(
                db.and_(Paper.source == source, Paper.title == title)
            )
        ).scalar()
