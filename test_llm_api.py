"""
Test script for LLM API configuration
Tests if the configured API can be successfully called
"""
import os
import sys
from dotenv import load_dotenv

# Fix encoding for Windows console
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Load environment variables
load_dotenv()

def test_custom_api():
    """Test custom LLM API configuration"""
    print("=" * 50)
    print("Testing Custom LLM API Configuration")
    print("=" * 50)

    api_key = os.environ.get('CUSTOM_LLM_API_KEY')
    base_url = os.environ.get('CUSTOM_LLM_BASE_URL')
    model = os.environ.get('CUSTOM_LLM_MODEL')

    print(f"\nConfiguration:")
    print(f"  API Key: {api_key[:20]}...{api_key[-10:] if api_key else 'None'}")
    print(f"  Base URL: {base_url}")
    print(f"  Model: {model}")

    if not all([api_key, base_url, model]):
        print("\n❌ Configuration incomplete!")
        print("Missing:")
        if not api_key:
            print("  - CUSTOM_LLM_API_KEY")
        if not base_url:
            print("  - CUSTOM_LLM_BASE_URL")
        if not model:
            print("  - CUSTOM_LLM_MODEL")
        return False

    try:
        import openai

        client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url
        )

        print(f"\n📡 Sending test request...")
        print(f"   Endpoint: {base_url}")
        print(f"   Model: {model}")

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Respond with ONLY a number from 0 to 100."},
                {"role": "user", "content": "Rate this test message from 0 to 100."}
            ],
            temperature=0,
            max_tokens=10
        )

        content = response.choices[0].message.content.strip()
        usage = response.usage

        print(f"\n✅ API request successful!")
        print(f"   Response: {content}")
        print(f"   Usage: {usage}")

        # Try parsing score
        import re
        match = re.search(r'\b(\d{1,3})\b', content)
        if match:
            score = int(match.group(1))
            print(f"   Parsed score: {score}")

        return True

    except ImportError:
        print("\n❌ OpenAI package not installed!")
        print("   Install with: pip install openai")
        return False

    except Exception as e:
        print(f"\n❌ API request failed!")
        print(f"   Error: {e}")
        print(f"   Error type: {type(e).__name__}")

        # Common error diagnostics
        error_str = str(e).lower()
        if '401' in error_str or 'unauthorized' in error_str or 'invalid' in error_str:
            print("\n   🔑 Possible cause: Invalid API key")
        elif '404' in error_str or 'not found' in error_str:
            print("\n   🔍 Possible cause: Invalid endpoint URL or model name")
        elif 'timeout' in error_str or 'timed out' in error_str:
            print("\n   ⏱️  Possible cause: Request timeout")
        elif 'connection' in error_str:
            print("\n   🌐 Possible cause: Network connection issue")

        return False


def test_llm_evaluator():
    """Test using the LLMEvaluator class"""
    print("\n" + "=" * 50)
    print("Testing LLMEvaluator Integration")
    print("=" * 50)

    try:
        from llm import LLMEvaluator

        print(f"\nInitializing LLMEvaluator with custom provider...")
        evaluator = LLMEvaluator(provider='custom')

        print(f"\n📝 Testing with sample paper...")
        result = evaluator.evaluate(
            title="Smart Contract Vulnerability Repair Using Multi-Agent Systems",
            abstract="This paper presents a novel approach to automated smart contract vulnerability repair using multi-agent systems and LLM-based software engineering techniques."
        )

        print(f"\n✅ Evaluation complete!")
        print(f"   Score: {result.score}/100")
        print(f"   Model: {result.model}")
        print(f"   Provider: {result.provider}")
        print(f"   Raw response: {result.raw_response}")

        if result.score >= 75:
            print(f"\n   📊 Result: Paper would be SAVED (score >= 75)")
        else:
            print(f"\n   📊 Result: Paper would be FILTERED (score < 75)")

        return True

    except Exception as e:
        print(f"\n❌ LLMEvaluator test failed!")
        print(f"   Error: {e}")
        return False


if __name__ == "__main__":
    print("PaperInfo LLM API Test Script\n")

    # Test 1: Direct API call
    test1_passed = test_custom_api()

    # Test 2: LLMEvaluator integration
    test2_passed = test_llm_evaluator()

    # Summary
    print("\n" + "=" * 50)
    print("Test Summary")
    print("=" * 50)
    print(f"Direct API Call: {'✅ PASSED' if test1_passed else '❌ FAILED'}")
    print(f"LLMEvaluator Integration: {'✅ PASSED' if test2_passed else '❌ FAILED'}")

    if test1_passed and test2_passed:
        print("\n🎉 All tests passed! Your API configuration is working.")
    else:
        print("\n⚠️  Some tests failed. Please check the error messages above.")
