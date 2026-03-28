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

    # 爬虫配置
    ARXIV_DELAY = 4                 # arXiv API 请求间隔（秒）
    DBLP_DELAY = 3                  # DBLP API 请求间隔（秒）
    S2_DELAY = 5                    # Semantic Scholar API 请求间隔（秒）
    MAX_PAPERS_PER_SOURCE = 300     # 每个数据源最大抓取论文数（提高到300以支持分批查询）
    REQUEST_TIMEOUT = 30            # 请求超时时间（秒）

    # 时间范围配置
    FETCH_DAYS_BACK = 30            # 首次运行获取最近 N 天的论文
    INCREMENTAL_UPDATE = True       # 是否启用增量更新（只抓取上次更新后的新论文）
    ENABLE_TIME_FILTER = True       # 是否启用时间过滤

    # LLM 评估器配置
    LLM_FILTER_ENABLED = os.environ.get('LLM_FILTER_ENABLED', 'true').lower() == 'true'
    LLM_FILTER_THRESHOLD = int(os.environ.get('LLM_FILTER_THRESHOLD', '75'))
    LLM_PROVIDER = os.environ.get('LLM_PROVIDER', 'openai')  # LLM 提供商: 'openai', 'anthropic', 'zhipu', 'custom'
    LLM_MODEL = os.environ.get('LLM_MODEL')  # LLM 模型名称 (None 使用默认模型)
    LLM_DELAY = float(os.environ.get('LLM_DELAY', '1.0'))  # LLM API 请求间隔（秒）
    LLM_API_KEY = None  # LLM API 密钥 (从环境变量自动读取)

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
    LLM_SYSTEM_PROMPT = (
        "You are an academic research assistant. Evaluate the relevance of the following paper "
        "to this specific research context: Automated smart contract vulnerability repair using "
        "multi-agent systems, LLM-based software engineering, and the analysis of real-world DeFi "
        "exploit incidents for benchmark datasets. Score the relevance from 0 to 100. Return ONLY "
        "the integer score."
    )

    # API 端点
    ARXIV_API_URL = 'http://export.arxiv.org/api/query'
    DBLP_API_URL = 'https://dblp.org/search/publ/api'

    # 分页配置
    PAPERS_PER_PAGE = 20

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
            # CCF-A 类会议/期刊：安全、软工、AI、网络、分布式（不含密码学）
            'ccf_venues': [
                # === 安全领域会议 (CCF-A) ===
                'IEEE S&P', 'Oakland', 'IEEE Symposium on Security and Privacy',
                'ACM CCS', 'ACM Conference on Computer and Communications Security',
                'USENIX Security', 'Usenix Security Symposium',
                'NDSS', 'Network and Distributed System Security Symposium',

                # === 安全领域期刊 (CCF-A) ===
                'IEEE TDSC', 'IEEE Transactions on Dependable and Secure Computing',
                'IEEE TIFS', 'IEEE Transactions on Information Forensics and Security',
                'ACM TIFS', 'ACM Transactions on Information and System Security',

                # === 软件工程会议 (CCF-A) ===
                'ICSE', 'International Conference on Software Engineering',
                'FSE', 'ESEC/FSE', 'ACM SIGSOFT Symposium on the Foundations of Software Engineering',
                'ASE', 'International Conference on Automated Software Engineering',
                'ISSTA', 'International Symposium on Software Testing and Analysis',
                'OOPSLA', 'Object-Oriented Programming, Systems, Languages & Applications',

                # === 软件工程期刊 (CCF-A) ===
                'IEEE TSE', 'IEEE Transactions on Software Engineering',
                'ACM TOSEM', 'ACM Transactions on Software Engineering and Methodology',

                # === AI会议 (CCF-A) ===
                'AAAI', 'IJCAI', 'ICML', 'NeurIPS', 'ACL',

                # === AI期刊 (CCF-A) ===
                'IEEE TPAMI', 'IEEE Transactions on Pattern Analysis and Machine Intelligence',
                'JMLR', 'Journal of Machine Learning Research',

                # === 网络/系统会议 (CCF-A) ===
                'SIGCOMM', 'MobiCom',
                'OSDI', 'USENIX Symposium on Operating Systems Design and Implementation',
                'SOSP', 'ACM Symposium on Operating Systems Principles',

                # === 网络期刊 (CCF-A) ===
                'IEEE JSAC', 'Journal on Selected Areas in Communications',
                'IEEE TWC', 'IEEE Transactions on Wireless Communications',
                'IEEE TMC', 'IEEE Transactions on Mobile Computing',

                # === 交叉领域会议 (CCF-A) ===
                'WWW', 'The Web Conference', 'International World Wide Web Conferences',
                'CAI', 'ACM International Conference on AI',
                'SEC', 'ACM Conference on Data and Application Security and Privacy'
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
                # 软件工程会议 (CCF-A)
                'ICSE', 'FSE', 'ESEC/FSE', 'ASE', 'ISSTA', 'OOPSLA',
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
