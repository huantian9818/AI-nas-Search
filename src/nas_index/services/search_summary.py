from dataclasses import dataclass

import httpx

from nas_index.config import AppSettings


class SearchSummaryUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SearchSummaryItem:
    name: str
    full_path: str
    entry_type: str


@dataclass(frozen=True, slots=True)
class SearchSummaryDirectory:
    path: str
    item_count: int
    items: tuple[SearchSummaryItem, ...]


@dataclass(frozen=True, slots=True)
class SearchSummaryContext:
    query: str
    total: int
    page: int
    page_size: int
    directories: tuple[SearchSummaryDirectory, ...]


class OpenAIChatSearchSummarizer:
    def __init__(
        self,
        settings: AppSettings,
    ):
        self.api_key = settings.ai_api_key
        self.base_url = settings.ai_base_url.rstrip("/")
        self.model = settings.ai_model
        self.timeout_seconds = settings.ai_timeout_seconds

    async def summarize(
        self,
        context: SearchSummaryContext,
    ) -> str:
        if not self.api_key:
            raise SearchSummaryUnavailable("管理员未配置 AI 总结")

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                trust_env=False,
            ) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "你是 NAS 文件搜索结果助手。只根据用户可见的目录名"
                                    "和文件名做概览，不要猜测文件内容，不要要求读取文件。"
                                ),
                            },
                            {
                                "role": "user",
                                "content": _format_prompt(context),
                            },
                        ],
                        "temperature": 0.2,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SearchSummaryUnavailable(
                "AI 总结请求失败"
            ) from exc
        payload = response.json()
        try:
            summary = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SearchSummaryUnavailable(
                "AI 总结返回格式不正确"
            ) from exc
        return str(summary).strip()


def _format_prompt(context: SearchSummaryContext) -> str:
    lines = [
        f"搜索词：{context.query}",
        f"结果总数：{context.total}",
        f"当前页：第 {context.page} 页，每页 {context.page_size} 条",
        "",
        "请输出：",
        "1. 结果主要集中在哪些主题或目录。",
        "2. 建议优先查看哪些目录。",
        "3. 是否有相似或重复的分类线索。",
        "",
        "当前用户可见的命中目录和文件名：",
    ]
    for directory in context.directories:
        lines.append(
            f"- 目录：{directory.path}，当前页命中 {directory.item_count} 条"
        )
        for item in directory.items:
            item_type = (
                "文件夹"
                if item.entry_type == "directory"
                else "文件"
            )
            lines.append(
                f"  - {item_type}: {item.name} ({item.full_path})"
            )
    return "\n".join(lines)
