from nas_index.config import AppSettings
from nas_index.services import search_summary
from nas_index.services.search_summary import OpenAIChatSearchSummarizer
from nas_index.services.search_summary import SearchSummaryContext
from nas_index.services.search_summary import SearchSummaryDirectory
from nas_index.services.search_summary import SearchSummaryItem


def test_format_prompt_builds_search_result_map():
    context = SearchSummaryContext(
        query="葡萄",
        total=5,
        page=1,
        page_size=5,
        directories=(
            SearchSummaryDirectory(
                path="/设计部/包装设计/效果图",
                item_count=2,
                items=(
                    SearchSummaryItem(
                        name="0卡果冻樱花葡萄@0.5x.png",
                        full_path="/设计部/包装设计/效果图/0卡果冻樱花葡萄@0.5x.png",
                        entry_type="file",
                    ),
                    SearchSummaryItem(
                        name="0卡果冻樱花葡萄@1x.png",
                        full_path="/设计部/包装设计/效果图/0卡果冻樱花葡萄@1x.png",
                        entry_type="file",
                    ),
                ),
            ),
            SearchSummaryDirectory(
                path="/设计部/包装设计/源文件",
                item_count=2,
                items=(
                    SearchSummaryItem(
                        name="0卡果冻樱花葡萄味1.12.ai",
                        full_path="/设计部/包装设计/源文件/0卡果冻樱花葡萄味1.12.ai",
                        entry_type="file",
                    ),
                    SearchSummaryItem(
                        name="0卡果冻樱花葡萄味1.12-转曲.ai",
                        full_path="/设计部/包装设计/源文件/0卡果冻樱花葡萄味1.12-转曲.ai",
                        entry_type="file",
                    ),
                ),
            ),
            SearchSummaryDirectory(
                path="/设计部/交接文件/薄荷图鉴",
                item_count=1,
                items=(
                    SearchSummaryItem(
                        name="葡萄",
                        full_path="/设计部/交接文件/薄荷图鉴/葡萄",
                        entry_type="directory",
                    ),
                ),
            ),
        ),
    )

    prompt = search_summary._format_prompt(context)

    assert "搜索结果地图" in prompt
    assert "命中目录数：3" in prompt
    assert "一级目录命中排行" in prompt
    assert "/设计部: 5" in prompt
    assert "二级目录命中排行" in prompt
    assert "/设计部/包装设计: 4" in prompt
    assert "/设计部/交接文件: 1" in prompt
    assert "文件类型统计" in prompt
    assert ".png: 2" in prompt
    assert ".ai: 2" in prompt
    assert "文件夹: 1" in prompt
    assert "重复和版本线索" in prompt
    assert "0卡果冻樱花葡萄" in prompt
    assert "请按以下格式输出" in prompt
    assert "优先查看目录" in prompt
    assert "完整命中目录和文件名" in prompt


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
    assert (
        "NAS 文件检索助手"
        in request_json["messages"][0]["content"]
    )
    assert (
        "引用下面提供的目录或文件名"
        in request_json["messages"][0]["content"]
    )
