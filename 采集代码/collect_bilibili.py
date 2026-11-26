# -*- coding: utf-8 -*-
"""
B站（浏览器版）自适应采集 —— 视频正文+评论
主题：预制菜进校园热点事件网络文本
功能：
  1) 页面内fetch搜索接口（带cookie，分页每页20条），最多10页
  2) 逐条视频fetch评论接口（主评论），翻页加载（每条最多50条）
  3) 主动学习自举扩展关键词 + 主题审核（新词必须与'预制菜进校园'相关，无关词自动剔除）
说明：
  - B站搜索页在自动化浏览器中不触发搜索结果，改用页面内fetch接口（浏览器环境自带
    cookie/buvid，规避-352风控）；接口若遇风控可升级wbi签名（见 _wbi_sign 备注）
  - B站搜索需登录态：首次运行请在浏览器窗口扫码登录
    （登录态自动保存到 _bilibili_login_cookies.json 复用）
  - 采集阶段不筛选：时间/相关性/去重等筛选由预处理阶段负责
"""
import os
import csv
import time
import json
import random
import re
import urllib.parse
import bootstrapper as boot
from playwright.sync_api import sync_playwright

#路径
PLATFORM = "bilibili"
RESULT_DIR = r"E:\learn\自然语言处理\大作业\result"
BASE_DIR = r"E:\learn\自然语言处理\大作业"
RESULT_FILE = "bilibili_comments.csv"

#浏览器版：单浏览器顺序采集（Playwright单线程），workers固定1
WORKERS = 1

#采集参数
MAX_PAGES = 10               # 搜索最多翻页数（每页20条）
MAX_COMMENTS = 300           # 每条视频评论采到底（滚动到页面评论区加载完为止；300为保险上限，防超热门视频无限加载拖死采集）
MAX_COMMENT_VIDEOS = 9999    # 所有视频都采评论（不设条数上限）
LOGIN_TIMEOUT = 180          # 等待扫码登录的最长秒数（B站搜索需登录态）

#断点续采 / 候选词 / 饱和日志文件（过程文件放根目录，result只留csv）
CHECKPOINT = os.path.join(BASE_DIR, "bilibili_checkpoint.json")
LOG_FILE = os.path.join(BASE_DIR, "bilibili_collect_log.txt")
CANDIDATE = os.path.join(BASE_DIR, "bilibili_candidate_keywords.txt")
COOKIE_FILE = os.path.join(BASE_DIR, "_bilibili_login_cookies.json")

#csv字段（type区分 video正文 / comment评论）
FIELDS = ["platform", "keyword", "type", "bvid", "user", "create_time",
          "content", "view_count", "like_count", "comment_count"]

#---------------- 主题审核（与微博/知乎/小红书一致）-----------------
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
    """审核候选关键词是否与'预制菜进校园'主题相关：命中任一主题词素即通过。"""
    kw = str(kw or "")
    return any(r in kw for r in THEME_ROOTS)

#---------------- 浏览器管理 -----------------
_browser = None
_context = None
_page = None
_playwright = None

def _browser_dead():
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

def _save_login_state():
    global _context
    try:
        if _context is None:
            return
        cookies = _context.cookies()
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False)
        print("[登录态] 已保存到 %s" % COOKIE_FILE)
    except Exception as e:
        print("[登录态] 保存失败: %s" % e)

def _restart_browser():
    global _browser, _context, _page
    try:
        _save_login_state()
    except Exception:
        pass
    for obj in (_page, _context, _browser):
        try:
            if obj is not None:
                obj.close()
        except Exception:
            pass
    _browser = _context = _page = None

def _has_login_cookie():
    """登录标志：B站登录后才有 SESSDATA cookie"""
    try:
        if _context is None:
            return False
        names = {c.get("name") for c in _context.cookies()}
        return bool(names & {"SESSDATA", "bili_jct", "DedeUserID"})
    except Exception:
        return False

def _wait_login():
    """等待用户在浏览器窗口扫码登录B站（未登录无法搜索，必须登录）"""
    if _has_login_cookie():
        return True
    print("[登录] B站搜索需登录态，请在浏览器窗口完成扫码/登录（等待%d秒）…" % LOGIN_TIMEOUT)
    print("[登录] 若未弹出登录框，请点击页面右上角\"登录\"按钮。")
    deadline = time.time() + LOGIN_TIMEOUT
    while time.time() < deadline:
        if _has_login_cookie():
            print("[登录] 登录成功，登录态已保存")
            _save_login_state()
            return True
        time.sleep(2)
    print("[登录] 等待超时，未检测到登录态（继续尝试，若搜不到请重跑并登录）")
    return False

def _ensure_browser():
    global _browser, _context, _page, _playwright
    if not _browser_dead():
        return
    print("[浏览器] 正在启动…")
    if _playwright is None:
        _playwright = sync_playwright().start()
    try:
        _browser = _playwright.chromium.launch(
            headless=False, args=["--disable-blink-features=AutomationControlled"],
            timeout=30000)
    except Exception as e:
        print("[浏览器] 启动失败：%s" % e)
        raise
    print("[浏览器] 窗口已打开")
    _context = _browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="zh-CN")
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, encoding="utf-8") as f:
                cookies = json.load(f)
            if cookies:
                _context.add_cookies(cookies)
                print("[浏览器] 已复用登录态")
        except Exception:
            pass
    _page = _context.new_page()
    _page.goto("https://www.bilibili.com/", timeout=30000, wait_until="domcontentloaded")
    time.sleep(3)
    _wait_login()
    print("[浏览器] 已就绪")

def _browser_sleep(lo=0.8, hi=1.8):
    time.sleep(random.uniform(lo, hi))

#---------------- 接口调用与解析 -----------------
def _fetch_json(url, retry=1):
    """页面内fetch接口（带cookie/buvid），失败且浏览器崩溃时重启重试"""
    for attempt in range(retry + 1):
        try:
            j = _page.evaluate(
                "fetch('%s',{credentials:'include'}).then(r=>r.json()).catch(e=>({err:String(e)}))" % url)
            if j is None:
                return None
            if "err" in j:
                raise Exception(j["err"][:80])
            return j
        except Exception as e:
            if _browser_dead() and attempt < retry:
                print("    [接口] 浏览器已崩溃，重启后重试…")
                _restart_browser()
                _ensure_browser()
            else:
                print("    [接口] 请求失败：%s" % str(e)[:60])
                return None
    return None

def _clean_html(s):
    """清理B站搜索接口返回的HTML高亮标签（如<em class="keyword">）"""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)
    return s.replace("&amp;", "&").strip()

def _norm_time(raw):
    """B站时间为unix秒 -> YYYY-MM-DD HH:MM"""
    try:
        ts = int(raw)
        if ts > 10 ** 12:
            ts = ts // 1000
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except Exception:
        return str(raw or "")

def _collect_search_responses(keyword):
    """分页fetch搜索接口（每页20条，最多MAX_PAGES页），返回原始JSON列表"""
    if _browser_dead():
        _ensure_browser()
    results = []
    for page in range(1, MAX_PAGES + 1):
        url = "https://api.bilibili.com/x/web-interface/search/type?" + urllib.parse.urlencode(
            {"search_type": "video", "keyword": keyword, "page": page})
        j = _fetch_json(url)
        if not j or j.get("code") != 0:
            break
        result = (j.get("data") or {}).get("result") or []
        if not result:
            break
        results.append(j)
        time.sleep(1.0)
    return results

def _extract_videos(keyword, results):
    """从搜索响应JSON中提取视频行（data.result[]，过滤非视频类型）"""
    rows = []
    seen = set()
    for j in results:
        for v in (j.get("data") or {}).get("result") or []:
            if not isinstance(v, dict):
                continue
            if v.get("type") != "video":               #过滤用户/番剧等混入条目
                continue
            bvid = str(v.get("bvid", ""))
            if not bvid or bvid in seen:
                continue
            seen.add(bvid)
            title = _clean_html(v.get("title"))
            desc = _clean_html(v.get("description"))
            content = title
            if desc and desc != title:
                content = title + " " + desc
            rows.append({
                "platform": PLATFORM, "keyword": keyword, "type": "video",
                "bvid": bvid, "user": v.get("author", ""),
                "create_time": _norm_time(v.get("pubdate", "")),
                "time": _norm_time(v.get("pubdate", "")),
                "content": boot.clean_text(content),
                "view_count": v.get("play", 0),
                "like_count": v.get("like", 0),
                "comment_count": v.get("video_review", 0),
                "aid": v.get("aid", ""),               #评论接口用（不写入csv）
            })
    return rows

def _collect_comments(keyword, video):
    """打开视频详情页，页面自身请求评论接口(x/v2/reply/wbi/main)，滚动加载并拦截响应体。
    手动fetch评论接口会被B站WAF拦截(-403/HTML)，必须依赖页面真实请求环境。
    滚动到评论区加载完为止（连续3轮无新评论响应即停），翻页采到底；
    评论区偶发不加载（B站限流），冷却60秒后重试一次，仍失败则跳过该视频。"""
    bvid = video["bvid"]
    responses = []
    def on_resp(resp):
        try:
            if "x/v2/reply/wbi/main" in resp.url:
                responses.append(resp)
        except Exception:
            pass
    def scroll_once():
        if _browser_dead():
            _ensure_browser()
        _page.on("response", on_resp)
        try:
            _page.goto("https://www.bilibili.com/video/%s" % bvid,
                       timeout=45000, wait_until="domcontentloaded")
            time.sleep(4)
            _page.mouse.move(500, 400)
            last_count = 0
            stall = 0
            for i in range(120):                      #最多120轮滚动（保险上限）
                _page.mouse.wheel(0, 3000)
                time.sleep(1.0)
                if len(responses) > last_count:
                    last_count = len(responses)
                    stall = 0
                elif len(responses) > 0:
                    stall += 1
                    if stall >= 3:                    #连续3轮无新评论响应 -> 评论区已加载完
                        break
                elif i > 40:                          #滚了40轮仍无评论响应，说明评论区未加载（被限流）
                    break
        except Exception:
            pass
        finally:
            try:
                _page.remove_listener("response", on_resp)
            except Exception:
                pass
        return len(responses) > 0

    got = False
    for attempt in range(2):
        responses.clear()
        got = scroll_once()
        if got:
            break
        if attempt == 0:
            print("    [评论] 视频%s评论区未加载，冷却60秒后重试…" % bvid)
            time.sleep(60)
    if not got:
        print("    [评论] 视频%s评论区两次均未加载，跳过" % bvid)
        return []
    #解析拦截到的响应体
    rows = []
    seen = set()
    for resp in responses:
        try:
            txt = resp.text()
            if not txt or not txt.lstrip().startswith("{"):
                continue
            j = json.loads(txt)
            for c in (j.get("data") or {}).get("replies") or []:
                rpid = str(c.get("rpid", ""))
                if rpid in seen:
                    continue
                seen.add(rpid)
                member = c.get("member") or {}
                rows.append({
                    "platform": PLATFORM, "keyword": keyword, "type": "comment",
                    "bvid": bvid, "user": member.get("uname", ""),
                    "create_time": _norm_time(c.get("ctime", "")),
                    "time": _norm_time(c.get("ctime", "")),
                    "content": boot.clean_text((c.get("content") or {}).get("message", "")),
                    "view_count": 0,
                    "like_count": c.get("like", 0),
                    "comment_count": c.get("rcount", 0),
                })
                if len(rows) >= MAX_COMMENTS:
                    return rows
        except Exception:
            continue
    return rows


def fetcher(keyword):
    """采集某关键词下的B站视频正文+评论。"""
    _ensure_browser()
    print("  [%s] 开始采集…" % keyword)
    rows = []
    results = _collect_search_responses(keyword)
    videos = _extract_videos(keyword, results)
    print("  [%s] 搜索到 %d 条视频" % (keyword, len(videos)))
    rows.extend(videos)
    #逐条采评论（所有有评论的视频都采，翻页采到底）
    commented = 0
    for v in videos:
        if int(v["comment_count"] or 0) <= 0:
            continue
        if commented >= MAX_COMMENT_VIDEOS:
            break
        commented += 1
        try:
            rows.extend(_collect_comments(keyword, v))
        except Exception:
            pass
        _browser_sleep()
        if commented % 5 == 0:
            print("  [%s] 已采 %d/%d 条视频的评论" % (keyword, commented, len(videos)))
    #每关键词结束重启浏览器，防长时间运行内存累积导致崩溃
    try:
        _restart_browser()
    except Exception:
        pass
    return rows


def main():
    os.makedirs(RESULT_DIR, exist_ok=True)

    #主题审核：清理checkpoint中已混入的无关词
    if os.path.exists(CHECKPOINT):
        try:
            with open(CHECKPOINT, encoding="utf-8") as f:
                ck = json.load(f)
            kws = ck.get("keywords") or []
            bad = [w for w in kws if not theme_audit(w)]
            if bad:
                ck["keywords"] = [w for w in kws if theme_audit(w)]
                with open(CHECKPOINT, "w", encoding="utf-8") as f:
                    json.dump(ck, f, ensure_ascii=False, indent=2)
                print("【主题审核】已清理无关词：%s" % "、".join(bad))
        except Exception:
            pass

    try:
        rows, final_keywords, history = boot.adaptive_collect(
            PLATFORM, fetcher, workers=WORKERS,
            seed_keywords=boot.SEED_KEYWORDS, exclude_words=boot.EXCLUDE_WORDS,
            checkpoint_path=CHECKPOINT, log_file=LOG_FILE, candidate_file=CANDIDATE,
            docx_log_path=os.path.join(BASE_DIR, "_work_词库记录", "词库演变记录_自动.docx"),
            min_hits=1, auto_add=True, use_classifier=True,
            word_audit=theme_audit)
    finally:
        _save_login_state()
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
    print("采集完成：共 %d 条（视频正文+评论），词库扩展到 %d 个关键词，每轮新增量 %s" % (
        len(rows), len(final_keywords), history))
    print("最终词库：%s" % "、".join(sorted(final_keywords)))
    print("结果文件：%s" % out_path)


if __name__ == "__main__":
    main()
