# -*- coding: utf-8 -*-
"""
微博（移动端 + PC端高级搜索）自适应采集
主题：预制菜进校园热点事件网络文本
功能：
  1) 话题标签 -> 话题页接口：采该话题下全部微博（覆盖全量，最新为主）
  2) 搜索词   -> 搜索接口：补充不带话题标签的相关微博
  3) PC高级搜索(s.weibo.com) 按月分段：补齐2023-2024历史舆情（时序分析的核心数据）
  4) 主动学习自举扩展关键词，多轮迭代直至词库收敛
采集阶段不筛选：时间/相关性/去重等筛选由预处理阶段负责。
防封号：每次请求后(0.3,1.5)秒随机停顿 + User-Agent轮换
注意：未登录时微博接口返回风控码，需在COOKIE/COOKIE_PC处填入登录Cookie
"""
import os
import sys
import csv
import re
import time
import random
import calendar
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import bootstrapper as boot

#路径
PLATFORM = "weibo"
RESULT_DIR = r"E:\learn\自然语言处理\大作业\result"
BASE_DIR = r"E:\learn\自然语言处理\大作业"
RESULT_FILE = "weibo_comments.csv"

#登录Cookie：登录 m.weibo.cn 后，复制浏览器请求头中的 Cookie 填入
COOKIE = "SCF=AllV1s3dfp_3bnqUilHAae0xk3G9HVZt_IrIO9LslzcdLGHjFsUy-NEjNXvdGDXqc2M-_SEdsyB243pu5m5mS0o.; SUB=_2A25H5MfeDeRhGeFN6VEV-SfOyj-IHXVkmEUWrDV6PUJbktAYLVL9kW1NQEDax0S18P6emdX-33i0ZSQwy-7aT_44; SUBP=0033WrSXqPxfM725Ws9jqgMF55529P9D9W53QpvORgW8m2RuMTPhRsY85NHD95QNe0z0Sh.4eo20Ws4Dqcjai--fiKnNiKyhi--fiKnXi-isi--fiK.0iKy2i--fiKysi-88PEH8SCHFxF-4efYt; SSOLoginState=1793111950; ALF=1795703950; WEIBOCN_FROM=1110006030; MLOGIN=1; _T_WM=91073756007; XSRF-TOKEN=8cf581; M_WEIBOCN_PARAMS=uicode%3D20000174"

#PC端 Cookie：登录 s.weibo.com 后，复制浏览器请求头中的 Cookie 填入
COOKIE_PC = "SINAGLOBAL=3469834131872.4126.1775485691886; ULV=1775485691937:1:1:1:3469834131872.4126.1775485691886:; XSRF-TOKEN=20q9EYAvQ40QHlf7rBFkwSfW; SCF=AllV1s3dfp_3bnqUilHAae0xk3G9HVZt_IrIO9LslzcdE9x5fXTHEmEZIsqjIad6yUpR9ICagjMftK9aWwDfjik.; SUB=_2A25H5MfdDeRhGeFN6VEV-SfOyj-IHXVkmEUVrDV8PUNbmtAYLXWjkW9NQEDax0MVdidjPafpfh7YRGHxE6QGxnBl; SUBP=0033WrSXqPxfM725Ws9jqgMF55529P9D9W53QpvORgW8m2RuMTPhRsY85JpX5KzhUgL.FoM0eoeX1K.EeKe2dJLoIEYLxK-L1hML12eLxK-L1hBLB.qLxK-L1KeL12BLxK-L12qLB-Hki--fi-i2iK.0P7tt; ALF=02_1795703949; WBPSESS=yE-rxuK543vKnEY2lVVaWWK6QyYcbj6xU5i81YjNDqvjS0QOxNGMU428vwPJ6YkhlwzbADv58c2LqSJbLudjoflVkjXhGiN4kJ7Rk3usIx-lB-myHtYPc4OmikfoH5OTOdruy-CWIZqCFdsFh5CLJw=="

#断点续采 / 候选词 / 饱和日志文件（过程文件放根目录，result只留csv）
CHECKPOINT = os.path.join(BASE_DIR, "weibo_checkpoint.json")
LOG_FILE = os.path.join(BASE_DIR, "weibo_collect_log.txt")
CANDIDATE = os.path.join(BASE_DIR, "weibo_candidate_keywords.txt")

#csv字段（纯采集字段，不含预处理列）
FIELDS = ["platform", "keyword", "weibo_id", "user", "created_at",
          "text", "reposts_count", "comments_count", "likes_count"]

#话题标签种子集：微博话题页是"预制菜进校园"舆情的主入口，
#话题页能翻到该话题下全部微博（total合计约4000+条），远多于搜索接口
TOPIC_TAGS = [
    "#预制菜进校园#", "#预制菜#",
    "#该不该抵制预制菜进校园#", "#预制菜可作补充但不宜作为主菜#",
    "#预制菜进校园有学生家长辞职送饭#",
    "#上海家长希望西贝竞标学生餐#", "#上海学生餐#", "#315预制菜#", "#西贝#",
]

#PC高级搜索关键词（历史采集）：按"关键词 × 月份"矩阵补齐历史舆情
SWEIBO_KEYWORDS = ["预制菜进校园", "预制菜", "校园预制菜"]

#多线程并行数（关键词级并行；3线程较保守，
#整体请求速率仍由bootstrapper全局限速GLOBAL_MIN_INTERVAL控制）
WORKERS = 3

#PC历史采集的风控冷却参数：
#  段内遇418 -> 冷却COOLDOWN_SECONDS后重试当前页，最多MAX_COOLDOWN次；
#  累计MAX_CONSEC_418个段被418放弃 -> 全局冷却GLOBAL_COOLDOWN秒（IP级风控）
COOLDOWN_SECONDS = 120
MAX_COOLDOWN = 3
GLOBAL_COOLDOWN = 300
MAX_CONSEC_418 = 3


def _history_months():
    """2023-06 ~ 2025-12 逐月列表（时序演化研究的时间范围）。"""
    out = []
    for y in range(2023, 2026):
        start = 6 if y == 2023 else 1
        for m in range(start, 13):
            out.append((y, m))
    return out


#请求头（UA由bootstrapper轮换；补全浏览器特征头降低风控识别率）
HEADERS = {
    "Referer": "https://m.weibo.cn/search?containerid=100103type%3D1",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}


def _append_blog(rows, keyword, m):
    """把一条微博卡片m整理为采集行。"""
    rows.append({
        "platform": PLATFORM,
        "keyword": keyword,
        "id": m.get("id", ""),
        "weibo_id": m.get("id", ""),
        "user": (m.get("user") or {}).get("screen_name", ""),
        "created_at": m.get("created_at", ""),
        "time": m.get("created_at", ""),
        "text": boot.clean_text(m.get("text")),
        "reposts_count": m.get("reposts_count", 0),
        "comments_count": m.get("comments_count", 0),
        "likes_count": m.get("attitudes_count", 0),
    })


def fetcher(keyword):
    """采集某关键词下的所有微博（主动学习引擎调用，返回采集行列表）。
    话题标签（#...#）走话题页接口：能翻到该话题下全部微博（覆盖全量）；
    普通词走搜索接口：补充不带话题标签的相关微博。"""
    session = requests.Session()
    session.headers.update(HEADERS)
    session.headers["User-Agent"] = boot.random_ua()
    session.cookies.update(dict([x.strip().split("=", 1) for x in COOKIE.split(";") if "=" in x]))
    rows = []
    is_topic = keyword.startswith("#") and keyword.endswith("#")
    empty_pages = 0                                 #连续空页计数（到底即停）
    for page in range(1, 51):                       #每个入口最多翻50页（尽量采全）
        try:
            if is_topic:
                #话题页接口：containerid=231522type=61&q=#话题#
                r = boot.http_get(
                    session, "https://m.weibo.cn/api/container/getIndex",
                    {"containerid": "231522type=61&q=%s" % keyword,
                     "page": page}, retries=2)
            else:
                #搜索接口
                r = boot.http_get(
                    session, "https://m.weibo.cn/api/container/getIndex",
                    {"containerid": "100103type=1&q=%s" % keyword,
                     "page_type": "searchall", "page": page}, retries=2)
            data = r.json()
        except Exception as e:
            print("  [%s] 第%d页失败：%s" % (keyword, page, e))
            break
        if data.get("ok") != 1:
            break
        cards = data.get("data", {}).get("cards", []) or []
        n = 0
        for card in cards:
            if card.get("card_type") != 9:
                continue
            _append_blog(rows, keyword, card.get("mblog") or {})
            n += 1
        if n == 0:                                  #本页无微博 -> 空页计数
            empty_pages += 1
            if empty_pages >= 2:
                break
        else:
            empty_pages = 0
        boot.random_delay()                         #防封号
    return rows


def _parse_sweibo_card(card, keyword, y, m):
    """解析s.weibo.com搜索结果HTML中的一张微博卡片。"""
    mid = re.search(r'\smid="(\d+)"', card)
    if not mid:
        return None
    user = re.search(r'nick-name="([^"]+)"', card)
    text = re.search(r'<p[^>]*node-type="feed_list_content"[^>]*>(.*?)</p>', card, re.S)
    tm = re.search(r'<div class="from"\s*>.*?<a[^>]*>\s*([^<]+?)\s*</a>', card, re.S)
    raw_time = tm.group(1).strip() if tm else ""
    mt = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日\s*(?:(\d{2}:\d{2}))?', raw_time)
    if mt:
        created_at = "%s-%02d-%02d %s:00" % (
            mt.group(1), int(mt.group(2)), int(mt.group(3)), mt.group(4) or "00:00")
    else:
        created_at = "%04d-%02d-01 00:00" % (y, m)   #解析失败时兜底为当月
    txt = re.sub(r'<[^>]+>', '', text.group(1)) if text else ""
    txt = re.sub(r'\s+', ' ', txt).strip()
    return {
        "platform": PLATFORM,
        "keyword": keyword,
        "id": mid.group(1),
        "weibo_id": mid.group(1),
        "user": user.group(1) if user else "",
        "created_at": created_at,
        "time": created_at,
        "text": txt,
        "reposts_count": 0,                          #PC搜索页互动数需JS渲染，静态HTML取不到
        "comments_count": 0,
        "likes_count": 0,
    }


def _fetch_sweibo_segment(session, kw, y, m, log=True):
    """采集一个"关键词×月份"段（PC高级搜索按月翻页）。
    遇418风控：冷却COOLDOWN_SECONDS后重试当前页（最多MAX_COOLDOWN次），
    不轻易放弃剩余页；仍失败则该段按已采页收尾。
    返回 (该段采集行, 是否因418放弃剩余页)。"""
    last_day = calendar.monthrange(y, m)[1]
    timescope = "custom:%04d-%02d-01-0:%04d-%02d-%02d-23" % (y, m, y, m, last_day)
    seg = []
    empty = 0
    cooldowns = 0
    wind_up = False                               #最终是否因418放弃
    for page in range(1, 51):
        try:
            r = boot.http_get(
                session, "https://s.weibo.com/weibo",
                {"q": kw, "timescope": timescope, "Refer": "g", "page": page},
                retries=2, timeout=(8, 30))
        except Exception as e:
            if "418" in str(e) and cooldowns < MAX_COOLDOWN:
                cooldowns += 1
                if log:
                    print("  [历史 %s %04d-%02d] 第%d页被风控，冷却%d秒后重试(%d/%d)"
                          % (kw, y, m, page, COOLDOWN_SECONDS, cooldowns, MAX_COOLDOWN))
                time.sleep(COOLDOWN_SECONDS + random.uniform(0, 10))
                continue
            if "418" in str(e):
                wind_up = True
            print("  [历史 %s %04d-%02d] 第%d页失败：%s" % (kw, y, m, page, e))
            break
        cards = re.split(r'<div class="card-wrap"', r.text)[1:]
        if not cards:
            break
        n = 0
        for card in cards:
            row = _parse_sweibo_card(card, kw, y, m)
            if not row:
                continue
            seg.append(row)
            n += 1
        if n == 0:
            empty += 1
            if empty >= 2:
                break
        else:
            empty = 0
        boot.random_delay()                      #防封号
    if log:
        print("  [历史] %s %04d-%02d 段完成，本段 %d 条%s"
              % (kw, y, m, len(seg), "（被风控截断）" if wind_up else ""))
    return seg, wind_up


def fetch_sweibo_history(seen_ids, workers=WORKERS, log=True):
    """PC高级搜索历史采集（多线程）：核心词 × 2023-06~2025-12逐月并行，
    补齐历史舆情文本。每段独立session（UA轮换）；跨段/跨源统一在主线程去重。
    累计MAX_CONSEC_418个段被418放弃时，全局冷却GLOBAL_COOLDOWN秒（IP级风控）。"""
    tasks = [(kw, y, m) for kw in SWEIBO_KEYWORDS for y, m in _history_months()]
    if workers > 1 and len(tasks) > 1:
        results = []
        consec_418 = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_fetch_sweibo_segment, _new_pc_session(), kw, y, m, log): (kw, y, m)
                       for kw, y, m in tasks}
            for fut in as_completed(futures):
                try:
                    seg, wind = fut.result()
                except Exception as e:
                    print("  [历史] 某段采集异常：%s" % e)
                    continue
                if wind:
                    consec_418 += 1
                    if consec_418 >= MAX_CONSEC_418:
                        if log:
                            print("  [历史] 连续%d段被418风控，全局冷却%d秒（IP级风控缓解）"
                                  % (consec_418, GLOBAL_COOLDOWN))
                        time.sleep(GLOBAL_COOLDOWN + random.uniform(0, 30))
                        consec_418 = 0
                else:
                    consec_418 = 0
                results.append(seg)
    else:
        results = []
        consec_418 = 0
        for kw, y, m in tasks:
            seg, wind = _fetch_sweibo_segment(_new_pc_session(), kw, y, m, log)
            if wind:
                consec_418 += 1
                if consec_418 >= MAX_CONSEC_418:
                    if log:
                        print("  [历史] 连续%d段被418风控，全局冷却%d秒（IP级风控缓解）"
                              % (consec_418, GLOBAL_COOLDOWN))
                    time.sleep(GLOBAL_COOLDOWN + random.uniform(0, 30))
                    consec_418 = 0
            else:
                consec_418 = 0
            results.append(seg)
    #主线程统一去重（seen_ids与移动端数据共享）
    rows = []
    for seg in results:
        for row in seg:
            iid = row["id"]
            if iid and iid in seen_ids:
                continue
            if iid:
                seen_ids.add(iid)
            rows.append(row)
    return rows


def _new_pc_session():
    """新建PC端采集session（独立Cookie/UA，供各线程使用）。"""
    s = requests.Session()
    s.headers.update({
        "Referer": "https://s.weibo.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": boot.random_ua(),
    })
    s.cookies.update(dict([x.strip().split("=", 1) for x in COOKIE_PC.split(";") if "=" in x]))
    return s


def _new_mobile_session():
    """新建移动端session（m.weibo.cn Cookie，供补采互动数等使用）。"""
    s = requests.Session()
    s.headers.update({
        "Referer": "https://m.weibo.cn/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "User-Agent": boot.random_ua(),
    })
    s.cookies.update(dict([x.strip().split("=", 1) for x in COOKIE.split(";") if "=" in x]))
    return s


def fetch_interactions(session, mid):
    """补查单条微博互动数（m.weibo.cn statuses/extend 接口）。
    返回 (转发, 评论, 点赞)；失败返回None。"""
    try:
        r = boot.http_get(session, "https://m.weibo.cn/statuses/extend",
                          {"id": mid}, retries=2, timeout=(8, 15))
        data = r.json()
        if data.get("ok") == 1:
            d = data.get("data") or {}
            return (d.get("reposts_count", 0), d.get("comments_count", 0),
                    d.get("attitudes_count", 0))
    except Exception:
        pass
    return None


def enrich_weibo_interactions(csv_path, workers=WORKERS, log=True):
    """互动数补采：对CSV中转发/评论/点赞全为0的行调用 statuses/extend 补查
    （PC历史采集取不到互动数，置0；移动端采集已有真实值）。
    多线程并行；重跑时只补仍未补到的行（断点续补）。返回成功补到的行数。"""
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = list(csv.DictReader(f))
    if not reader:
        return 0
    targets = [i for i, r in enumerate(reader)
               if r.get("weibo_id")
               and not (int(r.get("reposts_count") or 0)
                        or int(r.get("comments_count") or 0)
                        or int(r.get("likes_count") or 0))]
    if log:
        print("互动数补采：待补 %d 行（共 %d 行，%d线程）" % (len(targets), len(reader), workers))
    if not targets:
        print("互动数补采：无待补行（数据已完整）")
        return 0

    def _work(i):
        mid = reader[i].get("weibo_id", "")
        if not mid:
            return None
        got = fetch_interactions(_new_mobile_session(), mid)
        boot.random_delay()                        #防封号
        return (i, got) if got else None

    done = 0
    fail = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_work, i): i for i in targets}
        for fut in as_completed(futures):
            res = fut.result()
            if res and res[1]:
                i, (rc, cc, lc) = res
                reader[i]["reposts_count"] = rc
                reader[i]["comments_count"] = cc
                reader[i]["likes_count"] = lc
                done += 1
            else:
                fail += 1
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=reader[0].keys())
        w.writeheader()
        w.writerows(reader)
    if log:
        print("互动数补采完成：成功 %d 行，失败 %d 行" % (done, fail))
    return done


def main():
    import sys
    if "--enrich-only" in sys.argv:                #仅补采互动数（不重新采集）
        out_path = os.path.join(RESULT_DIR, RESULT_FILE)
        if not os.path.exists(out_path):
            print("未找到结果文件：%s，请先运行完整采集。" % out_path)
            return
        enrich_weibo_interactions(out_path, workers=WORKERS)
        return

    os.makedirs(RESULT_DIR, exist_ok=True)
    if not COOKIE:
        print("【提示】未配置 COOKIE，微博需登录后才能采集。请在 collect_weibo.py 顶部填入 Cookie 后重跑。")
        with open(os.path.join(RESULT_DIR, RESULT_FILE), "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
        print("已生成空结果文件：%s" % os.path.join(RESULT_DIR, RESULT_FILE))
        return

    #1) 移动端主动学习采集：话题标签(采话题全量) + 搜索词(补充)，自举扩展关键词
    seed = TOPIC_TAGS + list(boot.SEED_KEYWORDS)
    rows, final_keywords, history = boot.adaptive_collect(
        PLATFORM, fetcher, workers=WORKERS,
        seed_keywords=seed, exclude_words=boot.EXCLUDE_WORDS,
        checkpoint_path=CHECKPOINT, log_file=LOG_FILE, candidate_file=CANDIDATE,
        docx_log_path=os.path.join(BASE_DIR, "_work_词库记录", "词库演变记录_自动.docx"),
        min_hits=1, auto_add=True, use_classifier=True)

    #2) PC高级搜索历史采集（多线程按月分段）：补齐2023-06~2025-12历史舆情
    if COOKIE_PC:
        seen = set(r.get("id", "") for r in rows if r.get("id"))
        hist = fetch_sweibo_history(seen, workers=WORKERS)
        rows.extend(hist)
        print("PC历史采集：追加 %d 条，累计 %d 条" % (len(hist), len(rows)))

    out_path = os.path.join(RESULT_DIR, RESULT_FILE)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        #按csv表头过滤字段：fetcher返回的id/time是给引擎去重/时间筛选用的，不写入csv
        writer.writerows([{k: r.get(k, "") for k in FIELDS} for r in rows])
    print("采集完成：共 %d 条，词库扩展到 %d 个关键词，每轮新增量 %s" % (
        len(rows), len(final_keywords), history))
    print("最终词库：%s" % "、".join(sorted(final_keywords)))
    print("结果文件：%s" % out_path)

    #3) 互动数补采：PC历史行互动数为0，用statuses/extend补查转发/评论/点赞数
    enrich_weibo_interactions(out_path, workers=WORKERS)


if __name__ == "__main__":
    #评论补采模式：python collect_weibo.py --comments
    #对已采的微博正文（result/weibo_comments.csv）逐条补采评论文本
    if "--comments" in sys.argv:
        import collect_weibo_comments
        collect_weibo_comments.main()
    else:
        main()
