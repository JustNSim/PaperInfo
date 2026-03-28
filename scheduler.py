"""
PaperInfo 定时任务配置
使用 APScheduler 实现定时抓取论文
"""
import logging
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.executors.pool import ThreadPoolExecutor

from config import Config
from models import db, Domain, Paper
from crawler import ArxivCrawler, DBLPCrawler

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/paper_info.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 全局调度器实例
scheduler = None


def fetch_papers_for_domain(domain: Domain) -> int:
    """
    为指定领域抓取论文

    Args:
        domain: Domain 对象

    Returns:
        新增论文数量
    """
    logger.info(f"开始抓取领域: {domain.name}")
    new_count = 0

    try:
        # 1. 从 arXiv 抓取
        if domain.arxiv_categories:
            logger.info(f"从 arXiv 抓取 {domain.name} 论文...")
            arxiv_crawler = ArxivCrawler(
                delay=Config.ARXIV_DELAY,
                timeout=Config.REQUEST_TIMEOUT,
                max_results=Config.MAX_PAPERS_PER_SOURCE
            )
            arxiv_papers = arxiv_crawler.search(
                keywords=domain.keywords,
                categories=domain.arxiv_categories
            )
            new_count += _save_papers(arxiv_papers, domain)
        else:
            # 即使没有指定分类，也用关键词搜索
            logger.info(f"从 arXiv 用关键词抓取 {domain.name} 论文...")
            arxiv_crawler = ArxivCrawler(
                delay=Config.ARXIV_DELAY,
                timeout=Config.REQUEST_TIMEOUT,
                max_results=Config.MAX_PAPERS_PER_SOURCE
            )
            arxiv_papers = arxiv_crawler.search(keywords=domain.keywords)
            new_count += _save_papers(arxiv_papers, domain)

        # 2. 从 DBLP 抓取
        if domain.ccf_venues:
            logger.info(f"从 DBLP 抓取 {domain.name} 论文...")
            dblp_crawler = DBLPCrawler(
                delay=Config.ARXIV_DELAY,
                timeout=Config.REQUEST_TIMEOUT,
                max_results=Config.MAX_PAPERS_PER_SOURCE
            )
            dblp_papers = dblp_crawler.search(
                keywords=domain.keywords,
                venues=domain.ccf_venues
            )
            new_count += _save_papers(dblp_papers, domain)

        logger.info(f"领域 {domain.name} 抓取完成，新增 {new_count} 篇论文")

    except Exception as e:
        logger.error(f"抓取领域 {domain.name} 时出错: {e}")

    return new_count


# 批量提交大小：每处理 BATCH_SIZE 篇论文提交一次
BATCH_SIZE = 20


def _save_papers(papers: list, domain: Domain) -> int:
    """
    保存论文到数据库（去重，批量提交）

    优化策略：
    - 批量预查询已存在的论文（一次查询替代多次）
    - 使用集合进行 O(1) 去重检查
    - 每 BATCH_SIZE 篇论文提交一次

    Args:
        papers: 论文列表
        domain: 所属领域

    Returns:
        新增论文数量
    """
    if not papers:
        return 0

    # 第一步：批量预查询已存在的论文
    sources = set()
    titles = set()
    for paper_data in papers:
        sources.add(paper_data['source'])
        titles.add(paper_data['title'])

    existing_papers = db.session.query(Paper.source, Paper.title).filter(
        Paper.source.in_(sources),
        Paper.title.in_(titles)
    ).all()

    # 构建已存在论文的集合，用于 O(1) 查找
    existing_set = {(p.source, p.title) for p in existing_papers}
    logger.info(f"预查询发现 {len(existing_set)} 篇已存在的论文")

    # 第二步：处理新论文
    new_count = 0
    batch = []  # 当前批次的论文
    committed_count = 0  # 已提交的论文数

    for paper_data in papers:
        try:
            # 使用集合进行 O(1) 去重检查
            if (paper_data['source'], paper_data['title']) in existing_set:
                logger.debug(f"论文已存在，跳过: {paper_data['title'][:50]}")
                continue

            # 创建新论文对象
            paper = Paper(
                title=paper_data['title'],
                authors=paper_data.get('authors', []),
                abstract=paper_data.get('abstract'),
                source=paper_data['source'],
                source_id=paper_data.get('source_id'),
                year=paper_data.get('year'),
                venue=paper_data.get('venue'),
                url=paper_data.get('url'),
                pdf_url=paper_data.get('pdf_url'),
                published_date=paper_data.get('published_date'),
                domain_id=domain.id
            )

            batch.append(paper)
            new_count += 1
            logger.debug(f"准备新增论文: {paper_data['title'][:50]}")

            # 达到批次大小时提交
            if len(batch) >= BATCH_SIZE:
                committed = _commit_batch(batch)
                committed_count += committed
                batch = []  # 清空批次

        except Exception as e:
            logger.warning(f"处理论文失败: {e}")
            continue

    # 提交剩余的论文
    if batch:
        committed = _commit_batch(batch)
        committed_count += committed

    logger.info(f"共尝试新增 {new_count} 篇论文，成功提交 {committed_count} 篇")
    return committed_count


def _commit_batch(batch: list) -> int:
    """
    提交一批论文到数据库

    Args:
        batch: 论文对象列表

    Returns:
        成功提交的论文数量
    """
    if not batch:
        return 0

    try:
        for paper in batch:
            db.session.add(paper)

        db.session.commit()
        logger.info(f"成功提交批次: {len(batch)} 篇论文")
        return len(batch)

    except Exception as e:
        db.session.rollback()
        logger.error(f"批次提交失败 ({len(batch)} 篇论文丢失): {e}")
        return 0


def scheduled_fetch_job():
    """定时抓取任务 - 由调度器调用"""
    logger.info("=" * 50)
    logger.info(f"开始执行定时抓取任务: {datetime.now()}")
    logger.info("=" * 50)

    total_new = 0

    try:
        # 获取所有启用的领域
        domains = Domain.query.filter_by(enabled=True).all()

        if not domains:
            logger.warning("没有启用的领域，跳过抓取")
            return

        logger.info(f"找到 {len(domains)} 个启用的领域")

        for domain in domains:
            new_count = fetch_papers_for_domain(domain)
            total_new += new_count

        logger.info("=" * 50)
        logger.info(f"定时抓取任务完成，共新增 {total_new} 篇论文")
        logger.info("=" * 50)

    except Exception as e:
        logger.error(f"定时抓取任务出错: {e}")


def manual_trigger_fetch(domain_id: int = None) -> dict:
    """
    手动触发抓取

    Args:
        domain_id: 指定领域 ID，None 表示抓取所有启用的领域

    Returns:
        结果字典
    """
    logger.info(f"手动触发抓取，domain_id: {domain_id}")

    result = {
        'success': True,
        'new_papers': 0,
        'domains_processed': 0,
        'message': ''
    }

    try:
        if domain_id:
            domains = [Domain.query.get(domain_id)]
            if not domains or not domains[0]:
                result['success'] = False
                result['message'] = f'领域 ID {domain_id} 不存在'
                return result
        else:
            domains = Domain.query.filter_by(enabled=True).all()

        if not domains:
            result['message'] = '没有启用的领域'
            return result

        for domain in domains:
            new_count = fetch_papers_for_domain(domain)
            result['new_papers'] += new_count
            result['domains_processed'] += 1

        result['message'] = f'成功处理 {result["domains_processed"]} 个领域，新增 {result["new_papers"]} 篇论文'

    except Exception as e:
        logger.error(f"手动触发抓取失败: {e}")
        result['success'] = False
        result['message'] = f'抓取失败: {str(e)}'

    return result


def setup_scheduler(app=None):
    """
    设置并启动调度器

    Args:
        app: Flask 应用实例（可选）

    Returns:
        调度器实例
    """
    global scheduler

    if scheduler is not None:
        logger.warning("调度器已经运行")
        return scheduler

    # 配置执行器
    executors = {
        'default': ThreadPoolExecutor(max_workers=5)
    }

    # 创建调度器
    scheduler = BackgroundScheduler(
        executors=executors,
        timezone='Asia/Shanghai'
    )

    # 添加定时任务 - 每天指定时间执行
    scheduler.add_job(
        scheduled_fetch_job,
        trigger=CronTrigger(
            hour=Config.SCHEDULE_HOUR,
            minute=Config.SCHEDULE_MINUTE
        ),
        id='daily_paper_fetch',
        name='每日论文抓取',
        replace_existing=True
    )

    # 启动调度器
    scheduler.start()
    logger.info(f"调度器已启动，每天 {Config.SCHEDULE_HOUR:02d}:{Config.SCHEDULE_MINUTE:02d} 执行抓取")

    return scheduler


def get_next_run_time():
    """获取下次运行时间"""
    global scheduler
    if scheduler:
        job = scheduler.get_job('daily_paper_fetch')
        if job:
            return job.next_run_time
    return None
