from nas_index.config import AppSettings
from nas_index.services import search_summary
from nas_index.services.search_summary import OpenAIChatSearchSummarizer
from nas_index.services.search_summary import SearchSummaryContext


async def test_openai_chat_summarizer_ignores_environment_proxy(
    monkeypatch,
):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": "摘要"
                        }
                    }
                ]
            }

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(
        search_summary.httpx,
        "AsyncClient",
        FakeAsyncClient,
    )
    summarizer = OpenAIChatSearchSummarizer(
        AppSettings(ai_api_key="sk-test")
    )

    summary = await summarizer.summarize(
        SearchSummaryContext(
            query="苹果",
            total=0,
            page=1,
            page_size=50,
            directories=(),
        )
    )

    assert summary == "摘要"
    assert calls[0]["trust_env"] is False
