# -*- coding: utf-8 -*-
"""
楼中楼时间·深度补采：在 collect_weibo_sub_time.py 跑完后，把还没补上的子评论时间再补一轮。

为什么需要第二轮：
    第一轮只用 hotflow 的 comments 字段（多数微博该字段是空的），命中 45.5%，
    还剩 5,048 条分散在 1,278 个微博。其中 258 个微博（各 ≥4 条）占了 3,591 条，
    集中度高 —— 对这些微博用 hotFlowChild 逐条主评论深挖，性价比最高。

原理：
    hotflow 每页给主评论（含 id 和 total_number=子评论总数），comments 字段只带一小部分；
    hotFlowChild?cid=主评论id 直接返回该主评论下的子评论（含 created_at，单页最多 8 条，不可翻页）。
    所以对 total_number>0 且 comments 不全的主评论，逐条调 hotFlowChild。

运行：
    python collect_weibo_sub_time_deep.py
    # 或只试 5 个微博不写盘
    set WEIBO_SUB_LIMIT=5
    set WEIBO_SUB_DRY=1
    set WEIBO_SUB_MIN=4      # 只处理“无时间子评论 ≥ N 条”的微博，默认 4
"""
import collections
import csv
import json
import os
import random
import time

import requests

import collect_weibo_sub_time as S

DEEP_CHECKPOINT = os.path.join(S.MID_DIR, "weibo_sub_time_deep_checkpoint.json")
FIELDS = S.FIELDS
MAX_PAGES = int(os.environ.get("WEIBO_SUB_PAGES", "6"))     # 热评最多翻几页（每页约 8 条主评论）
MAX_MAIN = int(os.environ.get("WEIBO_SUB_MAXMAIN", "40"))    # 每微博最多深挖几条主评论
SLEEP_LO, SLEEP_HI = 0.5, 1.0
SAVE_EVERY = 60


def deep_collect(wid, headers, stats):
    """返回 {归一化文本: 时间}，含 hotflow 与 hotFlowChild 两路数据"""
    table = {}

    def absorb(items):
        for c in items:
            t = S.norm_key(S.clean_text(c.get("text")))
            tm = S.api_time(c.get("created_at"))
            if t and tm:
                table.setdefault(t, tm)

    mains = []
    max_id, max_id_type, pages = 0, 0, 0
    while pages < MAX_PAGES:
        url = ("https://m.weibo.cn/comments/hotflow?id=%s&mid=%s&max_id=%s&max_id_type=%s"
               % (wid, wid, max_id, max_id_type))
        try:
            j = requests.get(url, headers=headers, timeout=20).json()
        except Exception:
            stats["err"] += 1
            break
        if j.get("ok") != 1:
            break
        d = j.get("data") or {}
        arr = d.get("data") or []
        for a in arr:
            mains.append(a)
            absorb(a.get("comments") or [])
        nid, ntype = d.get("max_id"), d.get("max_id_type")
        if not arr or not nid or nid == max_id:
            break
        max_id, max_id_type = nid, (ntype if ntype is not None else max_id_type)
        pages += 1
        time.sleep(random.uniform(SLEEP_LO, SLEEP_HI))

    checked = 0
    for a in mains:
        if checked >= MAX_MAIN:
            break
        try:
            tn = int(a.get("total_number") or 0)
        except Exception:
            tn = 0
        have = len(a.get("comments") or [])
        if tn <= have:
            continue
        cid = a.get("id")
        if not cid:
            continue
        try:
            j2 = requests.get(
                "https://m.weibo.cn/comments/hotFlowChild?cid=%s&max_id=0&max_id_type=0" % cid,
                headers=headers, timeout=20).json()
        except Exception:
            stats["err"] += 1
            continue
        d2 = j2.get("data")
        subs = d2 if isinstance(d2, list) else ((d2 or {}).get("data") or [])
        absorb(subs)
        checked += 1
        stats["child"] += 1
        time.sleep(random.uniform(SLEEP_LO, SLEEP_HI))
    return table


def main():
    dry = os.environ.get("WEIBO_SUB_DRY", "") == "1"
    limit = int(os.environ.get("WEIBO_SUB_LIMIT", "0") or 0)
    min_sub = int(os.environ.get("WEIBO_SUB_MIN", "4") or 4)

    headers = S.load_headers()
    with open(S.RESULT, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    need = collections.defaultdict(list)
    for i, r in enumerate(rows):
        if r.get("level") == "sub" and not (r.get("created_at") or "").strip():
            need[r.get("weibo_id")].append(i)
    total_need = sum(len(v) for v in need.values())
    targets = [w for w, idxs in need.items() if len(idxs) >= min_sub]
    covered = sum(len(need[w]) for w in targets)
    print("仍无时间子评论: %d 条 / %d 个微博" % (total_need, len(need)))
    print("按阈值 ≥%d 条筛选: %d 个微博，覆盖 %d 条（%.1f%%）" % (
        min_sub, len(targets), covered, 100.0 * covered / total_need if total_need else 0))

    cp = {"done": [], "fills": {}}
    if os.path.exists(DEEP_CHECKPOINT):
        try:
            cp = json.load(open(DEEP_CHECKPOINT, encoding="utf-8"))
            print("断点恢复：已处理 %d 个微博" % len(cp.get("done", [])))
        except Exception:
            pass
    done = set(cp.get("done", []))
    fills = cp.get("fills", {})
    for wid, pairs in fills.items():
        for idx, tm in pairs:
            if 0 <= int(idx) < len(rows):
                rows[int(idx)]["created_at"] = tm

    todo = [w for w in targets if w not in done]
    if limit > 0:
        todo = todo[:limit]
    print("本次待处理微博:", len(todo))

    stats = collections.Counter()
    t0 = time.time()
    for n, wid in enumerate(todo, 1):
        headers["Referer"] = "https://m.weibo.cn/detail/" + wid
        table = deep_collect(wid, headers, stats)
        pairs = []
        for idx in need[wid]:
            tm = S.match_candidates(rows[idx].get("content"), table)
            if tm:
                pairs.append([idx, tm])
                if not dry:
                    rows[idx]["created_at"] = tm
        stats["hit"] += len(pairs)
        stats["miss"] += len(need[wid]) - len(pairs)
        if pairs:
            fills[wid] = pairs
        done.add(wid)
        print("  %d/%d 微博%s 词表%d 待补%d 命中%d | 累计命中%d 未命中%d child请求%d 错误%d" % (
            n, len(todo), wid, len(table), len(need[wid]), len(pairs),
            stats["hit"], stats["miss"], stats["child"], stats["err"]))
        if not dry and n % SAVE_EVERY == 0:
            json.dump({"done": sorted(done), "fills": fills},
                      open(DEEP_CHECKPOINT, "w", encoding="utf-8"), ensure_ascii=False)
        if stats["err"] >= 5:
            print("  请求失败较多，暂停 60 秒…")
            time.sleep(60)
            stats["err"] = 0

    hit_total = stats["hit"] + stats["miss"]
    print("\n本轮命中：%d / %d（%.1f%%）| child 请求 %d | 失败 %d" % (
        stats["hit"], hit_total, 100.0 * stats["hit"] / hit_total if hit_total else 0,
        stats["child"], stats["err"]))

    if dry:
        print("（干跑模式，未写盘）耗时 %.1f 分钟" % ((time.time() - t0) / 60.0))
        return

    json.dump({"done": sorted(done), "fills": fills},
              open(DEEP_CHECKPOINT, "w", encoding="utf-8"), ensure_ascii=False)
    with open(S.RESULT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows([{k: r.get(k, "") for k in FIELDS} for r in rows])

    still = sum(1 for r in rows if r.get("level") == "sub"
                and not (r.get("created_at") or "").strip())
    timed = sum(1 for r in rows if (r.get("created_at") or "").strip())
    print("已写回：%s" % S.RESULT)
    print("当前带时间 %d / %d 行；仍无时间子评论 %d 条" % (timed, len(rows), still))
    print("耗时 %.1f 分钟" % ((time.time() - t0) / 60.0))


if __name__ == "__main__":
    main()
