# -*- coding: utf-8 -*-
"""
小红书（搜索）自适应采集 —— Playwright 浏览器版（最新技术方案）
主题：预制菜进校园热点事件网络文本

采集内容：笔记（标题+正文+发布时间+互动数）与每条笔记的评论（用户+内容+时间+点赞）

原理：由真实浏览器打开小红书搜索页，前端自动完成登录会话/搜索预热/x-s签名，
     脚本拦截 search/notes 接口响应获取笔记列表；再带 xsec_token 打开笔记详情页，
     从页面 HTML 提取真实发布时间、从正文节点提取全文，并拦截 comment/page 接口
     采集评论 —— 与浏览器手动操作完全一致，不受签名算法更新/Cookie时效影响。

使用流程：
  1) 首次运行：弹出浏览器窗口 → 扫码/手机号登录小红书 → 登录后自动开始采集
  2) 之后运行：自动复用已保存登录态，无需再次扫码（失效时窗口会提示重登）
  3) 依赖：pip install playwright   （浏览器内核已装）
  4) 运行：python collect_xiaohongshu.py

采集阶段不筛选；主动学习自动扩展关键词并写入词库 Word（_work_词库记录）。
"""
import os
import re
import csv
import json
import time
import random
import datetime
from urllib.parse import quote

import bootstrapper as boot

#路径
PLATFORM = "xiaohongshu"
RESULT_DIR = r"E:\learn\自然语言处理\大作业\result"
BASE_DIR = r"E:\learn\自然语言处理\大作业"
RESULT_FILE = "xiaohongshu_comments.csv"
LOGIN_COOKIE_FILE = os.path.join(BASE_DIR, "_xhs_login_cookies.json")  # 登录态自动保存/复用

#采集参数
MAX_PAGES = 4               # 列表页最多滚动翻页次数（每页约20条 → 每词最多约80条）
MAX_NOTES = 45              # 每词最多进入详情页的笔记数
MAX_COMMENTS = 500          # 每条笔记评论量程上限（保护极端爆款笔记）
PAGE_WAIT = 3.5             # 列表滚动等待（随机+抖动）
DETAIL_WAIT = 3.0           # 详情页基础等待
LOGIN_TIMEOUT = 180         # 等待扫码登录的最长秒数

#断点/日志/候选词（过程文件在根目录）
CHECKPOINT = os.path.join(BASE_DIR, "xiaohongshu_checkpoint.json")
LOG_FILE = os.path.join(BASE_DIR, "xiaohongshu_collect_log.txt")
CANDIDATE = os.path.join(BASE_DIR, "xiaohongshu_candidate_keywords.txt")

FIELDS = ["platform", "keyword", "id", "note_id", "content_type", "user",
          "content", "create_time", "like_count", "reply_count"]

_BODY_SELECTORS = [".note-text", ".note-content", "#detail-desc",
                   ".desc", ".content", "article .text"]


#---------------- 主题审核（主动学习新增词的质量控制） -----------------
#新词必须与"预制菜进校园"主题相关才允许进入词库；无关词（如"一周/不用/到底"）自动剔除
THEME_ROOTS = [
    "预制菜", "预制", "进校园", "校园", "学校", "学生", "食堂", "家长",
    "午餐", "配餐", "供餐", "食品", "营养", "料理包", "中央厨房", "明厨亮灶",
    "厨房", "卫生", "冷链", "食材", "餐", "饭", "菜", "监管", "安全",
    "健康", "添加剂", "微波", "保温", "加热", "抵制", "反对", "支持",
    "争议", "热议", "校长", "老师", "孩子", "外卖", "供应商", "招标",
    "中标", "舆情", "风波", "舆论", "315", "校园餐", "校外供餐", "学生餐",
    "网友", "投诉", "曝光", "食堂餐", "配餐公司",
]


def theme_audit(kw):
    """审核一个候选关键词是否与'预制菜进校园'主题相关。
    命中任一主题词素即通过；纯泛化词（一周/不用/到底/好吃…）拒绝。"""
    kw = str(kw or "")
    return any(r in kw for r in THEME_ROOTS)


#---------------- 浏览器管理 -----------------
_browser = None
_context = None
_page = None
_playwright = None


def _browser_dead():
    """检测浏览器/页面是否已失效（崩溃、被关闭等）"""
    global _browser, _page
    try:
        if _browser is None or _page is None:
            return True
        if _page.is_closed():
            return True
        if not _browser.is_connected():
            return True
        return False
    except Exception:
        return True


def _restart_browser():
    """保存登录态并彻底关闭当前浏览器（崩溃自愈/每词重启防内存累积）"""
    global _browser, _context, _page
    try:
        _save_login_state()
    except Exception:
        pass
    try:
        if _browser is not None:
            _browser.close()
    except Exception:
        pass
    _browser = None
    _context = None
    _page = None


def _ensure_browser():
    """启动 Playwright 浏览器（有头，用户可见），加载历史登录态；已死则自动重启"""
    global _browser, _context, _page, _playwright
    if not _browser_dead():
        return
    #清理残留实例
    try:
        if _browser is not None:
            _browser.close()
    except Exception:
        pass
    _browser = None
    _context = None
    _page = None
    if _playwright is None:
        from playwright.sync_api import sync_playwright
        _playwright = sync_playwright().start()   # 全局只启动一次驱动，浏览器可反复重启
    _browser = _playwright.chromium.launch(
        headless=False, args=["--disable-blink-features=AutomationControlled"])
    _context = _browser.new_context(locale="zh-CN")
    if os.path.exists(LOGIN_COOKIE_FILE):
        try:
            _context.add_cookies(json.load(open(LOGIN_COOKIE_FILE, encoding="utf-8")))
            print("  [登录态] 已加载历史登录态")
        except Exception:
            print("  [登录态] 历史登录态加载失败，将重新登录")
    _page = _context.new_page()


def _save_login_state():
    """保存当前浏览器登录态（下次运行复用）"""
    global _context
    if _context is None:
        return
    try:
        json.dump(_context.cookies(), open(LOGIN_COOKIE_FILE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except Exception as e:
        print("  [登录态] 保存失败:", str(e)[:80])


def _wait_for_manual(page, keyword, hit_holder):
    """未登录或会话失效时：提示用户在浏览器窗口手动登录/过验证码，等待数据出现"""
    text = page.evaluate("document.body.innerText.slice(0,400)")
    if "登录后查看" in text or "扫码" in text:
        print("  [%s] 请在弹出的浏览器窗口中扫码/手机号登录…" % keyword)
        try:
            page.click("text=登录", timeout=4000)
        except Exception:
            pass
        page.wait_for_timeout(3000)
    else:
        print("  [%s] 页面无数据（可能触发验证码/需要登录），请在浏览器窗口中手动操作…" % keyword)
    deadline = time.time() + LOGIN_TIMEOUT
    while time.time() < deadline:
        page.wait_for_timeout(4000)
        try:
            page.mouse.wheel(0, 2500)
        except Exception:
            pass
        if hit_holder["count"] > 0:
            print("  [%s] 登录/验证通过，数据开始到达 ✓" % keyword)
            return True
    print("  [%s] 等待超时（%d秒），请重新运行" % (keyword, LOGIN_TIMEOUT))
    return False


#---------------- 解析辅助 -----------------
def _fmt_ts(ts):
    """unix秒（或毫秒）时间戳 → 'YYYY-MM-DD HH:MM:SS'（北京时间）；失败返回空串"""
    try:
        v = int(ts)
        if v > 10 ** 12:      # 毫秒
            v = v // 1000
        return datetime.datetime.fromtimestamp(
            v, datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _norm_count(v):
    """互动数归一化：'3.4万'→'34000'；纯数字原样返回字符串"""
    if isinstance(v, (int, float)):
        return str(int(v))
    s = str(v or "").strip()
    if not s:
        return "0"
    if "万" in s:
        try:
            return str(int(float(s.replace("万", "")) * 10000))
        except Exception:
            return s
    return s


def _extract_note_time(html):
    """详情页HTML → 笔记真实发布时间（秒级时间戳→格式化）"""
    m = re.search(r'"time"\s*:\s*"?(\d{10})"?', html)
    if m:
        return _fmt_ts(m.group(1))
    return ""


def _extract_body(page, title):
    """详情页DOM → 笔记正文全文；失败回退 og:description"""
    try:
        for sel in _BODY_SELECTORS:
            try:
                t = page.evaluate(
                    "(() => { const el = document.querySelector('%s'); return el ? el.innerText.trim() : ''; })()" % sel)
                if t and len(t) > 10:
                    if title and not t.startswith(title):
                        return title + "\n" + t
                    return t
            except Exception:
                continue
        # 回退：og:description
        html = page.content()
        m = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', html)
        if m and m.group(1):
            return (title + "\n" if title else "") + m.group(1)
    except Exception:
        pass
    return title or ""


#---------------- 采集 -----------------
def _is_note_id(nid):
    """小红书正常笔记ID为24位雪花ID（含时间戳信息）；广告/UUID等异常ID返回False"""
    return bool(nid) and 20 <= len(nid) <= 26 and nid.isalnum()


def fetcher(keyword):
    """小红书采集：列表页拦截笔记 → 详情页正文/时间 → 评论"""
    _ensure_browser()
    page = _page
    list_notes = []        # 列表笔记 (id, token, title, interact, user)
    holder = {"count": 0}

    def on_resp(resp):
        u = resp.url
        if "search/notes" in u:
            try:
                j = resp.json()
                items = ((j.get("data") or {}).get("items") or []) if j.get("code") == 0 else []
                if items:
                    for it in items:
                        nc = it.get("note_card") or it
                        if isinstance(nc, dict):
                            nid = str(it.get("id") or nc.get("noteId") or "")
                            if _is_note_id(nid):
                                list_notes.append(it)
                    holder["count"] += len(items)
            except Exception:
                pass
    page.on("response", on_resp)

    # 1) 列表页（浏览器崩溃则重启重试一次）
    url = "https://www.xiaohongshu.com/search_result?keyword=" + quote(keyword)
    for attempt in range(2):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            break
        except Exception as e:
            print("  [%s] 打开搜索页失败: %s" % (keyword, str(e)[:100]))
            if _browser_dead() and attempt == 0:
                _restart_browser()
                _ensure_browser()
                page = _page
                continue
            page.remove_listener("response", on_resp)
            return []
    page.wait_for_timeout(9000)
    page.mouse.wheel(0, 2500)
    page.wait_for_timeout(4000)
    if holder["count"] == 0:
        if not _wait_for_manual(page, keyword, holder):
            page.remove_listener("response", on_resp)
            return []
    for _ in range(1, MAX_PAGES):
        before = len(list_notes)
        try:
            page.mouse.wheel(0, 3000)
        except Exception:
            break
        page.wait_for_timeout(int((PAGE_WAIT + random.uniform(0, 2)) * 1000))
        if len(list_notes) == before:
            try:
                page.mouse.wheel(0, 3000)
            except Exception:
                break
            page.wait_for_timeout(int((PAGE_WAIT + random.uniform(0, 2)) * 1000))
    page.remove_listener("response", on_resp)
    print("  [%s] 列表采到 %d 条笔记，进入详情采集…" % (keyword, len(list_notes)))

    # 2) 详情页 + 评论（崩溃自动重启重试；每词采完重启浏览器防内存累积）
    rows = []
    for it in list_notes[:MAX_NOTES]:
        nc = it.get("note_card") or it
        nid = str(it.get("id") or nc.get("noteId") or "")
        token = it.get("xsec_token") or ""
        if not _is_note_id(nid):
            continue
        interact = nc.get("interact_info") or {}
        title = (nc.get("display_title") or nc.get("displayTitle") or "")[:120]
        user = ((nc.get("user") or {}).get("nickname") or "")
        note_rows = None
        for attempt in range(2):      # 最多重试1次（浏览器崩溃后重启再采）
            if _browser_dead():
                _restart_browser()
                _ensure_browser()
                page = _page
            note_rows = _collect_one(page, keyword, nid, token, title, user, interact)
            if note_rows is not None:
                break
            print("    [%s] 浏览器已重启，重试…" % nid[:10])
        if note_rows:
            rows.extend(note_rows)
        time.sleep(random.uniform(0.5, 1.5))
    _restart_browser()      # 每词结束重启：释放内存，避免长时间运行崩溃
    print("  [%s] 详情采集完成：%d 条文本（笔记+评论）" % (keyword, len(rows)))
    return rows


def _collect_one(page, keyword, nid, token, title, user, interact):
    """单条笔记：详情页正文/时间 + 评论，返回该笔记及其评论的行"""
    url = "https://www.xiaohongshu.com/explore/" + nid
    if token:
        url += "?xsec_token=" + token + "&xsec_source=pc_search"
    comments = []
    got = {"n": 0}
    seen_cursor = set()

    def on_c(resp):
        if "comment/page" in resp.url or "comment/list" in resp.url:
            try:
                j = resp.json()
                if j.get("code") == 0:
                    cs = (j.get("data") or {}).get("comments") or []
                    comments.append(j)
                    got["n"] += len(cs)
            except Exception:
                pass
    page.on("response", on_c)

    body = title
    create_time = ""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(int(DETAIL_WAIT * 1000))
        body = _extract_body(page, title)
        html = page.content()
        create_time = _extract_note_time(html)
        # 滚动触发评论加载：动态翻页到底（最多40轮，连续3轮无新增即到底）
        stable = 0
        for _ in range(40):
            if got["n"] >= MAX_COMMENTS:
                break
            before = got["n"]
            try:
                page.mouse.wheel(0, 3000)
            except Exception:
                break
            page.wait_for_timeout(int((DETAIL_WAIT + random.uniform(0, 1.5)) * 1000))
            if got["n"] == before:
                stable += 1
                if stable >= 3:
                    break   # 评论不再增长（已到底），停止滚动
            else:
                stable = 0
    except Exception as e:
        print("    [%s] 详情页失败: %s" % (nid[:10], str(e)[:80]))
        if _browser_dead():
            return None     # 浏览器崩溃：整体失败，交由上层重启重试
    try:
        page.remove_listener("response", on_c)
    except Exception:
        pass

    note_likes = _norm_count(interact.get("liked_count") or interact.get("likedCount") or 0)
    note_replies = _norm_count(interact.get("comment_count") or interact.get("commentCount") or 0)

    # 笔记行
    rows = [{
        "platform": PLATFORM, "keyword": keyword, "id": "note_" + nid,
        "note_id": nid, "content_type": "note", "user": user,
        "content": body or title, "create_time": create_time,
        "like_count": note_likes, "reply_count": note_replies,
    }]
    # 评论行（含楼中楼子评论）：全部响应包去重合并
    seen_cmt = set()
    for j in comments:
        for c in (j.get("data") or {}).get("comments") or []:
            cid = str(c.get("id") or "")
            if cid in seen_cmt:
                continue
            seen_cmt.add(cid)
            rows.append({
                "platform": PLATFORM, "keyword": keyword, "id": "cmt_" + cid,
                "note_id": nid, "content_type": "comment", "user": ((c.get("user_info") or {}).get("nickname") or ""),
                "content": c.get("content") or "", "create_time": _fmt_ts(c.get("create_time")),
                "like_count": _norm_count(c.get("like_count") or 0),
                "reply_count": _norm_count(c.get("sub_comment_count") or 0),
            })
            # 子评论（楼中楼）：主评论自带的前几条
            for sc in (c.get("sub_comments") or [])[:10]:
                scid = str(sc.get("id") or "")
                if scid in seen_cmt:
                    continue
                seen_cmt.add(scid)
                rows.append({
                    "platform": PLATFORM, "keyword": keyword, "id": "scmt_" + scid,
                    "note_id": nid, "content_type": "comment", "user": ((sc.get("user_info") or {}).get("nickname") or ""),
                    "content": sc.get("content") or "", "create_time": _fmt_ts(sc.get("create_time")),
                    "like_count": _norm_count(sc.get("like_count") or 0),
                    "reply_count": "0",
                })
    return rows


#---------------- 主流程 -----------------
def main():
    os.makedirs(RESULT_DIR, exist_ok=True)
    print("=" * 50)
    print("小红书采集（浏览器版）：笔记全文 + 评论")
    print("首次运行请在弹出的浏览器窗口中登录小红书；登录态将自动保存复用")
    print("=" * 50)
    try:
        rows, final_keywords, history = boot.adaptive_collect(
            PLATFORM, fetcher, workers=1,
            seed_keywords=boot.SEED_KEYWORDS, exclude_words=boot.EXCLUDE_WORDS,
            checkpoint_path=CHECKPOINT, log_file=LOG_FILE, candidate_file=CANDIDATE,
            docx_log_path=os.path.join(BASE_DIR, "_work_词库记录", "词库演变记录_自动.docx"),
            min_hits=1, auto_add=True, use_classifier=True, word_audit=theme_audit)
    finally:
        _save_login_state()
        try:
            if _browser is not None:
                _browser.close()
        except Exception:
            pass
        try:
            if _playwright is not None:
                _playwright.stop()
        except Exception:
            pass

    out_path = os.path.join(RESULT_DIR, RESULT_FILE)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows([{k: r.get(k, "") for k in FIELDS} for r in rows])
    notes = sum(1 for r in rows if r.get("content_type") == "note")
    cmts = sum(1 for r in rows if r.get("content_type") == "comment")
    print("采集完成：共 %d 条文本（笔记 %d 条 + 评论 %d 条），词库扩展到 %d 个关键词，每轮新增量 %s" % (
        len(rows), notes, cmts, len(final_keywords), history))
    print("最终词库：%s" % "、".join(sorted(final_keywords)))
    print("结果文件：%s" % out_path)


if __name__ == "__main__":
    main()
