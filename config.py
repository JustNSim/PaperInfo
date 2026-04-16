"""
PaperInfo 全局配置文件
"""
import os
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()


class Config:
    """应用配置类"""

    # Flask 基础配置
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))

    # 数据库配置
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{os.path.join(BASE_DIR, "data", "papers.db")}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 定时任务配置
    SCHEDULE_HOUR = 2       # 每天更新时间：小时 (24小时制)
    SCHEDULE_MINUTE = 0     # 每天更新时间：分钟
    CATCH_UP_ENABLED = True  # 是否启用补执行机制（启动时检查并执行错过的定时任务）

    # 爬虫配置
    ARXIV_DELAY = 4                 # arXiv API 请求间隔（秒）
    DBLP_DELAY = 3                  # DBLP API 请求间隔（秒）
    S2_DELAY = 5                    # Semantic Scholar API 请求间隔（秒）
    # S2 API Key 轮换：注册 https://www.semanticscholar.org/product/api#api-key-form 获取免费 Key
    # 多个 Key 逗号分隔，轮换使用（每个 Key 独立限流）
    S2_API_KEYS = [k.strip() for k in os.environ.get('S2_API_KEYS', '').split(',') if k.strip()]
    MAX_PAPERS_PER_SOURCE = 300     # 每个数据源最大抓取论文数（提高到300以支持分批查询）
    REQUEST_TIMEOUT = 30            # 请求超时时间（秒）

    # DBLP 时间窗口（DBLP 论文批量入库，不适合增量更新）
    DBLP_YEAR_WINDOW = int(os.environ.get('DBLP_YEAR_WINDOW', '2'))  # 查最近 N 年的论文

    # 时间范围配置
    FETCH_DAYS_BACK = 30            # 首次运行获取最近 N 天的论文
    INCREMENTAL_UPDATE = True       # 是否启用增量更新（只抓取上次更新后的新论文）
    ENABLE_TIME_FILTER = True       # 是否启用时间过滤

    # LLM 评估器配置
    LLM_FILTER_ENABLED = os.environ.get('LLM_FILTER_ENABLED', 'true').lower() == 'true'

    # 分数阈值（相关性分和相关度分需要分别达到阈值才会保存）
    LLM_RELEVANCE_THRESHOLD = int(os.environ.get('LLM_RELEVANCE_THRESHOLD', '30'))  # 相关性阈值
    LLM_VALUE_THRESHOLD = int(os.environ.get('LLM_VALUE_THRESHOLD', '30'))  # 价值阈值

    # 兼容旧配置（如果只有单一阈值，则两个阈值都使用该值）
    LLM_FILTER_THRESHOLD = int(os.environ.get('LLM_FILTER_THRESHOLD', '30'))

    LLM_PROVIDER = os.environ.get('LLM_PROVIDER', 'openai')  # LLM 提供商: 'openai', 'anthropic', 'zhipu', 'custom'
    LLM_MODEL = os.environ.get('LLM_MODEL')  # LLM 模型名称 (None 使用默认模型)
    LLM_DELAY = float(os.environ.get('LLM_DELAY', '1.0'))  # LLM API 请求间隔（秒）
    LLM_API_KEY = None  # LLM API 密钥 (从环境变量自动读取)

    # LLM 并行配置
    LLM_PARALLEL_ENABLED = os.environ.get('LLM_PARALLEL_ENABLED', 'true').lower() == 'true'  # 是否启用并行评估
    LLM_MAX_WORKERS = int(os.environ.get('LLM_MAX_WORKERS', '5'))  # 最大并发线程数
    LLM_EVALUATION_TIMEOUT = int(os.environ.get('LLM_EVALUATION_TIMEOUT', '30'))  # 单次评估超时（秒）

    # 自定义 LLM API 配置 (用于第三方 OpenAI 兼容 API)
    CUSTOM_LLM_API_KEY = os.environ.get('CUSTOM_LLM_API_KEY')  # 自定义 API 密钥
    CUSTOM_LLM_BASE_URL = os.environ.get('CUSTOM_LLM_BASE_URL')  # 自定义 API 基础 URL
    CUSTOM_LLM_MODEL = os.environ.get('CUSTOM_LLM_MODEL')  # 自定义模型名称

    # LLM 默认模型配置
    LLM_DEFAULT_MODELS = {
        'openai': 'gpt-4o-mini',           # OpenAI: 快速且便宜
        'anthropic': 'claude-3-5-haiku-20241022',  # Anthropic: 快速且便宜
        'zhipu': 'glm-4-flash',            # Zhipu: 快速且免费额度高
        'custom': 'gpt-4o-mini'            # Custom: 使用 CUSTOM_LLM_MODEL 环境变量
    }

    # LLM 系统提示词 (用于评估论文相关性)

    # 通用默认prompt（适用于所有学术领域）
    LLM_DEFAULT_PROMPT = (
        "You are an academic research assistant. Evaluate the following paper on two dimensions:\n"
        "1. Relevance (相关性): How relevant the paper is to the research area and topics of interest. "
        "Consider keyword matching, domain alignment, and subject matter relevance. Score from 0 to 100.\n"
        "2. Value (价值): Academic merit including novelty, innovation, technical soundness, "
        "practical applicability, and significance to the broader research community. Score from 0 to 100.\n\n"
        "Return your response in the format: \"Relevance: XX, Value: YY\" where XX and YY are "
        "integer scores. Return ONLY the scores, no other text."
    )

    # 环境变量自定义prompt（优先级最高）
    LLM_SYSTEM_PROMPT = os.environ.get('LLM_SYSTEM_PROMPT', LLM_DEFAULT_PROMPT)

    # 各领域的预设prompt（可以作为示例）
    LLM_DOMAIN_PROMPTS = {
        'smart_contract_repair': (
            "You are an academic research assistant specializing in blockchain security. "
            "Evaluate the following paper on two dimensions:\n"
            "1. Relevance (相关性): How relevant the paper is to: Automated smart contract "
            "vulnerability repair using multi-agent systems, LLM-based software engineering, "
            "and the analysis of real-world DeFi exploit incidents. Score from 0 to 100.\n"
            "2. Value (价值): Academic merit including novelty, innovation, technical soundness, "
            "and practical applicability in blockchain security. Score from 0 to 100.\n\n"
            "Return your response in the format: \"Relevance: XX, Value: YY\". Return ONLY the scores."
        ),
        'llm_automation': (
            "You are an academic research assistant specializing in AI and software engineering. "
            "Evaluate the following paper on two dimensions:\n"
            "1. Relevance (相关性): How relevant the paper is to: Multi-agent systems, automated "
            "program repair, LLM-based code generation, and intelligent software engineering. "
            "Score from 0 to 100.\n"
            "2. Value (价值): Academic merit including novelty, innovation, technical soundness, "
            "and contribution to AI/SE research. Score from 0 to 100.\n\n"
            "Return your response in the format: \"Relevance: XX, Value: YY\". Return ONLY the scores."
        ),
        'general_cs': (
            "You are an academic research assistant. Evaluate the following computer science paper "
            "on two dimensions:\n"
            "1. Relevance (相关性): General relevance to computer science research and current "
            "trends in the field. Score from 0 to 100.\n"
            "2. Value (价值): Academic merit including novelty, methodology rigor, experimental "
            "validation, and contribution to the field. Score from 0 to 100.\n\n"
            "Return your response in the format: \"Relevance: XX, Value: YY\". Return ONLY the scores."
        )
    }

    # API 端点
    ARXIV_API_URL = 'http://export.arxiv.org/api/query'
    DBLP_API_URL = 'https://dblp.org/search/publ/api'

    # 分页配置
    PAPERS_PER_PAGE = 10

    # 预置领域配置
    DEFAULT_DOMAINS = [
        {
            'name': '区块链智能合约',
            # 关键词：涵盖核心概念、技术术语、应用场景
            'keywords': [
                # 核心概念
                'blockchain', 'smart contract', 'decentralized', 'decentralisation',
                'consensus', 'distributed ledger', 'cryptocurrency', 'virtual currency',
                # 平币和协议
                'bitcoin', 'ethereum', 'solidity', 'web3', 'web 3.0',
                # 应用场景
                'defi', 'decentralized finance', 'nft', 'non-fungible token', 'dao',
                'decentralized autonomous organization',
                # 技术术语
                'zero-knowledge', 'zk-snark', 'zk-stark', 'merkle tree', 'hash function',
                'byzantine fault', 'pbft', 'proof of work', 'proof of stake',
                'sharding', 'layer 2', 'rollup', 'sidechain',
                # 安全相关
                'smart contract security', 'reentrancy', 'flash loan attack',
                '51% attack', 'double spending', 'selfish mining'
            ],
            # arXiv 分类：密码学、分布式计算、博弈论
            'arxiv_categories': [
                'cs.CR',      # Cryptography and Security
                'cs.DC',      # Distributed, Parallel, and Cluster Computing
                'cs.GT',      # Computer Science and Game Theory
                'cs.SC',      # Symbolic Computation
                'cs.IT'       # Information Theory
            ],
            # CCF-A 类会议/期刊：只保留 DBLP 能识别的短名，去掉冗余全名别名
            'ccf_venues': [
                # === 安全领域会议 (CCF-A) ===
                'IEEE S&P', 'USENIX Security', 'NDSS',

                # === 安全领域期刊 (CCF-A) ===
                'IEEE TDSC', 'IEEE TIFS',

                # === 软件工程会议 (CCF-A) ===
                'ICSE', 'FSE', 'ASE', 'ISSTA', 'OOPSLA',

                # === 软件工程期刊 (CCF-A) ===
                'IEEE TSE', 'ACM TOSEM',

                # === AI会议 (CCF-A) ===
                'AAAI', 'IJCAI', 'ICML', 'NeurIPS', 'ACL',

                # === AI期刊 (CCF-A) ===
                'IEEE TPAMI', 'JMLR',

                # === 网络/系统会议 (CCF-A) ===
                'SIGCOMM', 'MobiCom', 'OSDI', 'SOSP',

                # === 网络期刊 (CCF-A) ===
                'IEEE JSAC', 'IEEE TWC', 'IEEE TMC',

                # === 交叉领域会议 (CCF-A) ===
                'WWW', 'CAI', 'SEC'
            ]
        },
        {
            'name': '大模型与自动化修复',
            'keywords': [
                'multi-agent', 'agent-based', 'autonomous agent', 'LLM agent',
                'automated program repair', 'APR', 'vulnerability repair', 'code generation',
                'software engineering', 'prompt engineering', 'root cause analysis'
            ],
            'arxiv_categories': [
                'cs.SE',  # Software Engineering
                'cs.AI',  # Artificial Intelligence
                'cs.LG'   # Machine Learning
            ],
            'ccf_venues': [
                # 软件工程会议 (CCF-A) — 只保留 DBLP 短名
                'ICSE', 'FSE', 'ASE', 'ISSTA', 'OOPSLA',
                'IEEE TSE', 'ACM TOSEM',
                # AI会议 (CCF-A)
                'AAAI', 'IJCAI', 'ICML', 'NeurIPS', 'ACL'
            ]
        }
    ]


class DevelopmentConfig(Config):
    """开发环境配置"""
    DEBUG = True
    TESTING = False


class ProductionConfig(Config):
    """生产环境配置"""
    DEBUG = False
    TESTING = False
    # 生产环境应从环境变量读取密钥
    SECRET_KEY = os.environ.get('SECRET_KEY')


# 配置字典
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
