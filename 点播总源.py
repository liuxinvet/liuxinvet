# -*- coding: utf-8 -*-
# 点播总源（海阔式自动更新版）
# 特性：
#   1. 多候选采集API自动探测：内置多个 MacCMS 点播采集接口，逐个请求，取第一个可用的
#   2. 固定更新清单（海阔式）：UPDATE_LIST_URL 永久不变，内容是候选采集API地址列表（每行一个URL），
#      启动时先拉清单刷新候选，失败回退内置 CANDIDATE_URLS；采集源变更只需改清单文件，客户端零改动
#   3. 双兼容导入：默影视(FongMi) 与 星落(dr_py) 均可用
#   4. 自实现 fetch 兜底：无 requests 依赖也能跑
# 用法：默影视以 type=3 站点栏目加载（api 填本 py 的远程 URL），点播栏目自动切换可用采集源
try:
    from base import Spider as BaseSpider
except Exception:
    try:
        from base.spider import Spider as BaseSpider
    except Exception:
        from base_spider import Spider as BaseSpider

import json
import re
import urllib.parse

try:
    import requests
except Exception:
    requests = None

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ============ 海阔式固定更新入口：URL 永久不变，内容可随时编辑 ============
# 指向托管在 GitHub/Gitee raw 的清单文件，每行一个候选点播采集API地址
UPDATE_LIST_URL = "https://down.nigx.cn/raw.githubusercontent.com/liuxinvet/liuxinvet/refs/heads/main/%E7%82%B9%E6%92%AD%E6%BA%90%E6%9B%B4%E6%96%B0%E6%B8%85%E5%8D%95.txt"

# 内置候选采集API（清单拉取失败时的兜底），按优先级排序，逐个探测
# 均为 MacCMS 标准采集接口（/api.php/provide/vod/ 或同类），要求支持 ac=list 返回分类
CANDIDATE_URLS = [
    "http://cj.lziapi.com/api.php/provide/vod/",
    "http://ffzy5.tv/api.php/provide/vod/",
    "https://xsd.sdzyapi.com/api.php/provide/vod/",
    "https://bfzyapi.com/api.php/provide/vod/",
    "https://jszyapi.com/api.php/provide/vod/",
    "https://taopianapi.com/cjapi/mc/vod/json.html",
]


class Spider(BaseSpider):

    def __init__(self):
        super().__init__()
        self._api = None
        self._cache_class = None

    def getName(self):
        return "点播·自动更新"

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

    def _api_url(self, base, ac):
        # 在 base 上追加/替换 ac 参数
        sep = "&" if "?" in base else "?"
        # 去掉已有 ac=xxx 参数避免冲突
        base2 = re.sub(r"[?&]ac=[^&]*", "", base)
        sep2 = "&" if "?" in base2 else "?"
        return base2 + sep2 + "ac=" + ac

    def _probe(self, url):
        try:
            u = self._api_url(url, "list")
            text = self._http_get_text(u)
            if not text:
                return None
            data = json.loads(text)
            cls = data.get("class")
            if isinstance(cls, list) and cls:
                return url
        except Exception:
            return None
        return None

    def _pick_api(self):
        if self._api:
            return self._api
        for url in self._get_candidates():
            ok = self._probe(url)
            if ok is not None:
                self._api = url
                break
        if self._api is None:
            raise RuntimeError("所有候选点播采集源均不可用")
        return self._api

    def _get_class(self):
        if self._cache_class:
            return self._cache_class
        api = self._pick_api()
        u = self._api_url(api, "list")
        text = self._http_get_text(u)
        data = json.loads(text)
        cls = data.get("class") or []
        out = []
        for c in cls:
            if isinstance(c, dict) and c.get("type_id") is not None and c.get("type_name"):
                out.append({"type_id": str(c["type_id"]), "type_name": str(c["type_name"])})
        self._cache_class = out
        return out

    def _list_vods(self, tid=None, pg=1, wd=None):
        api = self._pick_api()
        params = {}
        if wd:
            params["wd"] = wd
        else:
            params["pg"] = str(pg)
            if tid:
                params["t"] = str(tid)
        u = self._api_url(api, "detail")
        for k, v in params.items():
            u += "&" + k + "=" + urllib.parse.quote(v)
        text = self._http_get_text(u)
        data = json.loads(text)
        lst = data.get("list") or []
        vods = []
        for v in lst:
            if not isinstance(v, dict):
                continue
            play_from = v.get("vod_play_from") or ""
            play_url = v.get("vod_play_url") or ""
            if not play_url:
                continue
            vods.append({
                "vod_id": str(v.get("vod_id", "")),
                "vod_name": v.get("vod_name") or "",
                "vod_pic": v.get("vod_pic") or "",
                "vod_remarks": v.get("vod_remarks") or "",
                "vod_play_from": play_from,
                "vod_play_url": play_url,
            })
        page = data.get("page") or 1
        pagecount = data.get("pagecount") or 1
        return vods, int(page), int(pagecount)

    # ---------- 标准 Spider 接口 ----------
    def homeContent(self, filter):
        try:
            cls = self._get_class()
        except Exception:
            return "{}"
        return json.dumps({"class": cls, "list": []}, ensure_ascii=False)

    def categoryContent(self, tid, pg, filter, extend):
        try:
            vods, page, pagecount = self._list_vods(tid=tid, pg=pg)
        except Exception:
            return "{}"
        return json.dumps({"page": page, "pagecount": pagecount, "limit": len(vods),
                           "total": len(vods), "list": vods}, ensure_ascii=False)

    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, list) else ids
        try:
            api = self._pick_api()
            u = self._api_url(api, "detail") + "&ids=" + urllib.parse.quote(str(vid))
            text = self._http_get_text(u)
            data = json.loads(text)
            lst = data.get("list") or []
            if not lst:
                return "{}"
            v = lst[0]
            vod = {
                "vod_id": str(v.get("vod_id", vid)),
                "vod_name": v.get("vod_name") or "",
                "vod_pic": v.get("vod_pic") or "",
                "type_name": v.get("type_name") or "",
                "vod_year": str(v.get("vod_year") or ""),
                "vod_area": v.get("vod_area") or "",
                "vod_actor": v.get("vod_actor") or "",
                "vod_director": v.get("vod_director") or "",
                "vod_content": v.get("vod_content") or "",
                "vod_play_from": v.get("vod_play_from") or "",
                "vod_play_url": v.get("vod_play_url") or "",
            }
            return json.dumps({"list": [vod]}, ensure_ascii=False)
        except Exception:
            return "{}"

    def playerContent(self, flag, id, vipFlags):
        # id 为 vod_play_url（"线路名$地址$$$地址..."）时取第一条；否则按 flag 匹配
        try:
            parts = id.split("$$$")
            first = parts[0]
            if "$" in first:
                _, url = first.split("$", 1)
                return json.dumps({"parse": 0, "url": url}, ensure_ascii=False)
            return json.dumps({"parse": 0, "url": id}, ensure_ascii=False)
        except Exception:
            return json.dumps({"parse": 0, "url": id}, ensure_ascii=False)

    def searchContent(self, key, quick, pg=1):
        try:
            vods, page, pagecount = self._list_vods(wd=key, pg=pg)
        except Exception:
            return "{}"
        return json.dumps({"page": page, "pagecount": pagecount, "limit": len(vods),
                           "total": len(vods), "list": vods}, ensure_ascii=False)
