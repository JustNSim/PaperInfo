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
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'paper_count': self.papers.count()
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

    # 外键关联
    domain_id = db.Column(db.Integer, db.ForeignKey('domains.id'), nullable=False, index=True)

    # 唯一约束：同一数据源下标题唯一
    __table_args__ = (
        db.UniqueConstraint('source', 'title', name='uq_source_title'),
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
            'is_favorite': self.is_favorite
        }

    @staticmethod
    def exists_by_source_and_title(source: str, title: str) -> bool:
        """检查指定数据源下是否已存在该标题的论文"""
        return db.session.query(
            db.exists().where(
                db.and_(Paper.source == source, Paper.title == title)
            )
        ).scalar()
