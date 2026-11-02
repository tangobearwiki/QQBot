"""对比灰机灾厄 Wiki 的普通/Chrome 请求；仅测试，不注册为插件内置源。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wiki_client import (
    CloudflareBlockedError,
    WikiQueryError,
    WikiSource,
    search_wiki,
)


async def check(word: str) -> list[dict]:
    wiki = WikiSource(
        "灰机灾厄（仅测试）",
        "https://calamity.huijiwiki.com/api.php",
        "https://calamity.huijiwiki.com/index.php?curid=",
    )
    reports = []
    try:
        direct = await search_wiki(word, False, source=wiki, limit=3)
        reports.append(
            {"bypass_cloudflare": False, "blocked": False, "results": direct}
        )
    except CloudflareBlockedError as exc:
        # 普通请求被 CF 拦截是允许的；绕过路径必须返回真实词条才算通过。
        reports.append(
            {
                "bypass_cloudflare": False,
                "blocked": True,
                "http_status": exc.status_code,
            }
        )
    results = await search_wiki(word, True, source=wiki, limit=3)
    if not results:
        raise AssertionError(f"灰机 Chrome 请求未返回 {word!r} 的词条")
    reports.append({"bypass_cloudflare": True, "blocked": False, "results": results})
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--word", default="星流巨械", help="默认：星流巨械")
    parser.add_argument("--output", type=Path, help="可选：将测试结果保存为 JSON")
    args = parser.parse_args()
    try:
        reports = asyncio.run(check(args.word))
        text = json.dumps({"ok": True, "checks": reports}, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0
    except (WikiQueryError, ValueError, AssertionError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
