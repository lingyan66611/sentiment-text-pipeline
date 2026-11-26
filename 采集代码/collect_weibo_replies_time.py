# -*- coding: utf-8 -*-
"""
微博评论【带时间】补采脚本（基于 collect_weibo_comments.py 新建，原脚本保持不动）

为什么要新开一版：
    原版用 Playwright 打开 m.weibo.cn/detail/{id} 从 DOM 提取评论，EXTRACT_JS 只取了
    h3 正文、h4 用户名和 .cmt-sub-txt 子评论文本，页面上的时间节点从未读取，
    因此 result/weibo_comments_replies.csv 只有 5 列、没有时间。
    本版在完全相同的采集流程上多取一个时间字段，其余策略不变。

输出：result/weibo_comments_replies.csv
    字段 platform, weibo_id, user, content, level, created_at
    created_at 统一为 "YYYY-MM-DD HH:MM"；页面给的是相对时间（3小时前/昨天 14:30）时按采集时刻换算；
    楼中楼子评论页面不显示独立时间，一律留空（不猜、不继承主评论时间）。

策略（沿用原版）：
    - 只采 comments_count>0 的微博，3 个浏览器并行
    - 滚动直到连续多轮无新增（摘全评论）
    - 断点续采（独立断点文件：必须重采才能拿到时间）
    - 连续失败过多自动暂停防风控

与"其他文件不动"相关的处理：
    - 首次运行会把无时间版结果备份到 采集中间文件/weibo_comments_replies_无时间备份.csv
    - 不读写 collect_weibo_comments.py 的任何文件

运行：
    python collect_weibo_replies_time.py
    小规模试跑（先跑 5 条微博验证时间是否抓到）：
    set WEIBO_CMT_LIMIT=5    （cmd）
    $env:WEIBO_CMT_LIMIT=5   （PowerShell）
"""
import os
import csv
import json
import re
import shutil
import datetime
import time
import random
import threading
from queue import Queue

from collect_weibo import COOKIE, PLATFORM, BASE_DIR, RESULT_DIR

RESULT_FILE = "weibo_comments_replies.csv"
MID_DIR = os.path.join(BASE_DIR, "采集中间文件")
SRC_FILE = os.path.join(RESULT_DIR, "weibo_comments.csv")              # 已采微博正文
OUT_FILE = os.path.join(RESULT_DIR, RESULT_FILE)                       # 评论输出（含时间）
BACKUP_FILE = os.path.join(MID_DIR, "weibo_comments_replies_无时间备份.csv")
CHECKPOINT = os.path.join(MID_DIR, "weibo_cmt_time_checkpoint.json")   # 独立断点
COOKIE_JSON = os.path.join(MID_DIR, "_weibo_login_cookies.json")       # 登录态

WORKERS = 3
WAIT_SEC = 6            # 每条微博页面等待秒数（评论渲染）
MAX_SCROLLS = 40
STABLE_ROUNDS = 3
SKIP_COUNT = 10
PAUSE_SEC = 20
LOGIN_WAIT = 180        # 登录等待上限（秒）

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148")

FIELDS = ["platform", "weibo_id", "user", "content", "level", "created_at"]

# 评论提取JS：正文/用户名/子评论同原版，新增取该评论卡片上的时间
# 实测 m.weibo.cn 详情页结构：时间在卡片底部 <div class="lite-bot"><div class="... time">2023-9-28 来自 陕西</div>
# 而原版 block 停在含 h4 的 m-text-box（时间在其外层兄弟），故需向上找到同时含 h4 与 .time 的祖先
EXTRACT_JS = r"""
() => {
  function pickTime(block) {
    if (!block) return '';
    const cand = block.querySelector('.time, .ct, .comment-time');
    if (cand) {
      const t = (cand.textContent || '').trim();
      if (t) return t;
    }
    const RE = /(刚刚|今天|昨天|前天|\d+\s*(秒|分钟|小时|天)前|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2})/;
    const nodes = block.querySelectorAll('span, div, a, p');
    for (let i = 0; i < nodes.length; i++) {
      const t = (nodes[i].textContent || '').trim();
      if (t && t.length <= 24 && RE.test(t)) return t;
    }
    return '';
  }
  const out = [];
  document.querySelectorAll('.comment-content h3').forEach(h3 => {
    // 向上找最近的“同时含用户名 h4 与时间 .time”的祖先：落在单条评论卡片内，不会跨到下一条
    let block = null, cur = h3;
    for (let i = 0; i < 6 && cur; i++) {
      cur = cur.parentElement;
      if (!cur) break;
      if (cur.querySelector('h4') && cur.querySelector('.time')) { block = cur; break; }
    }
    if (!block) {
      // 兜底：退回原版口径（含 h4 的最近祖先）
      cur = h3;
      for (let i = 0; i < 4 && cur; i++) {
        if (cur.querySelector('h4')) { block = cur; break; }
        cur = cur.parentElement;
      }
      if (!block) block = h3.parentElement;
    }
    let user = '';
    const h4 = block ? block.querySelector('h4') : null;
    if (h4) user = h4.textContent.trim();
    const content = (h3.textContent || '').trim();
    const t = pickTime(block);
    const subs = [];
    if (block) {
      const subBox = block.querySelector('.cmt-sub-txt, .sub-txt');
      if (subBox) {
        subBox.querySelectorAll('p').forEach(p => {
          const s = p.textContent.trim();
          if (s) subs.push(s);
        });
      }
    }
    if (content) out.push({user: user, content: content, subs: subs, time: t});
  });
  return out;
}
"""


# ---------------- 时间归一化 ----------------
def norm_time(raw, now=None):
    """页面时间文本 -> 'YYYY-MM-DD HH:MM'。支持绝对日期、省略年份的月日、相对时间。"""
    s = (raw or "").strip()
    if not s:
        return ""
    now = now or datetime.datetime.now()

    # 1) 完整日期：2023-09-15 14:30 / 2023-09-15
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ ]+(\d{1,2}):(\d{2}))?", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4)) if m.group(4) else 0
        mi = int(m.group(5)) if m.group(5) else 0
        if 1 <= mo <= 12 and 1 <= d <= 31 and 0 <= hh <= 23 and 0 <= mi <= 59:
            return "%04d-%02d-%02d %02d:%02d" % (y, mo, d, hh, mi)

    # 2) 省略年份的月日：09-15 14:30 / 09-15（默认当年，晚于今天则算去年）
    m = re.search(r"(\d{1,2})-(\d{1,2})(?:[ ]+(\d{1,2}):(\d{2}))?", s)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        hh = int(m.group(3)) if m.group(3) else 0
        mi = int(m.group(4)) if m.group(4) else 0
        if 1 <= mo <= 12 and 1 <= d <= 31:
            y = now.year
            try:
                cand = datetime.datetime(y, mo, d, hh, mi)
            except ValueError:
                return ""
            if cand.date() > now.date():
                try:
                    cand = datetime.datetime(y - 1, mo, d, hh, mi)
                except ValueError:
                    return ""
            return cand.strftime("%Y-%m-%d %H:%M")

    # 3) 相对时间：刚刚 / N秒前 / N分钟前 / N小时前 / N天前
    if "刚刚" in s:
        return now.strftime("%Y-%m-%d %H:%M")
    m = re.search(r"(\d+)\s*(秒|分钟|小时|天)前", s)
    if m:
        unit = {"秒": 1, "分钟": 60, "小时": 3600, "天": 86400}[m.group(2)]
        dt = now - datetime.timedelta(seconds=int(m.group(1)) * unit)
        return dt.strftime("%Y-%m-%d %H:%M")

    # 4) 今天 / 昨天 / 前天 [14:30]
    m = re.search(r"(今天|昨天|前天)(?:[ ]+(\d{1,2}):(\d{2}))?", s)
    if m:
        offset = {"今天": 0, "昨天": 1, "前天": 2}[m.group(1)]
        hh = int(m.group(2)) if m.group(2) else 0
        mi = int(m.group(3)) if m.group(3) else 0
        dt = now - datetime.timedelta(days=offset)
        return dt.replace(hour=hh, minute=mi, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")

    return ""


# ---------------- 登录态 ----------------
def _cookie_list():
    """优先用已保存的登录态，其次用 collect_weibo.COOKIE"""
    if os.path.exists(COOKIE_JSON):
        try:
            saved = json.load(open(COOKIE_JSON, encoding="utf-8")).get("cookies") or []
            if saved:
                return saved
        except Exception:
            pass
    cks = []
    for kv in COOKIE.split(";"):
        if "=" in kv:
            k, v = kv.strip().split("=", 1)
            cks.append({"name": k, "value": v, "domain": ".weibo.cn", "path": "/"})
    return cks


def ensure_login():
    """预检登录态：无效时打开可见窗口等扫码登录，成功后保存 Cookie。返回 cookie 列表。"""
    from playwright.sync_api import sync_playwright
    cks = _cookie_list()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False,
                                     args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, viewport={"width": 420, "height": 900},
                                 is_mobile=True)
        try:
            ctx.add_cookies(cks)
        except Exception:
            pass
        page = ctx.new_page()
        page.goto("https://m.weibo.cn", wait_until="domcontentloaded", timeout=30000)

        def logged_in():
            try:
                txt = page.evaluate("""async () => {
                    const r = await fetch('https://m.weibo.cn/api/config', {credentials: 'include'});
                    return await r.text();
                }""")
                return bool(json.loads(txt).get("data", {}).get("login"))
            except Exception:
                return False

        if not logged_in():
            print("[登录] 微博登录态无效，请在打开的窗口中扫码登录（最多等待 %d 秒）…" % LOGIN_WAIT)
            waited = 0
            while waited < LOGIN_WAIT:
                page.wait_for_timeout(3000)
                waited += 3
                if logged_in():
                    print("[登录] 登录成功")
                    break
            else:
                browser.close()
                print("[登录] 未检测到登录，已停止。请重跑本脚本并在窗口内完成登录。")
                raise SystemExit(1)

        cookies = ctx.cookies()
        os.makedirs(MID_DIR, exist_ok=True)
        json.dump({"cookies": cookies}, open(COOKIE_JSON, "w", encoding="utf-8"),
                  ensure_ascii=False)
        print("[登录态] 已保存到 %s" % COOKIE_JSON)
        browser.close()
        return cookies


# ---------------- 单条微博采集 ----------------
def collect_one(browser, weibo_id, cookies):
    """打开一条微博详情页，提取评论（含时间）。返回(评论行列表, 状态)。"""
    ctx = browser.new_context(user_agent=UA, viewport={"width": 420, "height": 900},
                              is_mobile=True)
    cks = [c for c in cookies if str(c.get("domain", "")).find("weibo.cn") >= 0] or cookies
    try:
        ctx.add_cookies(cks)
    except Exception:
        pass
    page = ctx.new_page()
    rows = []
    status = "OK"
    try:
        page.goto("https://m.weibo.cn/detail/" + str(weibo_id),
                  wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(WAIT_SEC * 1000)
        count_js = "() => document.querySelectorAll('.comment-content h3').length"
        prev, stable = -1, 0
        for _ in range(MAX_SCROLLS):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(1600)
            try:
                n = page.evaluate(count_js)
            except Exception:
                n = prev
            if n == prev:
                stable += 1
                if stable >= STABLE_ROUNDS:
                    break
            else:
                stable = 0
            prev = n
        data = page.evaluate(EXTRACT_JS)
        now = datetime.datetime.now()
        for c in data:
            t = norm_time(c.get("time", ""), now)
            rows.append({"platform": PLATFORM, "weibo_id": str(weibo_id),
                         "user": c.get("user", ""), "content": c.get("content", ""),
                         "level": "main", "created_at": t})
            for s in (c.get("subs") or []):
                rows.append({"platform": PLATFORM, "weibo_id": str(weibo_id),
                             "user": "", "content": s, "level": "sub",
                             "created_at": ""})      #子评论页面无独立时间，留空不猜
        if not data:
            title = page.title()
            if "不存在" in title or "出错" in title:
                status = "GONE"
    except Exception as e:
        status = "ERR:" + str(e)[:60]
    finally:
        try:
            ctx.close()
        except Exception:
            pass
    return rows, status


def backfill_sub(out_rows):
    """楼中楼回填：新版详情页已不再渲染子评论，按微博从无时间版备份补回，created_at 留空。
    只对“本次已采到主评论”的微博回填，避免出现只有子评论没有主评论的孤立数据。"""
    if not os.path.exists(BACKUP_FILE):
        return out_rows, 0
    have_main = set()
    have_sub = set()
    for r in out_rows:
        if r.get("level") == "main":
            have_main.add(r.get("weibo_id"))
        elif r.get("level") == "sub":
            have_sub.add(r.get("weibo_id"))
    old_subs = {}
    try:
        with open(BACKUP_FILE, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row.get("level") == "sub":
                    old_subs.setdefault(row.get("weibo_id"), []).append(row)
    except Exception:
        return out_rows, 0
    added = 0
    for wid, subs in old_subs.items():
        if wid in have_sub or wid not in have_main:
            continue
        for row in subs:
            out_rows.append({
                "platform": row.get("platform", PLATFORM),
                "weibo_id": wid,
                "user": row.get("user", ""),
                "content": row.get("content", ""),
                "level": "sub",
                "created_at": "",
            })
            added += 1
    return out_rows, added


def _save(out_rows, done):
    with open(OUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows([{k: r.get(k, "") for k in FIELDS} for r in out_rows])
    json.dump({"done": sorted(done)}, open(CHECKPOINT, "w", encoding="utf-8"))


def main():
    os.makedirs(MID_DIR, exist_ok=True)

    # 备份无时间版结果（只备份一次）
    if os.path.exists(OUT_FILE) and not os.path.exists(BACKUP_FILE):
        shutil.copy2(OUT_FILE, BACKUP_FILE)
        print("已备份无时间版结果：%s" % BACKUP_FILE)

    cookies = ensure_login()

    with open(SRC_FILE, newline="", encoding="utf-8-sig") as f:
        weibos = list(csv.DictReader(f))
    print("已采微博总数:", len(weibos))

    tasks = []
    for w in weibos:
        try:
            cc = int(w.get("comments_count") or 0)
        except Exception:
            cc = 0
        wid = str(w.get("weibo_id") or w.get("id") or "")
        if cc > 0 and wid:
            tasks.append(wid)
    tasks = list(dict.fromkeys(tasks))
    print("有评论的微博:", len(tasks))

    done = set()
    if os.path.exists(CHECKPOINT):
        try:
            done = set(json.load(open(CHECKPOINT, encoding="utf-8")).get("done", []))
            print("断点恢复已完成:", len(done))
        except Exception:
            pass
    todo = [t for t in tasks if t not in done]

    lim = int(os.environ.get("WEIBO_CMT_LIMIT", "0") or 0)
    if lim > 0:
        todo = todo[:lim]
    print("本次待采:", len(todo))

    lock = threading.Lock()
    stats = {"rows": 0, "timed": 0, "gone": 0, "err": 0, "n": 0, "bad": 0}

    out_rows = []
    if os.path.exists(OUT_FILE):
        # 只有已是“带时间”版（6列）时才续采；旧5列文件不读入，避免旧数据与新数据重复
        try:
            with open(OUT_FILE, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames and "created_at" in reader.fieldnames:
                    out_rows = list(reader)
        except Exception:
            out_rows = []

    from playwright.sync_api import sync_playwright

    q = Queue()
    for wid in todo:
        q.put(wid)
    for _ in range(WORKERS):
        q.put(None)

    processed_total = len(todo)

    def worker_loop():
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True,
                                         args=["--disable-blink-features=AutomationControlled"])
            try:
                while True:
                    wid = q.get()
                    if wid is None:
                        break
                    try:
                        rows, st = collect_one(browser, wid, cookies)
                    except Exception:
                        rows, st = [], "ERR"
                    time.sleep(random.uniform(0.3, 1.0))
                    with lock:
                        if rows:
                            out_rows.extend(rows)
                            stats["rows"] += len(rows)
                            stats["timed"] += sum(1 for r in rows if r.get("created_at"))
                        if st == "GONE":
                            stats["gone"] += 1
                        elif st.startswith("ERR"):
                            stats["err"] += 1
                        done.add(wid)
                        stats["n"] += 1
                        n = stats["n"]
                        if n % 60 == 0:
                            try:
                                _save(out_rows, done)
                            except Exception:
                                pass
                        print("  进度：%d/%d 评论%d条(含时间%d) | 不存在%d 错误%d" % (
                            n, processed_total, stats["rows"], stats["timed"],
                            stats["gone"], stats["err"]))
                    if st in ("GONE",) or st.startswith("ERR"):
                        with lock:
                            stats["bad"] += 1
                            b = stats["bad"]
                        if b >= SKIP_COUNT:
                            print("  连续失败过多，暂停%d秒…" % PAUSE_SEC)
                            time.sleep(PAUSE_SEC)
                            with lock:
                                stats["bad"] = 0
                    else:
                        with lock:
                            stats["bad"] = 0
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

    threads = [threading.Thread(target=worker_loop) for _ in range(WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    out_rows, sub_added = backfill_sub(out_rows)
    _save(out_rows, done)
    total = len(out_rows)
    timed = sum(1 for r in out_rows if r.get("created_at"))
    print("完成：共 %d 条评论（%d 条微博），其中带时间 %d 条（%.1f%%），不存在 %d，错误 %d" % (
        total, len(done), timed, 100.0 * timed / total if total else 0,
        stats["gone"], stats["err"]))
    print("楼中楼回填（旧版无时间）: %d 条" % sub_added)
    print("结果文件：%s" % OUT_FILE)


if __name__ == "__main__":
    main()
