# -*- coding: utf-8 -*-
# 直播总源（海阔式自动更新版）
# 特性：
#   1. 多候选源自动探测：内置多个 m3u/iptv 地址，逐个请求，取第一个可用的
#   2. 固定更新清单（海阔式）：UPDATE_LIST_URL 永久不变，内容是候选源地址列表（每行一个URL），
#      启动时先拉清单刷新候选，失败回退内置 CANDIDATE_URLS；源地址变更只需改清单文件，客户端零改动
#   3. 双兼容导入：默影视(FongMi) 与 星落(dr_py) 均可用
#   4. 自实现 fetch 兜底：无 requests 依赖也能跑
# 用法：默影视以 type=3 站点栏目加载（api 填本 py 的远程 URL），直播栏目自动切换可用源
try:
    from base import Spider as BaseSpider
except Exception:
    try:
        from base.spider import Spider as BaseSpider
    except Exception:
        from base_spider import Spider as BaseSpider

import json
import re

try:
    import requests
except Exception:
    requests = None

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ============ 海阔式固定更新入口：URL 永久不变，内容可随时编辑 ============
# 指向托管在 GitHub/Gitee raw 的清单文件，每行一个候选 m3u/iptv 地址
UPDATE_LIST_URL = "https://down.nigx.cn/raw.githubusercontent.com/liuxinvet/liuxinvet/refs/heads/main/%E7%9B%B4%E6%92%AD%E6%BA%90%E6%B8%85%E5%8D%95.txt"

# 内置候选源（清单拉取失败时的兜底），按优先级排序，逐个探测
CANDIDATE_URLS = [
    "https://douyin.445569.xyz/live.m3u",
    "https://down.nigx.cn/raw.githubusercontent.com/Kimentanm/aptv/master/m3u/iptv.m3u",
    "http://193.123.86.190:14888/TV/iptv.php",
]


class Spider(BaseSpider):

    def __init__(self):
        super().__init__()
        self.m3u_url = None
        self._cache = None

    def getName(self):
        return "直播·自动更新"

    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        return False

    def destroy(self):
        pass

    def localProxy(self, param):
        return None

    # ---------- 自实现 fetch 兜底（FongMi 无 fetch base 也能跑） ----------
    def _http_get_text(self, url, timeout=12):
        if requests is not None:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout, verify=False)
            if r.status_code != 200:
                return None
            return r.text
        import urllib.request
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        return resp.read().decode("utf-8", "ignore")

    def fetch(self, url, headers=None, params=None, **kw):
        # 兼容 FongMi fetch 接口签名；无环境 fetch 时走自实现
        try:
            return super().fetch(url, headers=headers, params=params, **kw)
        except Exception:
            full = url
            if params:
                import urllib.parse
                full = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
            text = self._http_get_text(full)
            if text is None:
                raise RuntimeError("fetch failed: " + url)

            class _Resp:
                def __init__(self, t):
                    self._t = t

                def json(self):
                    return json.loads(self._t)

                @property
                def text(self):
                    return self._t

                @property
                def status_code(self):
                    return 200
            return _Resp(text)

    # ---------- 候选源管理 ----------
    def _get_candidates(self):
        urls = []
        if UPDATE_LIST_URL:
            try:
                text = self._http_get_text(UPDATE_LIST_URL)
                if text:
                    for line in text.splitlines():
                        line = line.strip()
                        if line.startswith("http"):
                            urls.append(line)
            except Exception:
                urls = []
        if not urls:
            urls = list(CANDIDATE_URLS)
        seen = set()
        out = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def _probe(self, url):
        try:
            text = self._http_get_text(url)
            if text and "#EXTM3U" in text and len(text.strip()) > 50:
                return text
        except Exception:
            return None
        return None

    def _fetch_m3u(self):
        if self._cache:
            return self._cache
        candidates = self._get_candidates()
        for url in candidates:
            text = self._probe(url)
            if text is not None:
                self.m3u_url = url
                self._cache = text
                break
        if self._cache is None:
            raise RuntimeError("所有候选直播源均不可用")
        return self._cache

    # ---------- m3u 解析 ----------
    def _parse_m3u(self):
        text = self._fetch_m3u()
        lines = text.splitlines()
        groups = {}
        cur_group = "默认"
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("#EXTINF"):
                m = re.search(r'group-title="([^"]*)"', line)
                if m:
                    cur_group = m.group(1).strip() or "默认"
                name = ""
                if "," in line:
                    name = line.split(",", 1)[1].strip()
                url = ""
                j = i + 1
                while j < len(lines) and (lines[j].strip() == "" or lines[j].strip().startswith("#")):
                    j += 1
                if j < len(lines):
                    url = lines[j].strip()
                if url:
                    groups.setdefault(cur_group, []).append({"name": name, "url": url})
                    i = j
                    continue
            i += 1
        return groups

    def liveContent(self):
        try:
            groups = self._parse_m3u()
        except Exception:
            return "[]"
        arr = []
        for g, items in groups.items():
            for it in items:
                arr.append({
                    "channel_group": g,
                    "channel_name": it["name"] or "未知",
                    "url": it["url"],
                })
        return json.dumps(arr, ensure_ascii=False)

    def homeContent(self, filter):
        try:
            groups = self._parse_m3u()
        except Exception:
            return "{}"
        classes = [{"type_id": str(idx), "type_name": g} for idx, g in enumerate(groups)]
        return json.dumps({"class": classes, "list": []}, ensure_ascii=False)

    def categoryContent(self, tid, pg, filter, extend):
        try:
            groups = self._parse_m3u()
        except Exception:
            return "{}"
        keys = list(groups.keys())
        if not keys:
            return "{}"
        try:
            g = keys[int(tid)]
        except Exception:
            g = keys[0]
        vods = []
        for it in groups.get(g, []):
            vods.append({
                "vod_id": it["url"],
                "vod_name": it["name"] or "未知",
                "vod_pic": "",
                "vod_remarks": "直播",
                "vod_play_from": "直链",
                "vod_play_url": it["name"] + "$" + it["url"],
            })
        return json.dumps({"page": 1, "pagecount": 1, "limit": len(vods), "total": len(vods), "list": vods}, ensure_ascii=False)

    def detailContent(self, ids):
        url = ids[0] if isinstance(ids, list) else ids
        name = "直播间"
        try:
            groups = self._parse_m3u()
            for items in groups.values():
                for it in items:
                    if it["url"] == url:
                        name = it["name"]
                        break
        except Exception:
            pass
        vod = {
            "vod_id": url,
            "vod_name": name,
            "vod_pic": "",
            "type_name": "直播",
            "vod_play_from": "直链",
            "vod_play_url": name + "$" + url,
        }
        return json.dumps({"list": [vod]}, ensure_ascii=False)

    def playerContent(self, flag, id, vipFlags):
        return json.dumps({"parse": 0, "url": id}, ensure_ascii=False)
