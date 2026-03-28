"""
PaperInfo 定时任务配置
使用 APScheduler 实现定时抓取论文
"""
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.executors.pool import ThreadPoolExecutor

from config import Config
from models import db, Domain, Paper, UpdateLog
from crawler import ArxivCrawler, DBLPCrawler, SemanticScholarCrawler
from llm import LLMEvaluator, LLMEvaluatorError

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

# 全局 LLM 评估器实例
llm_evaluator = None

# LLM 过滤计数器 (每次更新操作重置)
llm_filtered_count = 0


def _get_llm_evaluator():
    """
    获取或创建 LLM 评估器实例

    Returns:
        LLMEvaluator 实例（如果启用）或 None
    """
    global llm_evaluator

    if not Config.LLM_FILTER_ENABLED:
        return None

    if llm_evaluator is None:
        try:
            # 根据提供商选择正确的模型配置
            if Config.LLM_PROVIDER == 'custom':
                model = Config.CUSTOM_LLM_MODEL
            else:
                model = Config.LLM_MODEL

            llm_evaluator = LLMEvaluator(
                provider=Config.LLM_PROVIDER,
                api_key=Config.LLM_API_KEY,
                model=model,
                delay=Config.LLM_DELAY,
                system_prompt=Config.LLM_SYSTEM_PROMPT,
                enabled=Config.LLM_FILTER_ENABLED
            )
            logger.info(f"LLM 评估器已初始化: provider={Config.LLM_PROVIDER}, model={model}")
        except LLMEvaluatorError as e:
            logger.error(f"LLM 评估器初始化失败: {e}")
            logger.warning("LLM 过滤已禁用，将继续保存所有论文")
            return None

    return llm_evaluator


def _get_fetch_date_range():
    """
    获取抓取的时间范围

    Returns:
        (from_date, to_date) 元组，可能包含 None
    """
    from_date = None
    to_date = datetime.utcnow()

    # 检查论文表是否为空
    paper_count = Paper.query.count()
    if paper_count == 0:
        logger.info("论文表为空，使用默认时间范围获取历史数据")
        from_date = to_date - timedelta(days=Config.FETCH_DAYS_BACK)
        return from_date, to_date

    # 检查最近是否有清空操作
    try:
        last_cleared = UpdateLog.query.filter_by(
            trigger_type='data_cleared'
        ).order_by(UpdateLog.trigger_time.desc()).first()
        if last_cleared:
            # 检查清空后是否有新的成功更新
            last_successful = UpdateLog.query.filter(
                UpdateLog.trigger_type.in_(['scheduled', 'manual']),
                UpdateLog.status == 'success',
                UpdateLog.trigger_time > last_cleared.trigger_time
            ).first()
            if not last_successful:
                logger.info(f"检测到数据清空操作（{last_cleared.trigger_time.strftime('%Y-%m-%d %H:%M')}），使用默认时间范围")
                from_date = to_date - timedelta(days=Config.FETCH_DAYS_BACK)
                return from_date, to_date
    except Exception as e:
        logger.warning(f"检查清空记录失败: {e}")

    if Config.INCREMENTAL_UPDATE:
        # 尝试获取上次更新时间
        # 优先使用上次有新增论文的更新时间，而不是最近的空更新
        try:
            # 查找最近一次成功且有新增论文的更新
            last_log_with_papers = UpdateLog.query.filter(
                UpdateLog.trigger_type.in_(['scheduled', 'manual']),
                UpdateLog.status == 'success',
                UpdateLog.total_new > 0
            ).order_by(UpdateLog.trigger_time.desc()).first()

            # 检查上次更新时间（即使是空更新）
            last_log = UpdateLog.query.filter_by(status='success').order_by(
                UpdateLog.trigger_time.desc()
            ).first()

            if last_log_with_papers:
                # 有成功的历史记录，从那时起增量更新
                from_date = last_log_with_papers.trigger_time
                logger.info(f"增量更新：从 {from_date.strftime('%Y-%m-%d %H:%M')} 开始")
            elif last_log:
                # 上次更新成功但0篇论文，检查是否是最近的情况
                time_since_last = (datetime.utcnow() - last_log.trigger_time).total_seconds()
                if time_since_last < 3600:  # 1小时内
                    # 上次更新刚刚发生且0篇，说明可能当天没有新论文
                    # 使用默认范围获取历史数据
                    logger.info(f"上次更新在 {time_since_last/60:.1f} 分钟前且无新论文，使用默认时间范围")
                    from_date = to_date - timedelta(days=Config.FETCH_DAYS_BACK)
                else:
                    # 超过1小时，尝试从上次更新时间开始
                    from_date = last_log.trigger_time
                    logger.info(f"增量更新：从 {from_date.strftime('%Y-%m-%d %H:%M')} 开始")
        except Exception as e:
            logger.warning(f"获取上次更新时间失败: {e}，将使用默认范围")

    # 如果没有上次更新时间，使用配置的天数
    if not from_date and Config.FETCH_DAYS_BACK:
        from_date = to_date - timedelta(days=Config.FETCH_DAYS_BACK)
        logger.info(f"使用默认时间范围：最近 {Config.FETCH_DAYS_BACK} 天")

    return from_date, to_date


def fetch_papers_for_domain(domain: Domain) -> dict:
    """
    为指定领域抓取论文

    Args:
        domain: Domain 对象

    Returns:
        包含详细统计信息的字典: {
            'domain_id': int,
            'new_count': int,
            'source_stats': dict
        }
    """
    logger.info(f"开始抓取领域: {domain.name}")
    result = {
        'domain_id': domain.id,
        'domain_name': domain.name,
        'new_count': 0,
        'source_stats': {}
    }

    # 获取时间范围
    from_date, to_date = None, None
    if Config.ENABLE_TIME_FILTER:
        from_date, to_date = _get_fetch_date_range()

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
                categories=domain.arxiv_categories,
                from_date=from_date,
                to_date=to_date
            )
            arxiv_count = _save_papers(arxiv_papers, domain)
            result['new_count'] += arxiv_count
            result['source_stats']['arxiv'] = arxiv_count
        else:
            # 即使没有指定分类，也用关键词搜索
            logger.info(f"从 arXiv 用关键词抓取 {domain.name} 论文...")
            arxiv_crawler = ArxivCrawler(
                delay=Config.ARXIV_DELAY,
                timeout=Config.REQUEST_TIMEOUT,
                max_results=Config.MAX_PAPERS_PER_SOURCE
            )
            arxiv_papers = arxiv_crawler.search(
                keywords=domain.keywords,
                from_date=from_date,
                to_date=to_date
            )
            arxiv_count = _save_papers(arxiv_papers, domain)
            result['new_count'] += arxiv_count
            result['source_stats']['arxiv'] = arxiv_count

        # 2. 从 DBLP 抓取
        if domain.ccf_venues:
            logger.info(f"从 DBLP 抓取 {domain.name} 论文...")

            # 计算年份范围
            from_year = from_date.year if from_date else None
            to_year = to_date.year if to_date else None

            dblp_crawler = DBLPCrawler(
                delay=Config.DBLP_DELAY,
                timeout=Config.REQUEST_TIMEOUT,
                max_results=Config.MAX_PAPERS_PER_SOURCE
            )
            dblp_papers = dblp_crawler.search(
                keywords=domain.keywords,
                venues=domain.ccf_venues,
                from_year=from_year,
                to_year=to_year
            )
            dblp_count = _save_papers(dblp_papers, domain)
            result['new_count'] += dblp_count
            result['source_stats']['dblp'] = dblp_count

        # 3. 从 Semantic Scholar 抓取
        logger.info(f"从 Semantic Scholar 抓取 {domain.name} 论文...")
        s2_crawler = SemanticScholarCrawler(
            timeout=Config.REQUEST_TIMEOUT,
            max_results=100
        )
        # 使用前 5 个关键词作为核心关键词
        core_keywords = domain.keywords[:5] if domain.keywords else None
        s2_papers = s2_crawler.search(
            keywords=domain.keywords,
            venues=domain.ccf_venues,
            from_year=from_year,
            to_year=to_year,
            core_keywords=core_keywords
        )
        s2_count = _save_papers(s2_papers, domain)
        result['new_count'] += s2_count
        result['source_stats']['s2'] = s2_count

        logger.info(f"领域 {domain.name} 抓取完成，新增 {result['new_count']} 篇论文")

    except Exception as e:
        logger.error(f"抓取领域 {domain.name} 时出错: {e}")
        result['error'] = str(e)

    return result


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

    # 获取 LLM 评估器
    evaluator = _get_llm_evaluator()

    for paper_data in papers:
        try:
            # 使用集合进行 O(1) 去重检查
            if (paper_data['source'], paper_data['title']) in existing_set:
                logger.debug(f"论文已存在，跳过: {paper_data['title'][:50]}")
                continue

            # LLM 相关性评估
            if evaluator:
                eval_result = evaluator.evaluate(
                    title=paper_data.get('title', ''),
                    abstract=paper_data.get('abstract') or ''
                )

                if eval_result.error:
                    logger.warning(f"LLM 评估出错，跳过论文: {paper_data.get('title', '')[:50]} - {eval_result.error}")
                    continue

                if eval_result.score < Config.LLM_FILTER_THRESHOLD:
                    logger.debug(f"LLM 评分 {eval_result.score} < {Config.LLM_FILTER_THRESHOLD}，跳过: {paper_data.get('title', '')[:50]}")
                    global llm_filtered_count
                    llm_filtered_count += 1
                    continue

                logger.info(f"LLM 评分 {eval_result.score} >= {Config.LLM_FILTER_THRESHOLD}，通过: {paper_data.get('title', '')[:50]}")

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


def _create_update_log(trigger_type: str, total_new: int, source_stats: dict,
                       domain_ids: list, status: str = 'success', error_message: str = None,
                       llm_filtered: int = 0):
    """
    创建更新日志记录

    Args:
        trigger_type: 触发类型 ('scheduled' 或 'manual')
        total_new: 新增论文总数
        source_stats: 来源统计 {'arxiv': 10, 'dblp': 5}
        domain_ids: 处理的领域 ID 列表
        status: 状态 ('success', 'failed', 'partial')
        error_message: 错误信息（可选）
        llm_filtered: LLM 过滤的论文数（可选）
    """
    try:
        log = UpdateLog(
            trigger_type=trigger_type,
            total_new=total_new,
            arxiv_new=source_stats.get('arxiv', 0),
            dblp_new=source_stats.get('dblp', 0),
            llm_filtered=llm_filtered,
            source_stats=source_stats,
            domains_processed=domain_ids,
            status=status,
            error_message=error_message
        )
        db.session.add(log)
        db.session.commit()
        log_msg = f"更新日志已记录: {trigger_type} - 新增 {total_new} 篇论文"
        if Config.LLM_FILTER_ENABLED and llm_filtered > 0:
            log_msg += f" (LLM 过滤 {llm_filtered} 篇)"
        logger.info(log_msg)
    except Exception as e:
        logger.error(f"记录更新日志失败: {e}")
        db.session.rollback()


def scheduled_fetch_job():
    """定时抓取任务 - 由调度器调用"""
    logger.info("=" * 50)
    logger.info(f"开始执行定时抓取任务: {datetime.now()}")
    logger.info("=" * 50)

    global llm_filtered_count
    llm_filtered_count = 0  # 重置 LLM 过滤计数器

    total_new = 0
    all_source_stats = {'arxiv': 0, 'dblp': 0}
    domain_results = []

    try:
        # 获取所有启用的领域
        domains = Domain.query.filter_by(enabled=True).all()

        if not domains:
            logger.warning("没有启用的领域，跳过抓取")
            return

        logger.info(f"找到 {len(domains)} 个启用的领域")

        for domain in domains:
            result = fetch_papers_for_domain(domain)
            total_new += result.get('new_count', 0)
            # 合并来源统计
            for source, count in result.get('source_stats', {}).items():
                all_source_stats[source] = all_source_stats.get(source, 0) + count
            domain_results.append(result)

        logger.info("=" * 50)
        logger.info(f"定时抓取任务完成，共新增 {total_new} 篇论文")
        if Config.LLM_FILTER_ENABLED and llm_filtered_count > 0:
            logger.info(f"LLM 过滤了 {llm_filtered_count} 篇不相关论文")
        logger.info("=" * 50)

        # 记录更新日志
        _create_update_log('scheduled', total_new, all_source_stats, [d.id for d in domains], 'success', llm_filtered=llm_filtered_count)

    except Exception as e:
        logger.error(f"定时抓取任务出错: {e}")
        # 记录失败日志
        _create_update_log('scheduled', 0, {'arxiv': 0, 'dblp': 0}, [d.id for d in domains], 'failed', str(e), llm_filtered=0)


def manual_trigger_fetch(domain_id: int = None) -> dict:
    """
    手动触发抓取

    Args:
        domain_id: 指定领域 ID，None 表示抓取所有启用的领域

    Returns:
        结果字典
    """
    logger.info(f"手动触发抓取，domain_id: {domain_id}")

    global llm_filtered_count
    llm_filtered_count = 0  # 重置 LLM 过滤计数器

    result = {
        'success': True,
        'new_papers': 0,
        'domains_processed': 0,
        'source_stats': {},
        'llm_filtered': 0,
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

        domain_ids = []
        for domain in domains:
            domain_result = fetch_papers_for_domain(domain)
            result['new_papers'] += domain_result.get('new_count', 0)
            result['domains_processed'] += 1
            domain_ids.append(domain_result['domain_id'])
            # 合并来源统计
            for source, count in domain_result.get('source_stats', {}).items():
                result['source_stats'][source] = result['source_stats'].get(source, 0) + count

        result['llm_filtered'] = llm_filtered_count
        if Config.LLM_FILTER_ENABLED and llm_filtered_count > 0:
            result['message'] = f'成功处理 {result["domains_processed"]} 个领域，新增 {result["new_papers"]} 篇论文（LLM 过滤了 {llm_filtered_count} 篇）'
        else:
            result['message'] = f'成功处理 {result["domains_processed"]} 个领域，新增 {result["new_papers"]} 篇论文'

        # 记录更新日志
        _create_update_log('manual', result['new_papers'], result['source_stats'], domain_ids, 'success', llm_filtered=llm_filtered_count)

    except Exception as e:
        logger.error(f"手动触发抓取失败: {e}")
        result['success'] = False
        result['message'] = f'抓取失败: {str(e)}'

        # 记录失败日志
        try:
            domain_ids = [d.id for d in Domain.query.filter_by(enabled=True).all()]
            _create_update_log('manual', 0, {'arxiv': 0, 'dblp': 0}, domain_ids, 'failed', str(e), llm_filtered=0)
        except:
            pass

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

    # 检查调度器是否已经存在并正在运行
    if scheduler is not None and scheduler.running:
        logger.warning("调度器已经在运行，跳过重复启动")
        return scheduler

    # 如果调度器存在但未运行，先关闭它
    if scheduler is not None and not scheduler.running:
        try:
            scheduler.shutdown(wait=False)
            logger.info("关闭旧的调度器实例")
        except Exception as e:
            logger.warning(f"关闭旧调度器时出错: {e}")
        scheduler = None

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
