import unittest
from unittest.mock import patch

from translation_service import TranslationError, TranslationService


def configured_service():
    return TranslationService(
        azure_key='azure-test-key',
        deepseek_key='deepseek-test-key',
        deepseek_base_url='https://deepseek.example',
        deepseek_model='deepseek-test-model',
    )


class TranslationServiceTests(unittest.TestCase):
    def test_preprocess_and_postprocess_academic_terms(self):
        prepared = TranslationService.preprocess_terms(
            'Agent memory is a memory substrate for long-horizon AI agents.'
        )
        self.assertIn('智能体记忆', prepared)
        self.assertIn('记忆底座', prepared)
        self.assertIn('长程', prepared)
        self.assertIn('AI 智能体', prepared)

        corrected = TranslationService.correct_terms(
            '代理记忆 使用大型语言模型进行长期视野推理。'
        )
        self.assertEqual('智能体记忆使用大语言模型进行长程推理。', corrected)

    def test_azure_preprocessing_escapes_input_and_applies_dictionary(self):
        prepared = TranslationService._prepare_azure_html(
            'Agent memory improves A < B & smart contracts.'
        )
        self.assertIn(
            '<mstrans:dictionary translation="智能体记忆">Agent memory</mstrans:dictionary>',
            prepared,
        )
        self.assertIn(
            '<mstrans:dictionary translation="智能合约">smart contracts</mstrans:dictionary>',
            prepared,
        )
        self.assertIn('A &lt; B &amp;', prepared)

    def test_azure_is_used_when_healthy(self):
        service = configured_service()
        with patch.object(service, '_translate_azure', return_value='代理记忆支持长时域推理。') as azure, \
                patch.object(service, '_translate_deepseek') as deepseek, \
                patch.object(service, '_translate_mymemory') as mymemory:
            result = service.translate('Agent memory supports long-horizon reasoning.')

        self.assertEqual('azure', result.provider)
        self.assertEqual('智能体记忆支持长程推理。', result.text)
        azure.assert_called_once()
        deepseek.assert_not_called()
        mymemory.assert_not_called()

    def test_deepseek_is_used_after_azure_failure(self):
        service = configured_service()
        with patch.object(service, '_translate_azure', side_effect=RuntimeError('429')), \
                patch.object(service, '_translate_deepseek', return_value='代理记忆。') as deepseek, \
                patch.object(service, '_translate_mymemory') as mymemory:
            result = service.translate('Agent memory.')

        self.assertEqual('deepseek', result.provider)
        self.assertEqual('智能体记忆。', result.text)
        deepseek.assert_called_once_with('智能体记忆.')
        mymemory.assert_not_called()

    def test_mymemory_is_last_fallback(self):
        service = configured_service()
        with patch.object(service, '_translate_azure', side_effect=RuntimeError('Azure down')), \
                patch.object(service, '_translate_deepseek', side_effect=RuntimeError('DeepSeek down')), \
                patch.object(service, '_translate_mymemory', return_value='代理记忆。') as mymemory:
            result = service.translate('Agent memory.')

        self.assertEqual('mymemory', result.provider)
        self.assertEqual('智能体记忆。', result.text)
        mymemory.assert_called_once_with('智能体记忆.')

    def test_all_provider_failures_raise_translation_error(self):
        service = configured_service()
        failure = RuntimeError('unavailable')
        with patch.object(service, '_translate_azure', side_effect=failure), \
                patch.object(service, '_translate_deepseek', side_effect=failure), \
                patch.object(service, '_translate_mymemory', side_effect=failure):
            with self.assertRaises(TranslationError):
                service.translate('Agent memory.')

    def test_mymemory_chunks_stay_within_provider_limit(self):
        chunks = TranslationService._split_for_mymemory('word ' * 300, limit=100)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 100 for chunk in chunks))


if __name__ == '__main__':
    unittest.main()
