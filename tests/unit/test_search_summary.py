from nas_index.config import AppSettings
from nas_index.services import search_summary
from nas_index.services.search_summary import OpenAIChatSearchSummarizer
from nas_index.services.search_summary import SearchSummaryContext


async def test_openai_chat_summarizer_ignores_environment_proxy(
    monkeypatch,
):
    client_calls = []
    post_calls = []

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
            client_calls.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, *args, **kwargs):
            post_calls.append(
                {
                    "args": args,
                    "kwargs": kwargs,
                }
            )
            return FakeResponse()

    monkeypatch.setattr(
        search_summary.httpx,
        "AsyncClient",
        FakeAsyncClient,
    )
    summarizer = OpenAIChatSearchSummarizer(
        AppSettings(
            ai_api_key="sk-test",
            ai_max_tokens=650,
        )
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
    assert client_calls[0]["trust_env"] is False
    request_json = post_calls[0]["kwargs"]["json"]
    assert request_json["enable_thinking"] is False
    assert request_json["max_tokens"] == 650
