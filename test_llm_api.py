"""安全地测试 GLM、DeepSeek 以及自动降级链路，不输出 API Key。"""
import sys

from config import Config
from llm.evaluator import (
    CustomOpenAIProvider,
    LLMEvaluator,
    LLMEvaluatorError,
    RateLimitError,
)


SYSTEM_PROMPT = (
    'Evaluate the paper and return ONLY: "Relevance: XX, Value: YY" '
    'where both values are integers from 0 to 100.'
)
TEST_TITLE = 'Agent Memory for Reliable Language Model Applications'
TEST_ABSTRACT = (
    'This paper studies long-term memory architectures, retrieval, consolidation, '
    'and evaluation for autonomous language-model agents.'
)


def classify_error(error):
    """返回不包含响应正文或凭据的错误分类。"""
    message = str(error).lower()
    if '429' in message or 'rate limit' in message or 'usage limit' in message:
        return 'RATE_LIMITED'
    if '401' in message or 'unauthorized' in message or 'authentication' in message:
        return 'AUTH_FAILED'
    if '404' in message or 'not found' in message:
        return 'ENDPOINT_OR_MODEL_NOT_FOUND'
    if 'timeout' in message or 'timed out' in message:
        return 'TIMEOUT'
    if 'connection' in message or 'dns' in message or 'name resolution' in message:
        return 'CONNECTION_FAILED'
    return type(error).__name__.upper()


def test_provider(label, api_key, base_url, model):
    missing = [
        name for name, value in (
            ('API key', api_key), ('base URL', base_url), ('model', model)
        ) if not value
    ]
    print(f'[{label}] endpoint={base_url or "<missing>"}, model={model or "<missing>"}, key_set={bool(api_key)}')
    if missing:
        print(f'[{label}] CONFIG_INCOMPLETE: {", ".join(missing)}')
        return False

    provider = CustomOpenAIProvider(
        api_key=api_key,
        base_url=base_url,
        model=model,
        delay=0,
        provider_name=label,
        timeout=Config.LLM_EVALUATION_TIMEOUT,
        extra_body=(
            {'thinking': {'type': 'disabled'}} if label == 'DeepSeek' else None
        ),
    )
    try:
        result = provider.evaluate(TEST_TITLE, TEST_ABSTRACT, SYSTEM_PROMPT)
        print(
            f'[{label}] OK: relevance={result.relevance_score}, '
            f'value={result.value_score}'
        )
        return True
    except (RateLimitError, LLMEvaluatorError, Exception) as error:
        print(f'[{label}] FAILED: {classify_error(error)}')
        return False


def test_failover():
    evaluator = LLMEvaluator(
        provider='custom',
        api_key=Config.GLM_API_KEY,
        base_url=Config.CUSTOM_GLM_BASE_URL,
        model=Config.CUSTOM_GLM_MODEL,
        provider_label='GLM',
        fallback_api_key=Config.DEEPSEEK_API_KEY,
        fallback_base_url=Config.CUSTOM_DEEPSEEK_BASE_URL,
        fallback_model=Config.CUSTOM_DEEPSEEK_MODEL,
        fallback_label='DeepSeek',
        fallback_extra_body={'thinking': {'type': 'disabled'}},
        failure_cooldown=Config.LLM_PRIMARY_FAILURE_COOLDOWN,
        delay=0,
        timeout=Config.LLM_EVALUATION_TIMEOUT,
        system_prompt=SYSTEM_PROMPT,
    )
    result = evaluator.evaluate(TEST_TITLE, TEST_ABSTRACT)
    if result.error:
        print(f'[FAILOVER] FAILED: {classify_error(result.error)}')
        return False

    print(
        f'[FAILOVER] OK: provider={result.provider}, model={result.model}, '
        f'relevance={result.relevance_score}, value={result.value_score}'
    )
    return True


if __name__ == '__main__':
    print('PaperInfo dual-LLM connectivity test')
    glm_ok = test_provider(
        'GLM', Config.GLM_API_KEY,
        Config.CUSTOM_GLM_BASE_URL, Config.CUSTOM_GLM_MODEL,
    )
    deepseek_ok = test_provider(
        'DeepSeek', Config.DEEPSEEK_API_KEY,
        Config.CUSTOM_DEEPSEEK_BASE_URL, Config.CUSTOM_DEEPSEEK_MODEL,
    )
    failover_ok = test_failover()

    print(
        f'SUMMARY: GLM={"OK" if glm_ok else "UNAVAILABLE"}, '
        f'DeepSeek={"OK" if deepseek_ok else "UNAVAILABLE"}, '
        f'Failover={"OK" if failover_ok else "FAILED"}'
    )
    sys.exit(0 if deepseek_ok and failover_ok else 1)
