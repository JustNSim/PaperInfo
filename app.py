"""
PaperInfo - 论文调研工具
主应用入口
"""
import os
import json
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from sqlalchemy.exc import IntegrityError

from config import Config, config
from models import db, Domain, Paper, UpdateLog
from scheduler import setup_scheduler, manual_trigger_fetch, get_next_run_time

# 创建 Flask 应用
def create_app(config_name='default'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])

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

    return app


def _init_default_domains():
    """初始化默认领域"""
    for domain_config in Config.DEFAULT_DOMAINS:
        existing = Domain.query.filter_by(name=domain_config['name']).first()
        if not existing:
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
    except Exception as e:
        db.session.rollback()
        print(f"初始化默认领域时出错: {e}")


def register_routes(app):
    """注册所有路由"""

    @app.route('/stats')
    def stats():
        """统计页面"""
        return render_template('stats.html')

    @app.route('/')
    def index():
        """主页 - 论文列表"""
        # 获取筛选参数
        page = request.args.get('page', 1, type=int)
        per_page = Config.PAPERS_PER_PAGE
        domain_id = request.args.get('domain', type=int)
        keyword = request.args.get('q', '').strip()
        sources = request.args.get('sources', '').split(',') if request.args.get('sources') else []
        year = request.args.get('year', type=int)
        sort = request.args.get('sort', 'date')  # date, title

        # 构建查询
        query = Paper.query

        # 领域过滤
        if domain_id:
            query = query.filter_by(domain_id=domain_id)

        # 数据源过滤
        if sources:
            query = query.filter(Paper.source.in_(sources))

        # 年份过滤
        if year:
            query = query.filter_by(year=year)

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
        else:  # date
            query = query.order_by(Paper.published_date.desc())

        # 分页
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        papers = pagination.items

        # 获取所有领域
        domains = Domain.query.filter_by(enabled=True).all()

        # 获取可用年份列表
        years = db.session.query(Paper.year).filter(
            Paper.year.isnot(None)
        ).distinct().order_by(Paper.year.desc()).all()
        years = [y[0] for y in years]

        # 构建查询字符串（用于分页链接）
        query_params = {k: v for k, v in request.args.items() if k != 'page'}
        query_string = '&' + '&'.join(f'{k}={v}' for k, v in query_params.items()) if query_params else ''

        # 获取当前领域名称
        domain_name = None
        if domain_id:
            domain = Domain.query.get(domain_id)
            if domain:
                domain_name = domain.name

        # 获取最后更新时间
        last_update = None
        if papers:
            last_update = papers[0].fetched_date.strftime('%Y-%m-%d %H:%M')

        # 获取下次运行时间
        next_run = None
        if get_next_run_time():
            next_run = get_next_run_time().strftime('%Y-%m-%d %H:%M')

        return render_template('index.html',
                             papers=papers,
                             domains=domains,
                             domain_id=domain_id,
                             domain_name=domain_name,
                             years=years,
                             year=year,
                             sources=sources,
                             sort=sort,
                             page=page,
                             total=pagination.total,
                             per_page=per_page,
                             total_pages=pagination.pages,
                             query_string=query_string,
                             last_update=last_update,
                             next_run=next_run)

    @app.route('/api/papers')
    def api_papers():
        """API: 获取论文列表"""
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', Config.PAPERS_PER_PAGE, type=int)
        domain_id = request.args.get('domain', type=int)

        query = Paper.query
        if domain_id:
            query = query.filter_by(domain_id=domain_id)

        query = query.order_by(Paper.published_date.desc())
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)

        return jsonify({
            'papers': [p.to_dict() for p in pagination.items],
            'total': pagination.total,
            'pages': pagination.pages,
            'current_page': page
        })

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
            enabled=data.get('enabled', True)
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
        """API: 删除领域"""
        domain = Domain.query.get_or_404(domain_id)
        try:
            db.session.delete(domain)
            db.session.commit()
            return jsonify({'success': True, 'message': '领域已删除'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/api/stats')
    def api_stats():
        """API: 统计信息"""
        stats = {
            'total_papers': Paper.query.count(),
            'total_domains': Domain.query.count(),
            'by_source': {},
            'by_year': {},
            'recent_papers': []
        }

        # 按数据源统计
        for source in ['arxiv', 'dblp']:
            stats['by_source'][source] = Paper.query.filter_by(source=source).count()

        # 按年份统计
        papers_by_year = db.session.query(
            Paper.year, db.func.count(Paper.id)
        ).filter(Paper.year.isnot(None)).group_by(Paper.year).order_by(Paper.year.desc()).limit(10).all()
        stats['by_year'] = {str(year): count for year, count in papers_by_year}

        # 最近论文
        recent = Paper.query.order_by(Paper.published_date.desc()).limit(5).all()
        stats['recent_papers'] = [p.to_dict() for p in recent]

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

        if not paper_ids:
            return jsonify({'success': False, 'message': '请选择要收藏的论文'}), 400

        try:
            count = Paper.query.filter(Paper.id.in_(paper_ids)).update(
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

        if not paper_ids:
            return jsonify({'success': False, 'message': '请选择要取消收藏的论文'}), 400

        try:
            count = Paper.query.filter(Paper.id.in_(paper_ids)).update(
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

        if not paper_ids:
            return jsonify({'success': False, 'message': '请选择要删除的论文'}), 400

        try:
            # 获取要删除的论文信息（用于记录日志）
            papers_to_delete = Paper.query.filter(Paper.id.in_(paper_ids)).all()

            # 按来源和领域统计
            source_stats = {}
            domain_stats = {}
            for paper in papers_to_delete:
                source = paper.source
                source_stats[source] = source_stats.get(source, 0) + 1
                domain_id = paper.domain_id
                domain_stats[domain_id] = domain_stats.get(domain_id, 0) + 1

            # 执行删除
            count = Paper.query.filter(Paper.id.in_(paper_ids)).delete(
                synchronize_session=False
            )
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
    app.run(host='0.0.0.0', port=5000, debug=True)
