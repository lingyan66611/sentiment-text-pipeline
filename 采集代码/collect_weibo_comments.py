# -*- coding: utf-8 -*-
"""
微博评论补采脚本（浏览器版）
主题：预制菜进校园热点事件网络文本

背景：微博正文已采集完成（result/weibo_comments.csv，14807条）。
     微博移动端评论接口已改版（API失效），评论由前端渲染在详情页DOM中，
     因此用 Playwright 打开 m.weibo.cn/detail/{微博id}，滚动加载后从DOM提取评论。

输出：result/weibo_comments_replies.csv（一行=一条评论，含主评论与楼中楼子评论）
策略：
  - 只采 comments_count>0 的微博（约4700条），跳过无评论的
  - 3个浏览器并行，缩短总耗时
  - 断点续采：已采微博id存入 checkpoint，中断后重跑自动跳过
  - 已删除/不可见微博自动跳过并计数

运行：python collect_weibo_comments.py
"""
import os
import csv
import json
import time
import random
import threading

from collect_weibo import COOKIE, PLATFORM, BASE_DIR, RESULT_DIR

RESULT_FILE = "weibo_comments_replies.csv"
SRC_FILE = os.path.join(RESULT_DIR, "weibo_comments.csv")          # 已采微博正文
OUT_FILE = os.path.join(RESULT_DIR, RESULT_FILE)                   # 评论输出
CHECKPOINT = os.path.join(BASE_DIR, "weibo_cmt_checkpoint.json")   # 断点

WORKERS = 3           # 并行浏览器数
WAIT_SEC = 6          # 每条微博页面等待秒数（评论渲染）
MAX_SCROLLS = 40      # 每条微博最多滚动次数（保护极端大流量微博）
STABLE_ROUNDS = 3     # 连续N次滚动无新增评论则视为到底
SKIP_COUNT = 10       # 连续失败（不可见）达到该数时全局暂停几秒（防风控）
PAUSE_SEC = 20

FIELDS = ["platform", "weibo_id", "user", "content", "level"]

#评论提取JS：.comment-content 下每条主评论=一个h3，向上找用户名h4，向下找子评论.cmt-sub-txt
EXTRACT_JS = """
() => {
  const out = [];
  document.querySelectorAll('.comment-content h3').forEach(h3 => {
    let block = h3;
    for (let i = 0; i < 4 && block; i++) {
      if (block.querySelector('h4')) break;
      block = block.parentElement;
    }
    let user = '';
    if (block) {
      const h4 = block.querySelector('h4');
      if (h4) user = h4.textContent.trim();
    }
    const content = (h3.textContent || '').trim();
    const subs = [];
    if (block) {
      const subBox = block.querySelector('.cmt-sub-txt');
      if (subBox) {
        subBox.querySelectorAll('p').forEach(p => {
          const s = p.textContent.trim();
          if (s) subs.push(s);
        });
      }
    }
    if (content) out.push({user, content, subs});
  });
  return out;
}
"""


def collect_one(browser, weibo_id):
    """打开一条微博详情页并提取评论。返回(评论行列表, 状态)。"""
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
        viewport={"width": 420, "height": 900}, is_mobile=True)
    cks = []
    for kv in COOKIE.split(";"):
        if "=" in kv:
            k, v = kv.strip().split("=", 1)
            cks.append({"name": k, "value": v, "domain": ".weibo.cn", "path": "/"})
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
        #滚动触发评论懒加载：持续滚动直到连续多轮无新增（摘全评论）
        count_js = "() => document.querySelectorAll('.comment-content h3').length"
        prev = -1
        stable = 0
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
        for c in data:
            rows.append({"platform": PLATFORM, "weibo_id": str(weibo_id),
                         "user": c.get("user", ""), "content": c.get("content", ""),
                         "level": "main"})
            for s in (c.get("subs") or []):
                rows.append({"platform": PLATFORM, "weibo_id": str(weibo_id),
                             "user": "", "content": s, "level": "sub"})
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


def main():
    #读微博正文CSV
    with open(SRC_FILE, newline="", encoding="utf-8-sig") as f:
        weibos = list(csv.DictReader(f))
    print("已采微博总数:", len(weibos))

    #按评论数过滤
    tasks = []
    for w in weibos:
        try:
            cc = int(w.get("comments_count") or 0)
        except Exception:
            cc = 0
        wid = str(w.get("weibo_id") or w.get("id") or "")
        if cc > 0 and wid:
            tasks.append(wid)
    tasks = list(dict.fromkeys(tasks))     #去重保序
    print("有评论的微博:", len(tasks))

    #断点恢复
    done = set()
    if os.path.exists(CHECKPOINT):
        try:
            done = set(json.load(open(CHECKPOINT, encoding="utf-8")).get("done", []))
            print("断点恢复已采评论微博:", len(done))
        except Exception:
            pass
    todo = [t for t in tasks if t not in done]
    #可选：限制本次采集条数（环境变量WEIBO_CMT_LIMIT，便于小规模试跑）
    lim = int(os.environ.get("WEIBO_CMT_LIMIT", "0") or 0)
    if lim > 0:
        todo = todo[:lim]
    print("本次待采:", len(todo))

    lock = threading.Lock()
    stats = {"rows": 0, "gone": 0, "err": 0, "n": 0, "bad": 0}

    #续写已有结果
    out_rows = []
    if os.path.exists(OUT_FILE):
        try:
            with open(OUT_FILE, newline="", encoding="utf-8-sig") as f:
                out_rows = list(csv.DictReader(f))
        except Exception:
            pass

    from playwright.sync_api import sync_playwright
    from queue import Queue

    #多浏览器并行：队列分发，每个worker线程循环取任务直到全部完成
    q = Queue()
    for wid in todo:
        q.put(wid)
    for _ in range(WORKERS):
        q.put(None)                       #哨兵：线程结束信号

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
                        rows, st = collect_one(browser, wid)
                    except Exception:
                        rows, st = [], "ERR"
                    time.sleep(random.uniform(0.3, 1.0))
                    with lock:
                        if rows:
                            out_rows.extend(rows)
                            stats["rows"] += len(rows)
                        if st == "GONE":
                            stats["gone"] += 1
                        elif st.startswith("ERR"):
                            stats["err"] += 1
                        done.add(wid)
                        stats["n"] += 1
                        n = stats["n"]
                        #每处理60条落盘一次
                        if n % 60 == 0:
                            try:
                                _save(out_rows, done)
                            except Exception:
                                pass
                        print("  进度：%d/%d 评论%d条 | 不存在%d 错误%d" % (
                            n, processed_total, stats["rows"], stats["gone"], stats["err"]))
                    #连续失败过多 -> 暂停防风控（只在出现失败时计）
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

    #最终落盘
    _save(out_rows, done)
    print("评论补采完成：共 %d 条评论（%d 条微博），不存在 %d，错误 %d" % (
        stats["rows"], len(done), stats["gone"], stats["err"]))
    print("结果文件：%s" % OUT_FILE)


def _save(out_rows, done):
    with open(OUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows([{k: r.get(k, "") for k in FIELDS} for r in out_rows])
    json.dump({"done": sorted(done)}, open(CHECKPOINT, "w", encoding="utf-8"))


if __name__ == "__main__":
    main()
