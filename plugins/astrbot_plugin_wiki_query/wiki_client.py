"""公开 MediaWiki 搜索，只返回普通 list，不依赖 AstrBot 或 QQ 消息组件。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class WikiSource:
    name: str
    api_url: str
    page_url: str  # 以 ?curid= 结尾，拼接 MediaWiki pageid。


# 生产模块只预置星露谷原版和 SVE；其它 Wiki 可由调用方显式传 WikiSource。
SOURCES = {
    "stardew": WikiSource(
        "星露谷",
        "https://wiki.biligame.com/stardewvalley/api.php",
        "https://wiki.biligame.com/stardewvalley/index.php?curid=",
    ),
    "sve": WikiSource(
        "星露谷 SVE",
        "https://www.p1.wiki/api.php",
        "https://www.p1.wiki/index.php?curid=",
    ),
}


class WikiResult(TypedDict):
    source: str
    title: str
    pageid: int
    url: str
    snippet: str


class WikiQueryError(RuntimeError):
    """网络、HTTP 或 API 返回异常；与合法的空结果 [] 分开处理。"""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class CloudflareBlockedError(WikiQueryError):
    """收到 Cloudflare 验证页；Chrome 模拟也不保证通过所有挑战。"""


class _SnippetText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str):
        self.parts.append(data)


def _plain_text(html: str) -> str:
    parser = _SnippetText()
    parser.feed(html)
    return " ".join("".join(parser.parts).split())


def _decode_response(body: str, status: int, headers: dict) -> dict:
    headers = {key.lower(): value for key, value in headers.items()}
    lower_body = body.lower()
    challenged = (
        headers.get("cf-mitigated") == "challenge"
        or "cf-chl-" in lower_body
        or "/cdn-cgi/challenge-platform/" in lower_body
        or (status == 403 and headers.get("server", "").lower() == "cloudflare")
    )
    if challenged:
        raise CloudflareBlockedError(
            f"Cloudflare 验证未通过（HTTP {status}）；可尝试 bypass_cloudflare=True",
            status_code=status,
        )
    if not 200 <= status < 300:
        raise WikiQueryError(f"Wiki 请求失败：HTTP {status}", status_code=status)
    try:
        data = json.loads(body)
    except (ValueError, TypeError) as exc:
        raise WikiQueryError("Wiki 未返回有效 JSON，可能是验证页或服务异常") from exc
    if not isinstance(data, dict):
        raise WikiQueryError("Wiki 响应必须是 JSON 对象")
    if data.get("error"):
        raise WikiQueryError(f"Wiki API 错误：{data['error']}")
    query = data.get("query")
    if not isinstance(query, dict) or not isinstance(query.get("search"), list):
        raise WikiQueryError("Wiki 响应缺少 query.search 数组")
    return data


def _request_direct(url: str, timeout: float) -> dict:
    request = Request(
        url,
        headers={"User-Agent": "WikiQuery/1.0", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return _decode_response(
                response.read().decode("utf-8"), response.status, dict(response.headers)
            )
    except HTTPError as exc:
        with exc:
            return _decode_response(
                exc.read().decode("utf-8", errors="replace"),
                exc.code,
                dict(exc.headers),
            )
    except (URLError, OSError, UnicodeError) as exc:
        raise WikiQueryError(f"Wiki 网络请求失败：{exc}") from exc


async def _request_chrome(url: str, timeout: float) -> dict:
    try:
        from curl_cffi.requests import AsyncSession
        from curl_cffi.requests.exceptions import RequestException
    except ImportError as exc:
        raise WikiQueryError(
            "绕过模式需要 curl_cffi，请安装插件 requirements.txt"
        ) from exc
    try:
        # TLS/HTTP 指纹和默认请求头由库一并生成，无需登录或伪造 Cookie。
        async with AsyncSession(impersonate="chrome", timeout=timeout) as session:
            response = await session.get(url)
        return _decode_response(
            response.text, response.status_code, dict(response.headers)
        )
    except RequestException as exc:
        raise WikiQueryError(f"Chrome 模拟请求失败：{exc}") from exc


async def search_wiki(
    word: str,
    bypass_cloudflare: bool = False,
    *,
    source: str | WikiSource = "stardew",
    limit: int = 10,
    timeout: float = 25,
) -> list[WikiResult]:
    """输入词和绕过开关，返回标题/链接/纯文本摘要列表；无匹配时返回 []。

    source 可为 stardew、sve 或自定义 WikiSource。snippet 是搜索摘要，不是全文。
    """
    if not isinstance(word, str) or not word.strip():
        raise ValueError("查询词不能为空")
    if not isinstance(bypass_cloudflare, bool):
        raise TypeError("bypass_cloudflare 必须是 bool")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("limit 必须是 1-50 的整数")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < timeout <= 120
    ):
        raise ValueError("timeout 必须在 0-120 秒之间")
    if isinstance(source, str):
        if source not in SOURCES:
            raise ValueError(f"未知 Wiki 源：{source}，可选：{', '.join(SOURCES)}")
        wiki = SOURCES[source]
    elif isinstance(source, WikiSource):
        wiki = source
    else:
        raise TypeError("source 必须是源名称或 WikiSource")

    word = word.strip()
    params = {
        "action": "query",
        "list": "search",
        "srsearch": word,
        "srnamespace": "0",
        "srlimit": str(limit),
        "srprop": "snippet",
        "format": "json",
    }
    separator = "&" if "?" in wiki.api_url else "?"
    url = wiki.api_url + separator + urlencode(params)
    if bypass_cloudflare:
        data = await _request_chrome(url, timeout)
    else:
        # 标准库请求放入工作线程，避免阻塞 AstrBot 的事件循环。
        data = await asyncio.to_thread(_request_direct, url, timeout)

    results: list[WikiResult] = []
    for item in data["query"]["search"]:
        if not isinstance(item, dict):
            raise WikiQueryError("Wiki 搜索结果条目不是对象")
        title, pageid = item.get("title"), item.get("pageid")
        if not isinstance(title, str) or type(pageid) is not int or pageid <= 0:
            raise WikiQueryError("Wiki 搜索结果缺少有效 title/pageid")
        results.append(
            {
                "source": wiki.name,
                "title": title,
                "pageid": pageid,
                "url": wiki.page_url + str(pageid),
                "snippet": _plain_text(str(item.get("snippet") or "")),
            }
        )
    # 保留其余结果的相关性顺序，将完全匹配的标题排到最前。
    results.sort(key=lambda result: result["title"] != word)
    return results[:limit]


async def query_wikis(
    word: str,
    bypass_cloudflare: bool = False,
    *,
    limit: int = 5,
    timeout: float = 25,
) -> list[WikiResult]:
    """默认查询已注入的原版/SVE 两个源，合并为一个 list；limit 为每源上限。"""
    batches = await asyncio.gather(
        *(
            search_wiki(
                word, bypass_cloudflare, source=source, limit=limit, timeout=timeout
            )
            for source in SOURCES
        )
    )
    results = [result for batch in batches for result in batch]
    results.sort(key=lambda result: result["title"] != word.strip())
    return results
