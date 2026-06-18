import base64
import binascii
import hashlib
import hmac
import json
import re
from collections import Counter, defaultdict
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

    async def answer(
        self,
        context: SearchSummaryContext,
        question: str,
    ) -> str:
        return await self._complete(
            _format_question_prompt(
                context,
                question,
            )
        )

    async def summarize(
        self,
        context: SearchSummaryContext,
    ) -> str:
        return await self._complete(_format_prompt(context))

    async def _complete(
        self,
        user_content: str,
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
                                    "你是 NAS 文件检索助手。你只根据用户可见的路径、"
                                    "目录名和文件名做分析，不能推测文件内容或读取文件。"
                                    "你的目标是帮助用户决定先点哪些命中目录。"
                                    "回答必须简洁、带证据，证据只能引用下面提供的"
                                    "目录或文件名。"
                                ),
                            },
                            {
                                "role": "user",
                                "content": user_content,
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
        "请按以下格式输出：",
        "1. 总体判断：用 2-3 句话说明这批结果大概是什么。",
        "2. 主要命中方向：按主题归类，并引用目录或文件名证据。",
        "3. 优先查看目录：列 3-8 个目录，每个说明为什么值得先点。",
        "4. 重复和版本线索：指出同名、相似名、分辨率、转曲、源文件等线索。",
        "5. 建议下一步：给出可继续搜索的关键词或下一步查看建议。",
        "",
        "约束：",
        "- 不要猜测文件内容，只能根据路径、目录名、文件名判断。",
        "- 不要要求读取文件或访问 NAS。",
        "- 如果证据不足，直接说证据不足。",
        "- 每个重要判断都要引用至少一个目录或文件名作为依据。",
        "",
        "参考资料：",
    ]
    lines.extend(_format_result_reference(context))
    return "\n".join(lines)


def _format_question_prompt(
    context: SearchSummaryContext,
    question: str,
) -> str:
    lines = [
        f"用户问题：{question.strip()}",
        "",
        "回答要求：",
        "- 只回答用户问题，不要输出完整总结。",
        "- 最多 5 条；每条尽量不超过 2 句话。",
        "- 优先给出可查看的目录或文件名线索。",
        "- 每条关键判断都要引用目录或文件名作为依据。",
        "- 如果当前搜索结果里看不出来，就回答“当前搜索结果里看不出来”。",
        "",
        "参考资料：",
    ]
    lines.extend(_format_result_reference(context))
    return "\n".join(lines)


def _format_result_reference(
    context: SearchSummaryContext,
) -> list[str]:
    provided_count = sum(
        len(directory.items)
        for directory in context.directories
    )
    lines = [
        f"搜索词：{context.query}",
        f"结果总数：{context.total}",
        f"本次提供给你的结果数：{provided_count}",
        f"命中目录数：{len(context.directories)}",
        "",
        "搜索结果地图：",
        "一级目录命中排行：",
    ]
    lines.extend(_format_ranked_counts(_path_counts(context, 1)))
    lines.append("二级目录命中排行：")
    lines.extend(_format_ranked_counts(_path_counts(context, 2)))
    lines.append("文件类型统计：")
    lines.extend(_format_ranked_counts(_file_type_counts(context)))
    lines.append("重复和版本线索：")
    lines.extend(_format_version_groups(context))
    lines.append("")
    lines.append("完整命中目录和文件名：")
    for directory in context.directories:
        lines.append(
            f"- 目录：{directory.path}，命中 {directory.item_count} 条"
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
    return lines


def _path_counts(
    context: SearchSummaryContext,
    depth: int,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for directory in context.directories:
        counts[_path_prefix(directory.path, depth)] += (
            directory.item_count
        )
    return counts


def _path_prefix(
    path: str,
    depth: int,
) -> str:
    parts = [
        part
        for part in path.split("/")
        if part
    ]
    if not parts:
        return "/"
    return "/" + "/".join(parts[:depth])


def _file_type_counts(
    context: SearchSummaryContext,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for directory in context.directories:
        for item in directory.items:
            counts[_file_type_label(item)] += 1
    return counts


def _file_type_label(item: SearchSummaryItem) -> str:
    if item.entry_type == "directory":
        return "文件夹"
    name = item.name.rsplit("/", 1)[-1]
    if "." not in name:
        return "无扩展名"
    extension = name.rsplit(".", 1)[-1].strip().lower()
    if not extension:
        return "无扩展名"
    return f".{extension}"


def _format_ranked_counts(
    counts: Counter[str],
    *,
    limit: int = 10,
) -> list[str]:
    if not counts:
        return ["- 无"]
    return [
        f"- {label}: {count}"
        for label, count in counts.most_common(limit)
    ]


def _format_version_groups(
    context: SearchSummaryContext,
    *,
    limit: int = 8,
) -> list[str]:
    groups: dict[str, list[str]] = defaultdict(list)
    display_keys: dict[str, str] = {}
    for directory in context.directories:
        for item in directory.items:
            if item.entry_type != "file":
                continue
            key, display_key = _version_key(item.name)
            if len(key) < 2:
                continue
            groups[key].append(item.name)
            display_keys.setdefault(key, display_key)

    repeated = [
        (
            display_keys[key],
            sorted(set(names)),
        )
        for key, names in groups.items()
        if len(set(names)) > 1
    ]
    repeated.sort(
        key=lambda group: (
            -len(group[1]),
            group[0],
        )
    )
    if not repeated:
        return ["- 未发现明显重复或版本线索"]
    lines = []
    for key, names in repeated[:limit]:
        lines.append(
            f"- {key}: "
            + "；".join(names[:6])
        )
    return lines


def _version_key(name: str) -> tuple[str, str]:
    stem = name.rsplit(".", 1)[0]
    display_key = _strip_version_markers(stem)
    key = re.sub(
        r"\s+",
        "",
        display_key,
    ).lower()
    return key, display_key


def _strip_version_markers(stem: str) -> str:
    value = re.sub(
        r"@\d+(?:\.\d+)?x$",
        "",
        stem,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"[-_ ]?转曲$",
        "",
        value,
    )
    value = re.sub(
        r"[-_ ]?copy(?:\s*\d+)?$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"[-_ ]?副本(?:\s*\d+)?$",
        "",
        value,
    )
    return value.strip() or stem
