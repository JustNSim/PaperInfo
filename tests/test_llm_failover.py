import unittest

from llm.evaluator import EvalResult, FailoverLLMProvider, RateLimitError


class StubProvider:
    def __init__(self, name, result=None, error=None):
        self.provider_name = name
        self.model = f'{name}-model'
        self.result = result
        self.error = error
        self.calls = 0

    def evaluate(self, title, abstract, system_prompt):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def successful_result(provider):
    return EvalResult(
        relevance_score=80,
        value_score=75,
        model=f'{provider}-model',
        provider=provider,
    )


class FailoverLLMProviderTests(unittest.TestCase):
    def test_healthy_primary_is_used(self):
        primary = StubProvider('GLM', result=successful_result('GLM'))
        fallback = StubProvider('DeepSeek', result=successful_result('DeepSeek'))
        provider = FailoverLLMProvider(primary, fallback, cooldown_seconds=300)

        result = provider.evaluate('title', 'abstract', 'prompt')

        self.assertEqual('GLM', result.provider)
        self.assertEqual(1, primary.calls)
        self.assertEqual(0, fallback.calls)

    def test_rate_limit_switches_to_fallback(self):
        primary = StubProvider('GLM', error=RateLimitError('429'))
        fallback = StubProvider('DeepSeek', result=successful_result('DeepSeek'))
        provider = FailoverLLMProvider(primary, fallback, cooldown_seconds=300)

        result = provider.evaluate('title', 'abstract', 'prompt')

        self.assertEqual('DeepSeek', result.provider)
        self.assertEqual(1, primary.calls)
        self.assertEqual(1, fallback.calls)

    def test_open_circuit_bypasses_primary(self):
        primary = StubProvider('GLM', error=RateLimitError('429'))
        fallback = StubProvider('DeepSeek', result=successful_result('DeepSeek'))
        provider = FailoverLLMProvider(primary, fallback, cooldown_seconds=300)

        provider.evaluate('first', 'abstract', 'prompt')
        provider.evaluate('second', 'abstract', 'prompt')

        self.assertEqual(1, primary.calls)
        self.assertEqual(2, fallback.calls)


if __name__ == '__main__':
    unittest.main()
