"""测试非灰机源（B 站星露谷、SVE），不导入或启动任何 Bot。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wiki_client import WikiQueryError, query_wikis, search_wiki


async def check() -> list[dict]:
    reports = []
    for source, word in (("stardew", "杨桃"), ("sve", "索菲娅")):
        # 两条路径使用相同查询；验证返回的确实是词条，而非仅检查 HTTP 200。
        for bypass in (False, True):
            results = await search_wiki(word, bypass, source=source, limit=3)
            if not results:
                raise AssertionError(f"{source} 查询 {word!r} 没有返回词条")
            reports.append(
                {
                    "source": source,
                    "word": word,
                    "bypass_cloudflare": bypass,
                    "results": results,
                }
            )
    combined = await query_wikis("杨桃", False, limit=2)
    if {item["source"] for item in combined} != {"星露谷", "星露谷 SVE"}:
        raise AssertionError("双源入口未返回两个 Wiki 的结果")
    reports.append(
        {
            "source": "all",
            "word": "杨桃",
            "bypass_cloudflare": False,
            "results": combined,
        }
    )
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="可选：将测试结果保存为 JSON")
    args = parser.parse_args()
    try:
        reports = asyncio.run(check())
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
