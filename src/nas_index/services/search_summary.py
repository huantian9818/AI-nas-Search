import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import asdict, dataclass

import httpx

from nas_index.config import AppSettings


class SearchSummaryUnavailable(RuntimeError):
    pass


class SearchSummaryPayloadError(ValueError):
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


@dataclass(frozen=True, slots=True)
class SearchSummaryPayloadAccess:
    nas_id: int
    share_paths: tuple[str, ...]


def sign_search_summary_payload(
    context: SearchSummaryContext,
    *,
    nas_id: int,
    share_paths: tuple[str, ...],
    secret: bytes,
) -> dict[str, str]:
    document = {
        "access": {
            "nas_id": nas_id,
            "share_paths": sorted(share_paths),
        },
        "context": asdict(context),
    }
    payload = base64.urlsafe_b64encode(
        json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).decode("ascii")
    return {
        "payload": payload,
        "signature": _summary_payload_signature(
            payload,
            secret,
        ),
    }


def load_search_summary_payload(
    payload: str,
    signature: str,
    *,
    secret: bytes,
) -> tuple[SearchSummaryPayloadAccess, SearchSummaryContext]:
    try:
        expected_signature = _summary_payload_signature(
            payload,
            secret,
        )
    except UnicodeEncodeError as exc:
        raise SearchSummaryPayloadError(
            "总结数据已失效，请重新搜索"
        ) from exc

    if not hmac.compare_digest(
        signature,
        expected_signature,
    ):
        raise SearchSummaryPayloadError(
            "总结数据已失效，请重新搜索"
        )

    try:
        document = json.loads(
            base64.urlsafe_b64decode(
                payload.encode("ascii")
            ).decode("utf-8")
        )
        access_data = document["access"]
        context_data = document["context"]
        access = SearchSummaryPayloadAccess(
            nas_id=int(access_data["nas_id"]),
            share_paths=tuple(
                str(path)
                for path in access_data["share_paths"]
            ),
        )
        context = SearchSummaryContext(
            query=str(context_data["query"]),
            total=int(context_data["total"]),
            page=int(context_data["page"]),
            page_size=int(context_data["page_size"]),
            directories=tuple(
                SearchSummaryDirectory(
                    path=str(directory["path"]),
                    item_count=int(
                        directory["item_count"]
                    ),
                    items=tuple(
                        SearchSummaryItem(
                            name=str(item["name"]),
                            full_path=str(
                                item["full_path"]
                            ),
                            entry_type=str(
                                item["entry_type"]
                            ),
                        )
                        for item in directory["items"]
                    ),
                )
                for directory in context_data[
                    "directories"
                ]
            ),
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
    ) as exc:
        raise SearchSummaryPayloadError(
            "总结数据已失效，请重新搜索"
        ) from exc
    return access, context


def _summary_payload_signature(
    payload: str,
    secret: bytes,
) -> str:
    return hmac.new(
        secret,
        payload.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


class OpenAIChatSearchSummarizer:
    def __init__(
        self,
        settings: AppSettings,
    ):
        self.api_key = settings.ai_api_key
        self.base_url = settings.ai_base_url.rstrip("/")
        self.model = settings.ai_model
        self.timeout_seconds = settings.ai_timeout_seconds
        self.max_tokens = settings.ai_max_tokens

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
                        "max_tokens": self.max_tokens,
                        "enable_thinking": False,
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
