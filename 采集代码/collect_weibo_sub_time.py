# -*- coding: utf-8 -*-
"""
补充楼中楼（sub）时间：给 result/weibo_comments_replies.csv 中 created_at 为空的子评论回填时间。

背景：
    新版 m.weibo.cn 详情页已不再渲染楼中楼（子评论），Playwright 只能采到主评论，
    因此结果里的 sub 行时间全为空（这部分是从旧结果回填的，本身也没时间）。
    但移动端评论接口 hotflow 的每条主评论都带 comments 字段，里面就是子评论，
    并且含 created_at —— 所以用接口补。

做法：
    按微博拉 hotflow（翻页），收集所有子评论的文本与时间，用清洗后的文本
    与结果文件里“created_at 为空”的 sub 行逐一匹配，命中就回填时间。
    只写 created_at 一列；主评论、已有时间的行、其它列一律不动。

安全：
    - 首次运行前把结果备份到 采集中间文件/weibo_comments_replies_补采前备份.csv
    - 独立断点 采集中间文件/weibo_sub_time_checkpoint.json，中断后可续跑
    - 只做 GET 请求，不会删除或改写微博端任何内容

运行：
    python collect_weibo_sub_time.py
    # 小规模验证（只处理前 5 个微博、不写盘，仅打印命中率）
    set WEIBO_SUB_LIMIT=5
    set WEIBO_SUB_DRY=1
"""
import collections
import csv
import html
import json
import os
import random
import re
import sys
import time
from email.utils import parsedate_to_datetime

import requests

BASE = r"E:\learn\自然语言处理\大作业"
RESULT = os.path.join(BASE, "result", "weibo_comments_replies.csv")
MID_DIR = os.path.join(BASE, "采集中间文件")
COOKIE_JSON = os.path.join(MID_DIR, "_weibo_login_cookies.json")
BACKUP = os.path.join(MID_DIR, "weibo_comments_replies_补采前备份.csv")
CHECKPOINT = os.path.join(MID_DIR, "weibo_sub_time_checkpoint.json")

FIELDS = ["platform", "weibo_id", "user", "content", "level", "created_at"]

MAX_PAGES = int(os.environ.get("WEIBO_SUB_PAGES", "5"))   # 每条微博最多翻几页热评
SLEEP_LO, SLEEP_HI = 0.7, 1.5                            # 请求间隔（防风控）
SAVE_EVERY = 100                                          # 每处理多少个微博落一次断点
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148")

_TAG_RE = re.compile(r"<[^>]+>")
_EMOJI_RE = re.compile(r'<span class="url-icon">.*?</span>', re.S)
_WS_RE = re.compile(r"[\s\u200b\u200c\u200d\uFEFF]+")


def clean_text(s):
    """接口 text（HTML）转成与页面 textContent 一致的纯文本"""
    if not s:
        return ""
    s = _EMOJI_RE.sub("", s)          # 表情图片，页面上不产出文字
    s = _TAG_RE.sub("", s)            # 其余标签（@链接、网页链接）
    s = html.unescape(s)
    return s.strip()


def norm_key(s):
    """匹配用归一化：去掉全部空白与零宽字符"""
    return _WS_RE.sub("", (s or "").strip())


_PREFIX_RE = re.compile(r"^(?P<name>[^:：\s]{1,20})[:：](?P<body>.+)$", re.S)


def strip_prefix(s):
    """旧版页面把子评论渲染成 “用户名:正文”，接口只给正文，匹配前剥掉用户名前缀"""
    m = _PREFIX_RE.match((s or "").strip())
    return m.group("body") if m else (s or "")


def candidates(raw):
    """一条子评论可能的匹配形态：原样 + 去用户名前缀"""
    out = [norm_key(raw)]
    body = norm_key(strip_prefix(raw))
    if body and body != out[0]:
        out.append(body)
    return out


def api_time(s):
    """接口时间 'Wed Sep 27 10:54:13 +0800 2023' -> 'YYYY-MM-DD HH:MM'"""
    if not s:
        return ""
    try:
        dt = parsedate_to_datetime(s)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def load_headers():
    cks = json.load(open(COOKIE_JSON, encoding="utf-8"))["cookies"]
    cookie = "; ".join("%s=%s" % (c["name"], c["value"])
                       for c in cks if str(c.get("domain", "")).find("weibo.cn") >= 0)
    return {
        "User-Agent": UA,
        "X-Requested-With": "XMLHttpRequest",
        "MWeibo-Pwa": "1",
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie,
    }


def fetch_sub_times(wid, headers, stats):
    """拉一条微博的 hotflow，返回 (文本->时间, 抓到的子评论数)"""
    out = {}
    max_id, max_id_type, pages = 0, 0, 0
    while pages < MAX_PAGES:
        url = ("https://m.weibo.cn/comments/hotflow?id=%s&mid=%s&max_id=%s&max_id_type=%s"
               % (wid, wid, max_id, max_id_type))
        try:
            r = requests.get(url, headers=headers, timeout=20)
            j = r.json()
        except Exception as e:
            stats["err"] += 1
            break
        if j.get("ok") != 1:
            stats["empty"] += 1
            break
        d = j.get("data") or {}
        arr = d.get("data") or []
        for a in arr:
            for c in (a.get("comments") or []):
                t = norm_key(clean_text(c.get("text")))
                tm = api_time(c.get("created_at"))
                if t and tm:
                    out.setdefault(t, tm)
        nid, ntype = d.get("max_id"), d.get("max_id_type")
        if not arr or not nid or nid == max_id:
            break
        max_id, max_id_type = nid, (ntype if ntype is not None else max_id_type)
        pages += 1
        time.sleep(random.uniform(SLEEP_LO, SLEEP_HI))
    return out, len(out)


def match_one(text_norm, table):
    """完全相等优先；否则退到前 12 字唯一命中"""
    if text_norm in table:
        return table[text_norm]
    if len(text_norm) >= 12:
        pre = text_norm[:12]
        cands = [v for k, v in table.items() if k.startswith(pre)]
        if len(cands) == 1:
            return cands[0]
    return ""


def match_candidates(raw, table):
    """按多种文本形态依次匹配，返回命中的时间"""
    for k in candidates(raw):
        tm = match_one(k, table)
        if tm:
            return tm
    return ""


def main():
    dry = os.environ.get("WEIBO_SUB_DRY", "") == "1"
    limit = int(os.environ.get("WEIBO_SUB_LIMIT", "0") or 0)

    if not os.path.exists(COOKIE_JSON):
        print("缺少登录态文件：%s（请先跑一次 collect_weibo_replies_time.py 完成登录）" % COOKIE_JSON)
        raise SystemExit(1)
    headers = load_headers()

    with open(RESULT, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    print("结果总行数:", len(rows))

    # 需要补时间的 sub 行，按微博分组
    need = collections.defaultdict(list)
    for i, r in enumerate(rows):
        if r.get("level") == "sub" and not (r.get("created_at") or "").strip():
            need[r.get("weibo_id")].append(i)
    print("待补时间子评论:", sum(len(v) for v in need.values()), "条，涉及微博", len(need), "个")

    cp = {"done": [], "fills": {}}
    if os.path.exists(CHECKPOINT):
        try:
            cp = json.load(open(CHECKPOINT, encoding="utf-8"))
            print("断点恢复：已处理 %d 个微博" % len(cp.get("done", [])))
        except Exception:
            pass
    done = set(cp.get("done", []))
    fills = cp.get("fills", {})          # {weibo_id: [[行号, 时间], ...]}

    # 把断点里已填的写回内存
    for wid, pairs in fills.items():
        for idx, tm in pairs:
            if 0 <= int(idx) < len(rows):
                rows[int(idx)]["created_at"] = tm

    todo = [w for w in need if w not in done]
    if limit > 0:
        todo = todo[:limit]
    print("本次待处理微博:", len(todo))

    stats = collections.Counter()
    t0 = time.time()
    for n, wid in enumerate(todo, 1):
        table, got = fetch_sub_times(wid, headers, stats)
        pairs = []
        for idx in need[wid]:
            tm = match_candidates(rows[idx].get("content"), table)
            if tm:
                pairs.append([idx, tm])
                if not dry:
                    rows[idx]["created_at"] = tm
        stats["hit"] += len(pairs)
        stats["miss"] += len(need[wid]) - len(pairs)
        if pairs:
            fills[wid] = pairs
        done.add(wid)

        print("  %d/%d 微博%s 接口子评论%d 本微博待补%d 命中%d | 累计 命中%d 未命中%d 错误%d" % (
            n, len(todo), wid, got, len(need[wid]), len(pairs),
            stats["hit"], stats["miss"], stats["err"]))

        if not dry and n % SAVE_EVERY == 0:
            cp = {"done": sorted(done), "fills": fills}
            json.dump(cp, open(CHECKPOINT, "w", encoding="utf-8"), ensure_ascii=False)

        if stats["err"] >= 5:
            print("  连续请求失败较多，暂停 60 秒…")
            time.sleep(60)
            stats["err"] = 0

    total_need = stats["hit"] + stats["miss"]
    rate = 100.0 * stats["hit"] / total_need if total_need else 0
    print("\n补采命中：%d / %d（%.1f%%）| 请求失败 %d" % (stats["hit"], total_need, rate, stats["err"]))

    if dry:
        print("（干跑模式，未写盘）耗时 %.1f 分钟" % ((time.time() - t0) / 60.0))
        return

    if not os.path.exists(BACKUP):
        import shutil
        shutil.copy2(RESULT, BACKUP)
        print("已备份补采前结果：%s" % BACKUP)

    cp = {"done": sorted(done), "fills": fills}
    json.dump(cp, open(CHECKPOINT, "w", encoding="utf-8"), ensure_ascii=False)

    with open(RESULT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows([{k: r.get(k, "") for k in FIELDS} for r in rows])

    still = sum(1 for r in rows if r.get("level") == "sub"
                and not (r.get("created_at") or "").strip())
    timed = sum(1 for r in rows if (r.get("created_at") or "").strip())
    print("已写回：%s" % RESULT)
    print("当前带时间 %d / %d 行；仍无时间的子评论 %d 条" % (timed, len(rows), still))
    print("耗时 %.1f 分钟" % ((time.time() - t0) / 60.0))


if __name__ == "__main__":
    main()
