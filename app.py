"""
PaperInfo - 论文调研工具
主应用入口
"""
import os
import json
import queue
import threading
import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode
from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from sqlalchemy.exc import IntegrityError

from config import Config, config
from models import db, Domain, Paper, UpdateLog, ReadHistory, get_beijing_time
from scheduler import setup_scheduler, manual_trigger_fetch, get_next_run_time, _evaluate_single_paper
from translation_service import TranslationError, TranslationService

# 创建应用日志
logger = logging.getLogger(__name__)

# 领域配色板（高区分度，避免相近色；按领域 ID 确定性分配，保证各页面展示一致）
DOMAIN_COLORS = [
    '#2563eb',  # 蓝
    '#16a34a',  # 绿
    '#d97706',  # 琥珀
    '#dc2626',  # 红
    '#7c3aed',  # 紫
    '#db2777',  # 品红
    '#0891b2',  # 青
    '#65a30d',  # 黄绿
    '#c2410c',  # 橙
    '#475569',  # 石板灰
]


def domain_color(domain_id):
    """按领域 ID 分配固定颜色（首页徽章、历史页、统计图表共用）"""
    if domain_id is None:
        return DOMAIN_COLORS[-1]
    return DOMAIN_COLORS[(domain_id - 1) % len(DOMAIN_COLORS)]

# 创建 Flask 应用
def create_app(config_name='default'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])
    app.extensions['translation_service'] = TranslationService.from_config(app.config)

    # 确保必要的目录存在
    os.makedirs(app.config['BASE_DIR'] + '/data', exist_ok=True)
    os.makedirs(app.config['BASE_DIR'] + '/logs', exist_ok=True)

    # 初始化数据库
    db.init_app(app)

    # 创建数据库表
    with app.app_context():
        db.create_all()
        # 初始化默认领域
        _init_default_domains()

    # 设置定时任务
    setup_scheduler(app)

    # 注册路由
    register_routes(app)

    # 领域配色函数注入模板（index/history 等页面的领域徽章使用）
    app.jinja_env.globals['domain_color'] = domain_color

    return app


def _init_default_domains():
    """数据库为空时初始化默认领域；已有领域保留用户编辑的配置。"""
    existing_count = Domain.query.count()

    if existing_count == 0:
        # 数据库为空，创建默认领域
        for domain_config in Config.DEFAULT_DOMAINS:
            domain = Domain(
                name=domain_config['name'],
                keywords=domain_config['keywords'],
                arxiv_categories=domain_config.get('arxiv_categories', []),
                ccf_venues=domain_config.get('ccf_venues', []),
                enabled=True
            )
            db.session.add(domain)
        try:
            db.session.commit()
            print(f"首次安装：已创建 {len(Config.DEFAULT_DOMAINS)} 个默认领域")
        except Exception as e:
            db.session.rollback()
            print(f"初始化默认领域时出错: {e}")


def _query_papers_by_filters(filters: dict):
    """
    按筛选条件构建 Paper 查询（供跨页全选批量操作/导出复用）

    filters: {'domain': int, 'source': [..], 'year': int, 'days': int,
              'q': str, 'update_log': int}（与 index 路由同名参数语义一致）
    """
    query = Paper.query

    domain_id = filters.get('domain')
    if domain_id:
        query = query.filter_by(domain_id=int(domain_id))

    sources = filters.get('source') or []
    if sources:
        query = query.filter(Paper.source.in_(sources))

    year = filters.get('year')
    if year:
        query = query.filter_by(year=int(year))

    days = filters.get('days')
    if days:
        days = min(int(days), 3650)
        query = query.filter(
            Paper.published_date >= datetime.now() - timedelta(days=days)
        )

    keyword = (filters.get('q') or '').strip()
    if keyword:
        pattern = f'%{keyword}%'
        query = query.filter(
            db.or_(
                Paper.title.ilike(pattern),
                Paper.abstract.ilike(pattern),
                Paper.venue.ilike(pattern)
            )
        )

    update_log_id = filters.get('update_log')
    if update_log_id:
        log = UpdateLog.query.get(int(update_log_id))
        if log:
            query = query.filter(
                Paper.fetched_date >= log.trigger_time - timedelta(minutes=5),
                Paper.fetched_date <= log.trigger_time + timedelta(minutes=5)
            )

    return query


def _selection_query(data: dict):
    """
    解析批量操作/导出的目标论文查询

    支持三种方式（优先级从高到低）：
    - select_all: true + filters → 当前筛选条件下的全部论文（跨页）
    - favorites: true → 全部收藏论文
    - paper_ids: [...] → 显式选中的论文
    """
    if data.get('select_all'):
        return _query_papers_by_filters(data.get('filters') or {})
    if data.get('favorites'):
        return Paper.query.filter_by(is_favorite=True)
    paper_ids = data.get('paper_ids') or []
    # 空列表时匹配一个不存在的 id，返回空查询集
    return Paper.query.filter(Paper.id.in_(paper_ids if paper_ids else [-1]))


def register_routes(app):
    """注册所有路由"""

    @app.route('/stats')
    def stats():
        """统计页面"""
        return render_template('stats.html')

    @app.route('/history')
    def history():
        """历史页面 - 最近抓取与最近点击的论文"""
        recent_fetched = Paper.query.order_by(Paper.fetched_date.desc()).limit(50).all()
        recent_reads = ReadHistory.query.order_by(ReadHistory.last_read_at.desc()).limit(50).all()
        return render_template('history.html',
                             recent_fetched=recent_fetched,
                             recent_reads=recent_reads)

    @app.route('/')
    def index():
        """主页 - 论文列表"""
        # 获取筛选参数
        page = request.args.get('page', 1, type=int)
        per_page = Config.PAPERS_PER_PAGE
        domain_id = request.args.get('domain', type=int)
        keyword = request.args.get('q', '').strip()
        sources = request.args.getlist('source')  # 支持多个source参数
        year = request.args.get('year', type=int)
        days = request.args.get('days', type=int)
        sort = request.args.get('sort', 'date')  # date, title, score_desc, score_asc
        update_log_id = request.args.get('update_log', type=int)  # 按更新事件筛选

        # 构建查询
        query = Paper.query

        # 按更新事件筛选：查询该次更新新增的论文
        selected_update_log = None
        if update_log_id:
            selected_update_log = UpdateLog.query.get(update_log_id)
            if selected_update_log:
                # 使用该更新日志的时间范围筛选论文
                update_time = selected_update_log.trigger_time
                query = query.filter(
                    Paper.fetched_date >= update_time - timedelta(minutes=5),
                    Paper.fetched_date <= update_time + timedelta(minutes=5)
                )

        # 领域过滤
        if domain_id:
            query = query.filter_by(domain_id=domain_id)

        # 数据源过滤
        if sources:
            query = query.filter(Paper.source.in_(sources))

        # 年份过滤
        if year:
            query = query.filter_by(year=year)

        # 最近 N 天过滤（按论文发布日期）
        if days and days > 0:
            days = min(days, 3650)
            query = query.filter(
                Paper.published_date >= datetime.now() - timedelta(days=days)
            )

        # 关键词搜索
        if keyword:
            search_pattern = f'%{keyword}%'
            query = query.filter(
                db.or_(
                    Paper.title.ilike(search_pattern),
                    Paper.abstract.ilike(search_pattern),
                    Paper.venue.ilike(search_pattern)
                )
            )

        # 排序
        if sort == 'title':
            query = query.order_by(Paper.title.asc())
        elif sort == 'score_desc':
            query = query.order_by(Paper.llm_score.desc().nulls_last(), Paper.published_date.desc())
        elif sort == 'score_asc':
            query = query.order_by(Paper.llm_score.asc().nulls_last(), Paper.published_date.desc())
        elif sort == 'value_desc':
            query = query.order_by(Paper.llm_value_score.desc().nulls_last(), Paper.published_date.desc())
        elif sort == 'value_asc':
            query = query.order_by(Paper.llm_value_score.asc().nulls_last(), Paper.published_date.desc())
        else:  # date
            query = query.order_by(Paper.published_date.desc())

        # 分页
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        papers = pagination.items

        # 获取所有启用的领域（用于筛选下拉框）
        domains = Domain.query.filter_by(enabled=True).all()

        # 获取所有领域（含禁用的，用于领域管理面板）
        all_domains = Domain.query.order_by(Domain.enabled.desc(), Domain.name.asc()).all()

        # 计算每个领域的上次更新时间和论文数
        # 注意：SQLite 下 JSON contains([id]) 实际是按 LIKE '%[id]%' 匹配，只能命中
        # 单元素数组，多领域一起更新的记录匹配不到，改为在 Python 侧判断
        recent_success_logs = UpdateLog.query.filter(
            UpdateLog.trigger_type.in_(['scheduled', 'manual', 'catch_up']),
            UpdateLog.status == 'success'
        ).order_by(UpdateLog.trigger_time.desc()).limit(200).all()

        domain_last_updates = {}
        domain_paper_counts = {}
        for d in all_domains:
            last_time = None
            for log in recent_success_logs:
                if d.id in (log.domains_processed or []):
                    last_time = log.trigger_time
                    break
            domain_last_updates[d.id] = last_time
            domain_paper_counts[d.id] = d.papers.count()

        # 获取可用年份列表
        years = db.session.query(Paper.year).filter(
            Paper.year.isnot(None)
        ).distinct().order_by(Paper.year.desc()).all()
        years = [y[0] for y in years]

        # 构建查询字符串（用于分页链接）
        query_params = [
            (key, value)
            for key, values in request.args.lists() if key != 'page'
            for value in values
        ]
        query_string = '&' + urlencode(query_params) if query_params else ''

        # 获取当前领域名称
        domain_name = None
        if domain_id:
            domain = Domain.query.get(domain_id)
            if domain:
                domain_name = domain.name

        # 获取最近一次成功完成的更新任务时间；不能使用列表首篇论文的入库时间，
        # 因为论文按发布日期排序，而且新增 0 篇的任务不会产生新的 fetched_date。
        successful_update_logs = UpdateLog.query.filter(
            UpdateLog.trigger_type.in_(['scheduled', 'manual', 'catch_up']),
            UpdateLog.status == 'success'
        ).order_by(UpdateLog.trigger_time.desc()).all()
        if domain_id:
            last_successful_update = next(
                (
                    log for log in successful_update_logs
                    if domain_id in (log.domains_processed or [])
                ),
                None
            )
        else:
            last_successful_update = successful_update_logs[0] if successful_update_logs else None
        last_update = (
            last_successful_update.trigger_time.strftime('%Y-%m-%d %H:%M')
            if last_successful_update else None
        )

        # 获取下次运行时间
        next_run = None
        if get_next_run_time():
            next_run = get_next_run_time().strftime('%Y-%m-%d %H:%M')

        # 获取更新事件列表（用于筛选下拉框）
        update_logs = UpdateLog.query.filter(
            UpdateLog.trigger_type.in_(['scheduled', 'manual', 'catch_up']),
            UpdateLog.status == 'success',
            UpdateLog.total_new > 0
        ).order_by(UpdateLog.trigger_time.desc()).limit(30).all()

        return render_template('index.html',
                             papers=papers,
                             domains=domains,
                             domain_id=domain_id,
                             domain_name=domain_name,
                             years=years,
                             year=year,
                             days=days,
                             sources=sources,
                             sort=sort,
                             page=page,
                             total=pagination.total,
                             per_page=per_page,
                             total_pages=pagination.pages,
                             query_string=query_string,
                             last_update=last_update,
                             next_run=next_run,
                             update_logs=update_logs,
                             update_log_id=update_log_id,
                             all_domains=all_domains,
                             domain_last_updates=domain_last_updates,
                             domain_paper_counts=domain_paper_counts)

    @app.route('/api/papers')
    def api_papers():
        """API: 获取论文列表"""
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', Config.PAPERS_PER_PAGE, type=int)
        domain_id = request.args.get('domain', type=int)
        sort_by = request.args.get('sort', 'date')  # date, score_asc, score_desc
        sources = request.args.getlist('source')  # arxiv, dblp, s2
        days = request.args.get('days', type=int)

        query = Paper.query
        if domain_id:
            query = query.filter_by(domain_id=domain_id)

        # 数据源过滤
        if sources:
            query = query.filter(Paper.source.in_(sources))

        if days and days > 0:
            query = query.filter(
                Paper.published_date >= datetime.now() - timedelta(days=min(days, 3650))
            )

        # 排序
        if sort_by == 'score_desc':
            query = query.order_by(Paper.llm_score.desc().nulls_last(), Paper.published_date.desc())
        elif sort_by == 'score_asc':
            query = query.order_by(Paper.llm_score.asc().nulls_last(), Paper.published_date.desc())
        elif sort_by == 'value_desc':
            query = query.order_by(Paper.llm_value_score.desc().nulls_last(), Paper.published_date.desc())
        elif sort_by == 'value_asc':
            query = query.order_by(Paper.llm_value_score.asc().nulls_last(), Paper.published_date.desc())
        else:  # date (默认)
            query = query.order_by(Paper.published_date.desc())

        pagination = query.paginate(page=page, per_page=per_page, error_out=False)

        return jsonify({
            'papers': [p.to_dict() for p in pagination.items],
            'total': pagination.total,
            'pages': pagination.pages,
            'current_page': page
        })

    @app.route('/api/papers/<int:paper_id>/read', methods=['POST'])
    def api_mark_paper_read(paper_id):
        """API: 记录一次论文点击（upsert，每篇一行）"""
        Paper.query.get_or_404(paper_id)
        try:
            history = ReadHistory.query.filter_by(paper_id=paper_id).first()
            if history:
                history.read_count += 1
                history.last_read_at = get_beijing_time()
            else:
                history = ReadHistory(paper_id=paper_id, read_count=1,
                                      last_read_at=get_beijing_time())
                db.session.add(history)
            db.session.commit()
            return jsonify({'success': True, 'read_count': history.read_count})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/api/export/csv', methods=['POST'])
    def api_export_csv():
        """API: 导出论文为 CSV（选中/跨页全选/收藏）"""
        data = request.get_json() or {}
        papers = _selection_query(data).all()
        if not papers:
            return jsonify({'success': False, 'message': '没有可导出的论文'}), 400

        import csv
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Title', 'Authors', 'Abstract', 'Source', 'Venue', 'Year', 'Published', 'URL', 'PDF'])
        for p in papers:
            writer.writerow([
                p.title,
                '; '.join(p.authors or []),
                p.abstract or '',
                p.source,
                p.venue or '',
                p.year or '',
                p.published_date.strftime('%Y-%m-%d') if p.published_date else '',
                p.url or '',
                p.pdf_url or ''
            ])

        # BOM 保证 Excel 正确识别 UTF-8
        content = '\ufeff' + output.getvalue()
        filename = f"papers_{datetime.now().strftime('%Y%m%d')}.csv"
        return Response(
            content,
            mimetype='text/csv; charset=utf-8',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )

    @app.route('/api/export/xlsx', methods=['POST'])
    def api_export_xlsx():
        """API: 导出论文为 Excel xlsx（选中/跨页全选/收藏）"""
        data = request.get_json() or {}
        papers = _selection_query(data).all()
        if not papers:
            return jsonify({'success': False, 'message': '没有可导出的论文'}), 400

        import io
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = 'papers'
        ws.append(['Title', 'Authors', 'Abstract', 'Source', 'Venue', 'Year', 'Published', 'URL', 'PDF'])
        for p in papers:
            ws.append([
                p.title,
                '; '.join(p.authors or []),
                p.abstract or '',
                p.source,
                p.venue or '',
                p.year or '',
                p.published_date.strftime('%Y-%m-%d') if p.published_date else '',
                p.url or '',
                p.pdf_url or ''
            ])

        # 基础列宽，便于直接阅读
        for col, width in {'A': 60, 'B': 30, 'C': 80, 'D': 8, 'E': 20,
                           'F': 8, 'G': 12, 'H': 40, 'I': 40}.items():
            ws.column_dimensions[col].width = width
        ws.freeze_panes = 'A2'  # 冻结表头行

        buf = io.BytesIO()
        wb.save(buf)
        filename = f"papers_{datetime.now().strftime('%Y%m%d')}.xlsx"
        return Response(
            buf.getvalue(),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )

    @app.route('/api/export/bibtex', methods=['POST'])
    def api_export_bibtex():
        """API: 导出论文为 BibTeX（选中/跨页全选/收藏）"""
        data = request.get_json() or {}
        papers = _selection_query(data).all()
        if not papers:
            return jsonify({'success': False, 'message': '没有可导出的论文'}), 400

        import re
        entries = []
        used_keys = set()
        for p in papers:
            # BibTeX key：第一作者姓氏 + 年份 + 标题首词，冲突时追加 b/c/...
            first_author = (p.authors or ['Unknown'])[0]
            author_tokens = first_author.replace(',', ' ').split()
            surname = re.sub(r'[^a-zA-Z]', '', author_tokens[-1] if author_tokens else 'Unknown') or 'Unknown'
            year = str(p.year) if p.year else 'nd'
            title_words = p.title.split()
            first_word = re.sub(r'[^a-zA-Z0-9]', '', title_words[0] if title_words else 'Paper') or 'Paper'
            key = f'{surname}{year}{first_word}'
            suffix = ''
            while key + suffix in used_keys:
                suffix = chr(ord(suffix) + 1) if suffix else 'b'
            key += suffix
            used_keys.add(key)

            entry_type = 'article' if p.source == 'arxiv' else 'inproceedings'
            fields = [
                f'  title={{{p.title}}}',
                f'  author={{{" and ".join(p.authors or [])}}}'
            ]
            if p.year:
                fields.append(f'  year={{{p.year}}}')
            if p.venue:
                field_name = 'journal' if entry_type == 'article' else 'booktitle'
                fields.append(f'  {field_name}={{{p.venue}}}')
            if p.url:
                fields.append(f'  url={{{p.url}}}')
            entries.append(f'@{entry_type}{{{key},\n' + ',\n'.join(fields) + '\n}')

        content = '\n\n'.join(entries)
        filename = f"papers_{datetime.now().strftime('%Y%m%d')}.bib"
        return Response(
            content,
            mimetype='application/x-bibtex; charset=utf-8',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )

    @app.route('/api/domains', methods=['GET'])
    def api_domains():
        """API: 获取领域列表"""
        domains = Domain.query.all()
        return jsonify([d.to_dict() for d in domains])

    @app.route('/api/domains', methods=['POST'])
    def api_add_domain():
        """API: 添加新领域"""
        data = request.get_json()

        if not data or 'name' not in data:
            return jsonify({'success': False, 'message': '缺少领域名称'}), 400

        # 检查是否已存在
        existing = Domain.query.filter_by(name=data['name']).first()
        if existing:
            return jsonify({'success': False, 'message': '领域已存在'}), 400

        domain = Domain(
            name=data['name'],
            keywords=data.get('keywords', []),
            arxiv_categories=data.get('arxiv_categories', []),
            ccf_venues=data.get('ccf_venues', []),
            enabled=data.get('enabled', True),
            llm_prompt=data.get('llm_prompt')
        )

        try:
            db.session.add(domain)
            db.session.commit()
            return jsonify({'success': True, 'domain': domain.to_dict()})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/api/domains/<int:domain_id>', methods=['DELETE'])
    def api_delete_domain(domain_id):
        """API: 删除领域（包括所有论文）"""
        domain = Domain.query.get_or_404(domain_id)
        try:
            # 统计论文数量
            paper_count = Paper.query.filter_by(domain_id=domain_id).count()
            domain_name = domain.name

            # 删除领域（会级联删除所有论文）
            db.session.delete(domain)
            db.session.commit()

            # 记录删除操作到更新日志
            try:
                from scheduler import _create_update_log
                _create_update_log(
                    trigger_type='delete_domain',
                    domain_ids=[domain_id],
                    status='success',
                    operation_details={
                        'domain_name': domain_name,
                        'delete_papers': True
                    },
                    papers_affected=paper_count
                )
            except Exception as log_error:
                logger.error(f"记录删除领域日志失败: {log_error}")

            return jsonify({
                'success': True,
                'message': f'领域及 {paper_count} 篇论文已删除'
            })
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/api/domains/<int:domain_id>', methods=['GET', 'PUT', 'PATCH'])
    def api_update_domain(domain_id):
        """API: 获取或更新单个领域"""
        domain = Domain.query.get_or_404(domain_id)

        if request.method == 'GET':
            return jsonify({'success': True, 'domain': domain.to_dict()})

        data = request.get_json() or {}

        try:
            # 更新字段
            if 'name' in data:
                domain.name = data['name']
            if 'keywords' in data:
                domain.keywords = data['keywords']
            if 'arxiv_categories' in data:
                domain.arxiv_categories = data['arxiv_categories']
            if 'ccf_venues' in data:
                domain.ccf_venues = data['ccf_venues']
            if 'enabled' in data:
                domain.enabled = data['enabled']
            if 'llm_prompt' in data:
                domain.llm_prompt = data['llm_prompt']

            db.session.commit()
            return jsonify({'success': True, 'domain': domain.to_dict()})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/api/domains/<int:domain_id>/rescore', methods=['POST'])
    def api_rescore_domain_papers(domain_id):
        """API: 重新评分该领域的所有论文"""
        domain = Domain.query.get_or_404(domain_id)

        try:
            # 获取该领域的所有论文
            papers = Paper.query.filter_by(domain_id=domain_id).all()

            if not papers:
                return jsonify({'success': False, 'message': '该领域没有论文'}), 400

            # 导入评估器
            from scheduler import _get_llm_evaluator
            from llm import LLMEvaluatorError

            evaluator = _get_llm_evaluator(domain)

            if not evaluator:
                return jsonify({'success': False, 'message': 'LLM评估器未启用'}), 400

            results = {
                'total': len(papers),
                'success': 0,
                'failed': 0,
                'updated': []
            }

            for paper in papers:
                try:
                    # 评估论文
                    result = evaluator.evaluate(
                        title=paper.title,
                        abstract=paper.abstract or ''
                    )

                    if result.error:
                        results['failed'] += 1
                        results['updated'].append({
                            'id': paper.id,
                            'title': paper.title[:50],
                            'status': 'error',
                            'message': result.error
                        })
                        continue

                    # 更新评分
                    old_score = paper.llm_score
                    old_value_score = paper.llm_value_score
                    paper.llm_score = result.relevance_score
                    paper.llm_value_score = result.value_score

                    results['success'] += 1
                    results['updated'].append({
                        'id': paper.id,
                        'title': paper.title[:50],
                        'status': 'updated',
                        'old_relevance': old_score,
                        'old_value': old_value_score,
                        'new_relevance': result.relevance_score,
                        'new_value': result.value_score
                    })

                except LLMEvaluatorError as e:
                    results['failed'] += 1
                    results['updated'].append({
                        'id': paper.id,
                        'title': paper.title[:50],
                        'status': 'error',
                        'message': str(e)
                    })
                    continue

            # 提交更改
            db.session.commit()

            return jsonify({
                'success': True,
                'message': f'重新评分完成: 成功 {results["success"]} 篇, 失败 {results["failed"]} 篇',
                'results': results
            })

        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/api/domains/<int:domain_id>/rescore-stream', methods=['POST'])
    def api_rescore_domain_papers_stream(domain_id):
        """API: 重新评分该领域的所有论文（流式进度推送）

        特性：
        - 每评估完一篇论文立即保存，防止中断丢失结果
        - 使用并行评估提升速度
        - 实时推送进度更新
        - 记录完整的统计信息和分数变化详情
        """
        domain = Domain.query.get_or_404(domain_id)

        def generate_progress():
            """生成SSE进度事件"""
            import sys
            try:
                # 获取该领域的所有论文
                papers = Paper.query.filter_by(domain_id=domain_id).all()

                if not papers:
                    data = f"data: {json.dumps({'type': 'error', 'message': '该领域没有论文'})}\n\n"
                    yield data
                    sys.stdout.flush()
                    return

                # 导入评估器和并行处理
                from scheduler import _get_llm_evaluator
                from llm import LLMEvaluatorError
                from concurrent.futures import ThreadPoolExecutor, as_completed
                import queue
                import threading

                evaluator = _get_llm_evaluator(domain)

                if not evaluator:
                    data = f"data: {json.dumps({'type': 'error', 'message': 'LLM评估器未启用'})}\n\n"
                    yield data
                    sys.stdout.flush()
                    return

                total = len(papers)
                success_count = 0
                failed_count = 0
                filtered_count = 0

                # 记录分数变化详情
                score_changes = []

                # 发送开始事件
                data = f"data: {json.dumps({'type': 'start', 'total': total})}\n\n"
                yield data

                # 使用线程安全队列收集结果
                result_queue = queue.Queue()

                # 使用并行评估
                max_workers = min(Config.LLM_MAX_WORKERS, total)
                logger.info(f"使用 {max_workers} 个worker并行评估 {total} 篇论文")

                def evaluate_and_save(paper, index):
                    """评估单篇论文并立即保存"""
                    try:
                        # 记录原分数
                        old_relevance = paper.llm_score
                        old_value = paper.llm_value_score

                        result = _evaluate_single_paper({
                            'title': paper.title,
                            'abstract': paper.abstract or ''
                        }, evaluator, domain.id)

                        if result['success']:
                            # 始终更新评分到数据库（不管是否被过滤）
                            paper.llm_score = result.get('llm_score')
                            paper.llm_value_score = result.get('llm_value_score')

                            # 立即提交这一篇的更改
                            db.session.commit()

                            # 通过队列返回分数变化（线程安全）
                            # filtered 字段仍然保留用于统计显示
                            result_queue.put({
                                'index': index,
                                'success': True,
                                'filtered': result.get('filtered', False),
                                'paper_id': paper.id,
                                'title': paper.title,
                                'title_short': paper.title[:50],
                                'relevance': result.get('llm_score'),
                                'value': result.get('llm_value_score'),
                                'old_relevance': old_relevance,
                                'old_value': old_value,
                                'score_change': {
                                    'paper_id': paper.id,
                                    'title': paper.title,
                                    'old_relevance': old_relevance,
                                    'old_value': old_value,
                                    'new_relevance': result.get('llm_score'),
                                    'new_value': result.get('llm_value_score'),
                                    'filtered': result.get('filtered', False)
                                }
                            })
                        elif result.get('error'):
                            result_queue.put({
                                'index': index,
                                'success': False,
                                'filtered': False,
                                'paper_id': paper.id,
                                'title_short': paper.title[:50],
                                'error': result.get('error')
                            })
                    except Exception as e:
                        result_queue.put({
                            'index': index,
                            'success': False,
                            'filtered': False,
                            'paper_id': paper.id,
                            'title_short': paper.title[:50],
                            'error': str(e)
                        })

                # 使用线程池并行评估
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    # 提交所有任务
                    futures = {
                        executor.submit(evaluate_and_save, paper, i): paper
                        for i, paper in enumerate(papers, 1)
                    }

                    logger.info(f"已提交 {len(futures)} 个评估任务")

                    # 收集完成的任务并按顺序发送进度
                    completed_indices = set()
                    total_completed = 0

                    while len(completed_indices) < len(futures):
                        # 检查队列中的结果
                        try:
                            result = result_queue.get(timeout=0.1)
                            completed_indices.add(result['index'])
                            total_completed += 1

                            # 从队列结果中提取分数变化（线程安全）
                            if result.get('score_change'):
                                score_changes.append(result['score_change'])

                            if result.get('filtered'):
                                filtered_count += 1
                                relevance_threshold = getattr(Config, 'LLM_RELEVANCE_THRESHOLD', Config.LLM_FILTER_THRESHOLD)
                                value_threshold = getattr(Config, 'LLM_VALUE_THRESHOLD', Config.LLM_FILTER_THRESHOLD)
                                logger.debug(f"论文被过滤: {result.get('title_short')} (相关度: {result.get('relevance')} < {relevance_threshold} 或 价值: {result.get('value')} < {value_threshold})")
                                data = f"data: {json.dumps({
                                    'type': 'progress',
                                    'current': total_completed,
                                    'total': total,
                                    'percent': int(total_completed / total * 100),
                                    'paper_id': result.get('paper_id'),
                                    'title': result.get('title_short'),
                                    'filtered': True,
                                    'relevance': result.get('relevance'),
                                    'value': result.get('value')
                                })}\n\n"
                            elif not result.get('success'):
                                failed_count += 1
                                data = f"data: {json.dumps({
                                    'type': 'progress',
                                    'current': total_completed,
                                    'total': total,
                                    'percent': int(total_completed / total * 100),
                                    'paper_id': result.get('paper_id'),
                                    'title': result.get('title_short'),
                                    'error': result.get('error')
                                })}\n\n"
                            else:
                                success_count += 1
                                data = f"data: {json.dumps({
                                    'type': 'progress',
                                    'current': total_completed,
                                    'total': total,
                                    'percent': int(total_completed / total * 100),
                                    'paper_id': result.get('paper_id'),
                                    'title': result.get('title_short'),
                                    'relevance': result.get('relevance'),
                                    'value': result.get('value'),
                                    'old_relevance': result.get('old_relevance'),
                                    'old_value': result.get('old_value')
                                })}\n\n"

                            yield data

                        except queue.Empty:
                            # 队列为空，继续等待
                            continue

                # 记录重新评分操作到更新日志
                try:
                    from scheduler import _create_update_log

                    # 计算分数发生变化的论文数量
                    score_changed_count = sum(1 for c in score_changes if
                        (c.get('old_relevance') != c.get('new_relevance')) or
                        (c.get('old_value') != c.get('new_value'))
                    )

                    logger.info(f"重新评分完成: 总计={total}, 成功={success_count}, 失败={failed_count}, 被过滤={filtered_count}, 分数变化={score_changed_count}")

                    _create_update_log(
                        trigger_type='rescore',
                        domain_ids=[domain_id],
                        status='success' if failed_count == 0 else 'partial',
                        operation_details={
                            'domain_name': domain.name,
                            'total': total,
                            'success_count': success_count,
                            'failed_count': failed_count,
                            'filtered_count': filtered_count,
                            'score_changed_count': score_changed_count
                        },
                        papers_affected=success_count
                    )
                except Exception as log_error:
                    logger.error(f"记录重新评分日志失败: {log_error}")

                # 发送完成事件，包含完整统计和详情
                # 只返回前20条分数变化示例
                changes_sample = score_changes[:20] if score_changes else []

                data = f"data: {json.dumps({
                    'type': 'complete',
                    'total': total,
                    'success': success_count,
                    'failed': failed_count,
                    'filtered': filtered_count,
                    'score_changes_count': len(score_changes),
                    'score_changes_sample': changes_sample
                })}\n\n"
                yield data

            except Exception as e:
                db.session.rollback()
                data = f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                yield data

        return Response(
            stream_with_context(generate_progress()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no'
            }
        )

    @app.route('/api/domains/<int:domain_id>/remove-only', methods=['DELETE'])
    def api_remove_domain_only(domain_id):
        """API: 仅删除领域定义，保留论文"""
        domain = Domain.query.get_or_404(domain_id)

        try:
            # 获取该领域的论文数量
            paper_count = Paper.query.filter_by(domain_id=domain_id).count()
            domain_name = domain.name

            # 取消论文与领域的关联
            # 注意：我们需要设置domain_id为某个存在的领域ID，或者让该字段允许NULL
            # 这里我们创建一个"未分类"领域，如果不存在的话
            if paper_count > 0:
                # 查找或创建"未分类"领域
                uncategorized = Domain.query.filter_by(name='未分类').first()
                if not uncategorized:
                    uncategorized = Domain(
                        name='未分类',
                        keywords=[],
                        arxiv_categories=[],
                        ccf_venues=[],
                        enabled=False
                    )
                    db.session.add(uncategorized)
                    db.session.flush()  # 获取ID

                # 将所有论文移到"未分类"领域
                Paper.query.filter_by(domain_id=domain_id).update({'domain_id': uncategorized.id})

            # 删除领域
            db.session.delete(domain)
            db.session.commit()

            # 记录删除操作到更新日志
            try:
                from scheduler import _create_update_log
                _create_update_log(
                    trigger_type='delete_domain',
                    domain_ids=[domain_id],
                    status='success',
                    operation_details={
                        'domain_name': domain_name,
                        'delete_papers': False,
                        'moved_to_uncategorized': True
                    },
                    papers_affected=paper_count
                )
            except Exception as log_error:
                logger.error(f"记录删除领域日志失败: {log_error}")

            return jsonify({
                'success': True,
                'message': f'领域已删除，{paper_count} 篇论文已移至"未分类"',
                'papers_moved': paper_count
            })

        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/api/stats')
    def api_stats():
        """API: 统计信息"""
        # 整体统计
        stats = {
            'total_papers': Paper.query.count(),
            'total_domains': Domain.query.count(),
            'by_source': {},
            'by_year': {},
            'recent_papers': [],
            'domains': []
        }

        # 按数据源统计（整体）
        for source in ['arxiv', 'dblp', 's2']:
            stats['by_source'][source] = Paper.query.filter_by(source=source).count()

        # 按年份统计（整体）
        papers_by_year = db.session.query(
            Paper.year, db.func.count(Paper.id)
        ).filter(Paper.year.isnot(None)).group_by(Paper.year).order_by(Paper.year.desc()).limit(10).all()
        stats['by_year'] = {str(year): count for year, count in papers_by_year}

        # 最近论文（整体）
        recent = Paper.query.order_by(Paper.published_date.desc()).limit(5).all()
        stats['recent_papers'] = [p.to_dict() for p in recent]

        # 收藏数与新增统计
        now = get_beijing_time()
        stats['favorites_count'] = Paper.query.filter_by(is_favorite=True).count()
        stats['week_new'] = Paper.query.filter(
            Paper.fetched_date >= now - timedelta(days=7)
        ).count()
        stats['month_new'] = Paper.query.filter(
            Paper.fetched_date >= now - timedelta(days=30)
        ).count()

        # 整体平均评分
        avg_rel = db.session.query(db.func.avg(Paper.llm_score)).filter(
            Paper.llm_score.isnot(None)
        ).scalar()
        avg_val = db.session.query(db.func.avg(Paper.llm_value_score)).filter(
            Paper.llm_value_score.isnot(None)
        ).scalar()
        stats['avg_relevance'] = int(avg_rel) if avg_rel else None
        stats['avg_value'] = int(avg_val) if avg_val else None

        # 月度分布（按发布日期，最近 12 个月，缺失月份补零）
        month_col = db.func.strftime('%Y-%m', Paper.published_date)
        month_rows = db.session.query(
            month_col, db.func.count(Paper.id)
        ).filter(
            Paper.published_date.isnot(None)
        ).group_by(month_col).all()
        month_counts = {m: c for m, c in month_rows if m}
        by_month = {}
        cursor = now.replace(day=1)
        for _ in range(12):
            key = cursor.strftime('%Y-%m')
            by_month[key] = month_counts.get(key, 0)
            cursor = (cursor - timedelta(days=1)).replace(day=1)
        stats['by_month'] = dict(sorted(by_month.items()))

        # 相关度评分分布（5 个分档）
        score_dist = {}
        for label, lo, hi in [('0-19', 0, 19), ('20-39', 20, 39), ('40-59', 40, 59),
                              ('60-79', 60, 79), ('80-100', 80, 100)]:
            score_dist[label] = Paper.query.filter(
                Paper.llm_score.isnot(None),
                Paper.llm_score >= lo,
                Paper.llm_score <= hi
            ).count()
        stats['score_distribution'] = score_dist

        # 各领域详细统计
        domains = Domain.query.filter_by(enabled=True).all()
        for domain in domains:
            domain_papers = Paper.query.filter_by(domain_id=domain.id)

            # 按数据源统计
            by_source = {}
            for source in ['arxiv', 'dblp', 's2']:
                by_source[source] = domain_papers.filter_by(source=source).count()

            # 按年份统计
            by_year = db.session.query(
                Paper.year, db.func.count(Paper.id)
            ).filter(
                Paper.domain_id == domain.id,
                Paper.year.isnot(None)
            ).group_by(Paper.year).order_by(Paper.year.desc()).limit(5).all()
            by_year_dict = {str(year): count for year, count in by_year}

            # 评分统计
            avg_relevance = db.session.query(
                db.func.avg(Paper.llm_score)
            ).filter(
                Paper.domain_id == domain.id,
                Paper.llm_score.isnot(None)
            ).scalar()
            avg_relevance = int(avg_relevance) if avg_relevance else None

            avg_value = db.session.query(
                db.func.avg(Paper.llm_value_score)
            ).filter(
                Paper.domain_id == domain.id,
                Paper.llm_value_score.isnot(None)
            ).scalar()
            avg_value = int(avg_value) if avg_value else None

            stats['domains'].append({
                'id': domain.id,
                'name': domain.name,
                'color': domain_color(domain.id),
                'total': domain_papers.count(),
                'by_source': by_source,
                'by_year': by_year_dict,
                'avg_relevance': avg_relevance,
                'avg_value': avg_value,
                'enabled': domain.enabled
            })

        return jsonify(stats)

    @app.route('/api/trigger', methods=['POST'])
    def api_trigger():
        """API: 手动触发抓取"""
        domain_id = None
        if request.is_json:
            data = request.get_json() or {}
            domain_id = data.get('domain_id')

        result = manual_trigger_fetch(domain_id)
        status_code = 200 if result['success'] else 500
        return jsonify(result), status_code

    @app.route('/api/schedule')
    def api_schedule():
        """API: 获取调度信息"""
        next_run = get_next_run_time()
        return jsonify({
            'next_run': next_run.isoformat() if next_run else None,
            'schedule_hour': Config.SCHEDULE_HOUR,
            'schedule_minute': Config.SCHEDULE_MINUTE
        })

    @app.route('/api/translate', methods=['POST'])
    def api_translate():
        """API: 使用 Azure → DeepSeek → MyMemory 服务链翻译论文文本。"""
        data = request.get_json(silent=True) or {}
        text = data.get('text')
        if not isinstance(text, str) or not text.strip():
            return jsonify({'success': False, 'message': '待翻译文本不能为空'}), 400
        if len(text) > 12000:
            return jsonify({'success': False, 'message': '单次翻译文本不能超过 12000 个字符'}), 400

        try:
            result = app.extensions['translation_service'].translate(text)
            return jsonify({
                'success': True,
                'translated_text': result.text,
                'provider': result.provider,
            })
        except TranslationError:
            logger.exception('All translation providers failed')
            return jsonify({
                'success': False,
                'message': '翻译服务暂时不可用，请稍后重试',
            }), 503

    @app.route('/api/clear', methods=['POST'])
    def api_clear():
        """API: 清空所有论文数据"""
        try:
            # 获取清空选项
            data = request.get_json() or {}
            clear_domains = data.get('clear_domains', False)
            keep_domains = data.get('keep_domains', True)

            # 统计要删除的数据
            paper_count = Paper.query.count()
            domain_count = 0

            # 删除论文
            Paper.query.delete()

            # 如果选择也删除领域
            if clear_domains and not keep_domains:
                domain_count = Domain.query.count()
                Domain.query.delete()

            db.session.commit()

            # 记录清空操作到更新日志
            log = UpdateLog(
                trigger_type='data_cleared',
                total_new=0,
                arxiv_new=0,
                dblp_new=0,
                source_stats={'cleared_papers': paper_count, 'cleared_domains': domain_count},
                domains_processed=[],
                status='success'
            )
            db.session.add(log)
            db.session.commit()

            result = {
                'success': True,
                'message': f'已清空 {paper_count} 篇论文' + (f'和 {domain_count} 个领域' if clear_domains else ''),
                'papers_deleted': paper_count,
                'domains_deleted': domain_count if clear_domains else 0
            }
            return jsonify(result), 200

        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/api/batch/favorite', methods=['POST'])
    def api_batch_favorite():
        """API: 批量收藏"""
        data = request.get_json() or {}
        paper_ids = data.get('paper_ids', [])

        if not paper_ids and not data.get('select_all'):
            return jsonify({'success': False, 'message': '请选择要收藏的论文'}), 400

        try:
            count = _selection_query(data).update(
                {Paper.is_favorite: True},
                synchronize_session=False
            )
            db.session.commit()
            return jsonify({
                'success': True,
                'message': f'已收藏 {count} 篇论文',
                'count': count
            })
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/api/batch/unfavorite', methods=['POST'])
    def api_batch_unfavorite():
        """API: 批量取消收藏"""
        data = request.get_json() or {}
        paper_ids = data.get('paper_ids', [])

        if not paper_ids and not data.get('select_all'):
            return jsonify({'success': False, 'message': '请选择要取消收藏的论文'}), 400

        try:
            count = _selection_query(data).update(
                {Paper.is_favorite: False},
                synchronize_session=False
            )
            db.session.commit()
            return jsonify({
                'success': True,
                'message': f'已取消收藏 {count} 篇论文',
                'count': count
            })
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/api/batch/delete', methods=['POST'])
    def api_batch_delete():
        """API: 批量删除论文"""
        data = request.get_json() or {}
        paper_ids = data.get('paper_ids', [])

        if not paper_ids and not data.get('select_all'):
            return jsonify({'success': False, 'message': '请选择要删除的论文'}), 400

        try:
            # 获取要删除的论文信息（用于记录日志）
            papers_to_delete = _selection_query(data).all()

            if not papers_to_delete:
                return jsonify({'success': False, 'message': '未找到要删除的论文'}), 400

            # 按来源和领域统计
            source_stats = {}
            domain_stats = {}
            for paper in papers_to_delete:
                source = paper.source
                source_stats[source] = source_stats.get(source, 0) + 1
                domain_id = paper.domain_id
                domain_stats[domain_id] = domain_stats.get(domain_id, 0) + 1

            # 逐个删除论文（更安全，确保会话同步）
            count = 0
            for paper in papers_to_delete:
                db.session.delete(paper)
                count += 1

            db.session.commit()

            # 记录删除操作到更新日志
            log = UpdateLog(
                trigger_type='papers_deleted',
                total_new=0,
                arxiv_new=0,
                dblp_new=0,
                source_stats={
                    'deleted_papers': count,
                    'by_source': source_stats,
                    'by_domain': domain_stats
                },
                domains_processed=list(domain_stats.keys()),
                status='success'
            )
            db.session.add(log)
            db.session.commit()

            return jsonify({
                'success': True,
                'message': f'已删除 {count} 篇论文',
                'count': count
            })
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/favorites')
    def favorites():
        """收藏页面"""
        page = request.args.get('page', 1, type=int)
        per_page = Config.PAPERS_PER_PAGE

        # 只显示收藏的论文
        query = Paper.query.filter_by(is_favorite=True).order_by(Paper.published_date.desc())
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        papers = pagination.items

        # 获取所有领域
        domains = Domain.query.filter_by(enabled=True).all()

        # 构建查询字符串
        query_params = {k: v for k, v in request.args.items() if k != 'page'}
        query_string = '&' + '&'.join(f'{k}={v}' for k, v in query_params.items()) if query_params else ''

        return render_template('favorites.html',
                             papers=papers,
                             domains=domains,
                             page=page,
                             total=pagination.total,
                             per_page=per_page,
                             total_pages=pagination.pages,
                             query_string=query_string)

    @app.route('/update-logs')
    def update_logs():
        """更新日志页面"""
        page = request.args.get('page', 1, type=int)
        per_page = 20

        # 获取更新日志，按时间倒序
        query = UpdateLog.query.order_by(UpdateLog.trigger_time.desc())
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        logs = pagination.items

        # 获取所有领域名称（用于显示）
        domains = Domain.query.all()
        domain_names = {d.id: d.name for d in domains}

        # 构建查询字符串
        query_params = {k: v for k, v in request.args.items() if k != 'page'}
        query_string = '&' + '&'.join(f'{k}={v}' for k, v in query_params.items()) if query_params else ''

        return render_template('update_logs.html',
                             logs=logs,
                             domain_names=domain_names,
                             page=page,
                             total=pagination.total,
                             per_page=per_page,
                             total_pages=pagination.pages,
                             query_string=query_string)

    @app.route('/api/update-logs')
    def api_update_logs():
        """API: 获取更新日志"""
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)

        query = UpdateLog.query.order_by(UpdateLog.trigger_time.desc())
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)

        return jsonify({
            'logs': [log.to_dict() for log in pagination.items],
            'total': pagination.total,
            'pages': pagination.pages,
            'current_page': page
        })

    @app.errorhandler(404)
    def not_found(e):
        return render_template('base.html', content='<h1>页面未找到</h1>'), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template('base.html', content='<h1>服务器错误</h1>'), 500


# 创建应用实例
app = create_app()

if __name__ == '__main__':
    # 后台常驻（任务计划/服务）时设 FLASK_DEBUG=0，避免 reloader 派生子进程导致进程管理混乱
    debug = os.environ.get('FLASK_DEBUG', '1').lower() in ('1', 'true', 'yes')
    app.run(host='0.0.0.0', port=5000, debug=debug, use_reloader=debug)
