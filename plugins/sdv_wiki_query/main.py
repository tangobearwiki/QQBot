import asyncio
import json
import os
import re
import time
import unicodedata
from urllib.parse import quote, urlencode

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Node, Nodes, Plain
from astrbot.api.star import Star, register

VANILLA_API = "https://wiki.biligame.com/stardewvalley/api.php"
SVE_API = "https://www.p1.wiki/api.php"
RSV_API = "https://rsv.miraheze.org/w/api.php"
VANILLA_PAGE = "https://wiki.biligame.com/stardewvalley/"
SVE_PAGE = "https://www.p1.wiki/?curid="
RSV_PAGE = "https://rsv.miraheze.org/wiki/?curid="
B23_API = "https://api.bilibili.com/x/share/click"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)
SS_PREFIX = re.compile(r"^ss[ \u3000]+(.+)$", re.IGNORECASE)

FUZZY_SHOW = 55.0        # 低于此分的候选不展示
FUZZY_TAG_BELOW = 800.0  # 命中分低于此值时标注（模糊匹配）
CACHE_MAX_AGE = 30 * 86400

PDIR = os.path.dirname(os.path.abspath(__file__))
WIKI_CFG = {
    "原版": {"key": "vanilla", "api": VANILLA_API, "base": VANILLA_PAGE, "curid": False},
    "SVE": {"key": "sve", "api": SVE_API, "base": SVE_PAGE, "curid": True},
    "RSV": {"key": "rsv", "api": RSV_API, "base": RSV_PAGE, "curid": True},
}

# ---- 模糊评分: rapidfuzz 优先, 缺失时退回 difflib ----
try:
    from rapidfuzz import fuzz as _fz

    _ratio = lambda a, b: _fz.ratio(a, b)
    _partial = lambda a, b: _fz.partial_ratio(a, b)
except ImportError:
    import difflib

    _ratio = lambda a, b: difflib.SequenceMatcher(None, a, b).ratio() * 100
    _partial = _ratio

try:
    from pypinyin import lazy_pinyin

    _to_pinyin = lambda s: "".join(lazy_pinyin(s))
except ImportError:
    _to_pinyin = lambda s: ""

_WS_RE = re.compile(r"[\s_]+")


def _norm(s):
    return _WS_RE.sub("", unicodedata.normalize("NFKC", s).lower())


def _fuzzy_score(a, b):
    r, p = _ratio(a, b), _partial(a, b)
    return max(r, r * 0.55 + p * 0.45)


def _score(kw_norm, kw_py, tnorm, tpy):
    if not tnorm or not kw_norm:
        return 0.0
    if kw_norm == tnorm:
        return 1000.0
    if kw_norm in tnorm:
        return 900.0 - min(len(tnorm) - len(kw_norm), 60)
    if tnorm in kw_norm:
        return 880.0 - min(len(kw_norm) - len(tnorm), 60)
    s = _fuzzy_score(kw_norm, tnorm)
    if kw_py and tpy:
        # 长度守卫: 防止超短标题(如"书")靠拼音子串虚高
        lr = min(len(kw_py), len(tpy)) / max(len(kw_py), len(tpy))
        ps = _fuzzy_score(kw_py, tpy) * 0.95 * (0.35 + 0.65 * lr)
        if ps > s:
            s = ps
    return s


def _build_cache_entry(titles):
    out = []
    for t, pid in titles:
        tn = _norm(t)
        out.append((t, pid, tn, _to_pinyin(tn)))
    return out


def _local_search(cache, kw_norm, kw_py, top_n=5):
    scored = []
    for title, pid, tnorm, tpy in cache:
        s = _score(kw_norm, kw_py, tnorm, tpy)
        if s >= FUZZY_SHOW:
            scored.append((title, pid, s))
    scored.sort(key=lambda x: (-x[2], len(x[0])))
    return scored[:top_n]


def _page_url(name, title, pageid):
    cfg = WIKI_CFG[name]
    if cfg["curid"]:
        return f"{cfg['base']}{pageid}"
    return cfg["base"] + quote(title.replace(" ", "_"), safe="/")


def _titles_path(name):
    return os.path.join(PDIR, f"titles_{WIKI_CFG[name]['key']}.json")


async def _curl_json(url, params):
    cmd = ["curl", "-sS", "--max-time", "15", "-A", UA, url + "?" + urlencode(params)]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    out, _ = await proc.communicate()
    try:
        return json.loads(out.decode("utf-8", "replace"))
    except Exception:
        return {}


async def _fetch_all_titles(session, api):
    """allpages 翻页抓取, 兼容新旧 continuation 格式"""
    titles, cont = [], {}
    for _ in range(60):
        params = {
            "action": "query",
            "list": "allpages",
            "apnamespace": "0",
            "aplimit": "500",
            "apfilterredir": "nonredirects",
            "format": "json",
        }
        params.update(cont)
        if api == RSV_API:
            data = await _curl_json(api, params)
        else:
            async with session.get(api, params=params, timeout=HTTP_TIMEOUT) as resp:
                data = await resp.json(content_type=None)
        if not isinstance(data, dict):
            break
        if data.get("error"):
            logger.warning(f"[sdv_wiki] allpages 报错: {str(data['error'])[:120]}")
            break
        titles += [
            (p["title"], p.get("pageid", 0))
            for p in (data.get("query") or {}).get("allpages") or []
            if p.get("title")
        ]
        c = data.get("continue") or {}
        qc = (data.get("query-continue") or {}).get("allpages") or {}
        if c:
            cont = {k: v for k, v in c.items() if k != "continue"}
            await asyncio.sleep(0.3)
            continue
        if qc:
            cont = dict(qc)
            await asyncio.sleep(0.3)
            continue
        break
    return titles


async def _gen_b23(session, sem, url):
    """将 biligame 长链接转为 b23.tv 短链，失败时返回原链接"""
    async with sem:
        try:
            async with session.post(
                B23_API,
                data={
                    "platform": "unix",
                    "share_channel": "COPY",
                    "share_id": "public.webview.0.0.pv",
                    "share_mode": "4",
                    "oid": url,
                    "buvid": "sdvbot",
                    "build": "6114514",
                },
                timeout=HTTP_TIMEOUT,
            ) as resp:
                data = await resp.json(content_type=None)
            content = (data or {}).get("data", {}).get("content", "")
            if content.startswith("https://b23.tv/"):
                return content
            logger.warning(f"[sdv_wiki] b23 生成异常: {data}")
        except Exception as e:
            logger.warning(f"[sdv_wiki] b23 生成失败({url}): {e}")
    return url


@register("sdv_wiki_query", "assistant", "星露谷 Wiki 查询（含模糊搜索）", "2.2.0")
class SdvWikiQuery(Star):
    def __init__(self, context):
        super().__init__(context)
        self._corpus = {}
        self._bg = None
        self._refreshed = False

    async def initialize(self):
        # 词条文件较大（数万条 + 拼音索引），放到线程里构建，避免阻塞事件循环
        await asyncio.to_thread(self._load_files)
        if self._stale_any():
            self._refreshed = True
            self._bg = asyncio.create_task(self._ensure_corpus())

    async def terminate(self):
        if self._bg and not self._bg.done():
            self._bg.cancel()

    def _load_files(self):
        for name in WIKI_CFG:
            path = _titles_path(name)
            if not os.path.exists(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    titles = json.load(f)
                self._corpus[name] = _build_cache_entry(titles)
                logger.info(f"[sdv_wiki] 已加载{name}词条: {len(titles)} 条")
            except Exception as e:
                logger.warning(f"[sdv_wiki] {name}词条文件损坏: {e}")

    def _stale_any(self):
        now = time.time()
        return any(
            not os.path.exists(_titles_path(n))
            or now - os.path.getmtime(_titles_path(n)) > CACHE_MAX_AGE
            for n in WIKI_CFG
        )

    async def _ensure_corpus(self):
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": UA}) as session:
                for name, cfg in WIKI_CFG.items():
                    path = _titles_path(name)
                    if os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_MAX_AGE:
                        continue
                    try:
                        titles = await _fetch_all_titles(session, cfg["api"])
                        if titles:
                            tmp = path + ".tmp"
                            with open(tmp, "w", encoding="utf-8") as f:
                                json.dump(titles, f, ensure_ascii=False)
                            os.replace(tmp, path)
                            self._corpus[name] = await asyncio.to_thread(_build_cache_entry, titles)
                            logger.info(f"[sdv_wiki] {name}词条已更新: {len(titles)} 条")
                        else:
                            logger.warning(f"[sdv_wiki] {name}词条拉取为空, 保留旧数据")
                    except Exception as e:
                        logger.warning(f"[sdv_wiki] {name}词条拉取失败: {e}")
        finally:
            self._refreshed = True

    async def _remote_search(self, session, name, keyword, kw_norm, kw_py):
        """词条缓存缺失时的降级路径: 走远端搜索 + 本地打分"""
        cfg = WIKI_CFG[name]
        results = []
        try:
            if name == "原版":
                async with session.get(
                    cfg["api"],
                    params={"action": "opensearch", "search": keyword, "limit": "8", "format": "json"},
                    timeout=HTTP_TIMEOUT,
                ) as resp:
                    data = await resp.json(content_type=None)
                titles = data[1] if isinstance(data, list) and len(data) > 1 else []
                for t in titles:
                    tn = _norm(t)
                    results.append((t, 0, _score(kw_norm, kw_py, tn, _to_pinyin(tn))))
            else:
                async with session.get(
                    cfg["api"],
                    params={
                        "action": "query",
                        "list": "search",
                        "srsearch": keyword,
                        "srlimit": "8",
                        "format": "json",
                    },
                    timeout=HTTP_TIMEOUT,
                ) as resp:
                    data = await resp.json(content_type=None)
                for h in (data.get("query") or {}).get("search") or []:
                    t = h.get("title") or ""
                    tn = _norm(t)
                    results.append((t, h.get("pageid", 0), _score(kw_norm, kw_py, tn, _to_pinyin(tn))))
        except Exception as e:
            logger.warning(f"[sdv_wiki] {name}远端搜索失败: {e}")
        results = [r for r in results if r[2] >= FUZZY_SHOW]
        results.sort(key=lambda x: (-x[2], len(x[0])))
        return [(t, _page_url(name, t, pid), s) for t, pid, s in results[:5]]

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_ss_query(self, event: AstrMessageEvent):
        text = (event.message_str or "").strip()
        m = SS_PREFIX.match(text)
        if not m:
            return
        keyword = m.group(1).strip()
        if not keyword or len(keyword) > 50:
            yield event.plain_result("ss 关键词无效，请输入 1-50 个字符")
            return
        kw_norm = _norm(keyword)
        if not kw_norm:
            yield event.plain_result(f"没有找到与「{keyword}」相关的 Wiki 页面")
            return
        kw_py = _to_pinyin(kw_norm)
        logger.info(f"[sdv_wiki] 查询: {keyword}")

        if not self._corpus:
            await asyncio.to_thread(self._load_files)
        # 防止后台刷新任务运行中再次被拉起（竞态导致重复抓取/写文件）
        if (self._bg is None or self._bg.done()) and not self._refreshed and self._stale_any():
            self._refreshed = True
            self._bg = asyncio.create_task(self._ensure_corpus())

        async with aiohttp.ClientSession(headers={"User-Agent": UA}) as session:
            out = {}
            for name in WIKI_CFG:
                cache = self._corpus.get(name)
                if cache:
                    out[name] = [
                        (t, _page_url(name, t, pid), s)
                        for t, pid, s in _local_search(cache, kw_norm, kw_py)
                    ]
                else:
                    out[name] = await self._remote_search(session, name, keyword, kw_norm, kw_py)
            if out.get("原版"):
                sem = asyncio.Semaphore(3)
                out["原版"] = [
                    (t, await _gen_b23(session, sem, u), s)
                    for t, u, s in out["原版"]
                ]

        lines, nodes, total = [], [], 0
        for name in WIKI_CFG:
            results = out.get(name) or []
            if not results:
                continue
            best_t, best_u, best_s = results[0]
            tag = "" if best_s >= FUZZY_TAG_BELOW else "（模糊匹配）"
            lines.append(f"[{name}] {best_t}{tag}\n{best_u}")
            body = "\n".join(f"{t}\n{u}" for t, u, s in results)
            nodes.append(
                Node(
                    content=[Plain(f"【{name}维基】相关词条\n\n{body}")],
                    name=f"{name}维基",
                    uin=str(event.get_self_id() or "10000"),
                )
            )
            total += len(results)

        if not lines:
            yield event.plain_result(f"没有找到与「{keyword}」相关的 Wiki 页面，试试更短或更准确的关键词")
            return

        chain = [Plain("\n".join(lines))]
        if total >= 2:
            chain.append(Nodes(nodes))
        yield event.chain_result(chain)
