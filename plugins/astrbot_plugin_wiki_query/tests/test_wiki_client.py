"""离线回归：编码、重复请求、验证页、API 错误和返回列表契约。"""

import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wiki_client as client


def response(body, *, status=200, headers=None):
    result = BytesIO(body.encode("utf-8"))
    result.status = status
    result.headers = headers or {}
    return result


def payload(items=None):
    if items is None:
        items = [
            {
                "title": "其它词条",
                "pageid": 2,
                "snippet": "测试 <span>摘要</span> &amp; 中文",
            },
            {"title": "杨桃", "pageid": 1, "snippet": "精确匹配"},
        ]
    return json.dumps({"query": {"search": items}})


class WikiClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_encoding_and_repeated_requests(self):
        words = ["中文 & plus+ #? 空格", "第二次请求"]
        with patch.object(
            client, "urlopen", side_effect=lambda *a, **k: response(payload())
        ) as request:
            for word in words:
                await client.search_wiki(word, False)
        self.assertEqual(request.call_count, 2)
        for call, word in zip(request.call_args_list, words):
            params = parse_qs(urlsplit(call.args[0].full_url).query)
            self.assertEqual(params["srsearch"], [word])
            self.assertEqual(params["action"], ["query"])

    async def test_result_contract_exact_match_and_html(self):
        with patch.object(
            client, "urlopen", side_effect=lambda *a, **k: response(payload())
        ):
            results = await client.search_wiki("杨桃", False)
        self.assertIsInstance(results, list)
        self.assertEqual(results[0]["title"], "杨桃")
        self.assertEqual(results[0]["url"], client.SOURCES["stardew"].page_url + "1")
        self.assertEqual(results[1]["snippet"], "测试 摘要 & 中文")
        self.assertEqual(
            set(results[0]), {"source", "title", "pageid", "url", "snippet"}
        )

    async def test_valid_empty_result(self):
        with patch.object(client, "urlopen", return_value=response(payload([]))):
            self.assertEqual(await client.search_wiki("不存在的词"), [])

    async def test_chrome_switch_does_not_use_direct_transport(self):
        with (
            patch.object(
                client,
                "_request_chrome",
                new_callable=AsyncMock,
                return_value=json.loads(payload()),
            ) as chrome,
            patch.object(client, "_request_direct") as direct,
        ):
            await client.search_wiki("杨桃", True, source="sve")
        chrome.assert_awaited_once()
        direct.assert_not_called()
        self.assertTrue(
            chrome.call_args.args[0].startswith(client.SOURCES["sve"].api_url)
        )

    async def test_cloudflare_http_403(self):
        error = HTTPError(
            "https://example.test/api.php",
            403,
            "Forbidden",
            {"Server": "cloudflare", "cf-mitigated": "challenge"},
            BytesIO(b"challenge"),
        )
        with (
            patch.object(client, "urlopen", side_effect=error),
            self.assertRaises(client.CloudflareBlockedError) as caught,
        ):
            await client.search_wiki("杨桃")
        self.assertEqual(caught.exception.status_code, 403)

    async def test_http_error_is_not_empty_result(self):
        error = HTTPError(
            "https://example.test", 429, "Too Many Requests", {}, BytesIO(b"rate limit")
        )
        with (
            patch.object(client, "urlopen", side_effect=error),
            self.assertRaises(client.WikiQueryError) as caught,
        ):
            await client.search_wiki("杨桃")
        self.assertEqual(caught.exception.status_code, 429)

    async def test_invalid_api_responses_raise(self):
        bodies = [
            "<html>maintenance</html>",
            "[]",
            "{}",
            '{"error":{"code":"search-error"}}',
            payload([{"title": "no id"}]),
        ]
        for body in bodies:
            with (
                self.subTest(body=body),
                patch.object(client, "urlopen", return_value=response(body)),
                self.assertRaises(client.WikiQueryError),
            ):
                await client.search_wiki("杨桃")

    async def test_http_200_challenge_is_not_success(self):
        with (
            patch.object(
                client,
                "urlopen",
                return_value=response(
                    '<script src="/cdn-cgi/challenge-platform/test"></script>'
                ),
            ),
            self.assertRaises(client.CloudflareBlockedError),
        ):
            await client.search_wiki("杨桃")

    async def test_invalid_inputs_do_not_make_requests(self):
        cases = [
            ((" ",), {}),
            (("词", "false"), {}),
            (("词",), {"source": "calamity"}),
            (("词",), {"limit": 0}),
            (("词",), {"limit": 51}),
            (("词",), {"timeout": 0}),
            (("词",), {"timeout": float("nan")}),
        ]
        with patch.object(client, "urlopen") as direct:
            for args, kwargs in cases:
                with (
                    self.subTest(args=args, kwargs=kwargs),
                    self.assertRaises((ValueError, TypeError)),
                ):
                    await client.search_wiki(*args, **kwargs)
        direct.assert_not_called()

    async def test_only_two_builtin_sources(self):
        self.assertEqual(set(client.SOURCES), {"stardew", "sve"})

    async def test_query_both_sources_returns_one_list(self):
        with patch.object(
            client, "urlopen", side_effect=lambda *a, **k: response(payload())
        ):
            results = await client.query_wikis("杨桃", False, limit=2)
        self.assertEqual(len(results), 4)
        self.assertEqual({item["source"] for item in results}, {"星露谷", "星露谷 SVE"})
        self.assertEqual([item["title"] for item in results[:2]], ["杨桃", "杨桃"])

    async def test_aggregate_does_not_hide_source_failure(self):
        with (
            patch.object(
                client, "_request_direct", side_effect=client.WikiQueryError("服务异常")
            ),
            self.assertRaises(client.WikiQueryError),
        ):
            await client.query_wikis("杨桃")


if __name__ == "__main__":
    unittest.main()
