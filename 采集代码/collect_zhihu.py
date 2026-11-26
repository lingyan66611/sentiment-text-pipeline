# -*- coding: utf-8 -*-
"""
知乎（浏览器版）自适应采集 —— 回答+文章+评论
主题：预制菜进校园热点事件网络文本
功能：
  1) Playwright浏览器打开知乎，浏览器内fetch搜索接口（回答/文章/想法/话题），深翻页300条/关键词
  2) 每条回答/文章采集评论（主评论+楼中楼，comment_v5优先、旧接口回退双通道）
  3) 主动学习自举扩展关键词 + 主题审核（新词必须与'预制菜进校园'相关，无关词自动剔除）
说明：
  - requests直连已被知乎风控拦截（40352 unhuman验证），改走浏览器内fetch绕风控
  - 首次运行会弹出浏览器窗口；若出现"环境异常"验证页，请在窗口里手动完成验证（一次性，登录态自动保存复用）
采集阶段不筛选：时间/相关性/去重等筛选由预处理阶段负责。
"""
import os
import csv
import time
import json
import random
import bootstrapper as boot
from playwright.sync_api import sync_playwright

#路径
PLATFORM = "zhihu"
RESULT_DIR = r"E:\learn\自然语言处理\大作业\result"
BASE_DIR = r"E:\learn\自然语言处理\大作业"
RESULT_FILE = "zhihu_comments.csv"

#登录Cookie：登录 www.zhihu.com 后，复制浏览器请求头中的 Cookie 填入（z_c0必填）
COOKIE = "_zap=984b6138-b118-4ae3-adb3-4c6b0489c565; _xsrf=f9a7d572-4b4e-4bc9-80a0-76472927d627; d_c0=XMOZZF7IIR2PTmDdJylPy8Y32lMMYh1i2xM=|1793263324; captcha_session_v2=2|1:0|10:1793263325|18:captcha_session_v2|88:OS8yMEwzTjNEa1o1aCt0T0o0Z2NjT3VRY1ZaMCsvYTJHWnp6b3VOZjJBVElLMEMrQUZYbk92c3BJQ0hpUVNBRg==|dec0e1076345388fb494157fc1b395a9dc5dc3d374afc3b1ce17add236fd3004; __snaker__id=sUZ9xGMDfD20fzXd; DATE=1793263336238; crystal=U2FsdGVkX18443HQwQlfHr9ejF/0zZLdS0uXO4IvC58kE3rx1wB0qUPUMjxyFD/mu4Ubu48gwuErZSwifslfN3wM8rsovVIogEGhA9FoKOZhYDP7U7ApyK4RK8mvhspHF3Cxwf2xBcV2ojC6Xuze0BCH/VMnI1/jcpl9epn5bnxTLfa41Y0iqm5LqziknWWRZR6DDg52IcF+Q8G0l95IIcBYpwptbsdfDJS+u1pExyD3pMTrPw8x4HLIhQb0fKw5; cmci9xde=U2FsdGVkX1/UYYqxqr6ZfgPTBNFXHq922FjpC2WBfhB/rxzCx64KXzCUxcuuxMfgGBzab4Tk9wOcavD9LmA+bg==; pmck9xge=U2FsdGVkX19C+7+/owZqqaLcK/KeLAMqqyiUjG8B6oo=; assva6=U2FsdGVkX1/BKOPzbh/c3pAMVjCu+0fPZSSXhQ6NZrs=; assva5=U2FsdGVkX19Qd0kLwu1IrQmZvm9VvbycTklwHwgOEfD/KkqKR0/lLYjxNzvtTGfGlYoDR4Q+jC7iRjhacvyPFg==; vmce9xdq=U2FsdGVkX1/IfBZNuOh7kY9OTGtBYwC2h0YFAcdXIjAkY6aTh67+oaEl1u18FZBDrtjwPsyoT5VCu2h7V0zmpDSzgJSXGc7d/0zLftLIe/1tX3H+P7QeTQhATSCtsjXhA4mFFcC8jrUmaRkVW0pYfBlFc/sXyzLfap3ssc0HkF8=; gdxidpyhxdE=MnkEBUpqmAYtKquvukK9x2AImlVMtoznPZlJTJ9o5lpq2rWCizVrZUoLjeenxEI707P%5Cb50azTcpbXs3UEbbmUiLhLppyit6RI%2BoQwrGeZHt%5CbelVS%5C958WdbMs8LA8zgkYcA80RHgYqxCCZEqAPvrQ52%2FxzsUeQG116IU%2BPSr%2BLnZWq%3A1793264238915; captcha_ticket_v2=2|1:0|10:1793263368|17:captcha_ticket_v2|728:eyJ2YWxpZGF0ZSI6IkNOMzFfU0FVTTNSaFB2ci5KUzhiR1BSYVU0VGJ5VVN2NTU4S1RVc2oxNWFqKjZ2KkxHMkhOcWpkckt4Q01sVm13Y2U0UE5LRHY5Ym42SlNobVVZdnZkdVF5TVBBWlp2ZnlsLmxJLkV6RE95MipTZVJHbml4NkdXYVRtSjAud3BDRWk5d29sVFRtc1VvWEc4dUlnYWVPVlNVT3pyR2owakcwVTRtREtVRXh0cG5ZbGxzelEwZFNmeDhFVlU2RFFYVXVWdlR0emMuQ1NKbDB1TzhOUEp4bk8uUEFFUF96OE5TTFZ2bFluM1huZ0dYX2MuWTBQdUptdW4yNVlXV3BvaGRHKkE5b1FoWEdhWGIyS2RqZTNnWFBMUzN5cFJ1Qk9LeV9uMlZSc3F3ZDFNQ1JCM1F0UVpEYjhYZFBmKllGTlZRWUNGM2hzQ0xLZl9NYk5nKlA5QWY5ZEpoM0hoeU5VdER3Z3ZQSHkwWk5mSjMuQ0RFKjJpc0dsc3o0UXlGcHp5cTZXWUJCU0dQQV9xMmVqQTRWNFFwbWs2NUdhUEczbklkMCpKTXd1OWFxSTlRZk9aVUlxU1laUzRaWkJEbWpXKmlnYWxYQWpfMFFBUXBDc1gqKmIyeEN3VHJjZ2U4a2NXZHNmQ21fZ3Y5NjYxOHhIbHIzakRaREdGSTVZRnoydlRFWGZEbkh5bldzck03N192X2lfMSJ9|af3c8bc3a0d5e9306611f7a81d6f8ff38d2603763a9bf0dc11ce1daec2220eb6; z_c0=2|1:0|10:1793263383|4:z_c0|92:Mi4xQUxNNktnQUFBQUJjdzVsa1hzZ2hIU1lBQUFCZ0FsVk5GMVhRYXdERG1MUFI2RFlnbnpuUmR3WXlMUmhQQjlHa05n|88e0737441db766db191a7e9833c900ec33fdaf5ca3b85b5bf1866666f36c460; q_c1=7e6c6fa8285346cb8b5603ba66b4f405|1793263384000|1793263384000; __zse_ck=005_6tbPxoMtRXBVREfMXw/YhVwvnziP=Xz4smwidaj0zvWo52seZ6/bGl9tWM7pkSmrFq/tGH6bU=nuc=Utmg=rov1l9Q0JO8VPM2jBU=JOBfxs9sGi3XuFFH5Sx3UyDhmB-WHH+835i3tWb3sntaVgriVNxH8ggt0dEvgkvueeS4YsOH2r52UvxjHSi+rbuYb9/ClTM6DoGMYvHAlgR30fUe5vomX9/G70kbDMl9DQ4gaIL+M5/1B2vZxgZfKH2xHfN; SESSIONID=6v4yCuDHHJ5mGC6KIfI4MUgNGmQUiE7McRFmIKiKahc; BEC=d6322fc1daba6406210e61eaa4ec5a7a"

#浏览器版：单浏览器顺序采集（Playwright单线程），workers固定1
WORKERS = 1

#评论采集参数
MAX_COMMENTS = 30          # 每条回答/文章最多采集的主评论数
MAX_SUB_COMMENTS = 10      # 每条主评论最多采集的楼中楼子评论数

#断点续采 / 候选词 / 饱和日志文件（过程文件放根目录，result只留csv）
CHECKPOINT = os.path.join(BASE_DIR, "zhihu_checkpoint.json")
LOG_FILE = os.path.join(BASE_DIR, "zhihu_collect_log.txt")
CANDIDATE = os.path.join(BASE_DIR, "zhihu_candidate_keywords.txt")
COOKIE_FILE = os.path.join(BASE_DIR, "_zhihu_login_cookies.json")

#csv字段（纯采集字段）
FIELDS = ["platform", "keyword", "type", "zhihu_id", "title", "user",
          "created_time", "content", "voteup_count", "comment_count"]

#---------------- 主题审核（与小红书一致）：新词必须与'预制菜进校园'相关 -----------------
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
    """审核候选关键词是否与'预制菜进校园'主题相关：命中任一主题词素即通过；
    纯泛化词（其实/地方/西贝/还是/时候/问题/自己…）拒绝。"""
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
    """保存浏览器登录态（含验证通过后更新的cookie）"""
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
    """保存登录态并彻底关闭当前浏览器（崩溃自愈/防内存累积）"""
    global _browser, _context, _page
    try:
        _save_login_state()
    except Exception:
        pass
    try:
        if _page is not None:
            _page.close()
    except Exception:
        pass
    try:
        if _context is not None:
            _context.close()
    except Exception:
        pass
    try:
        if _browser is not None:
            _browser.close()
    except Exception:
        pass
    _browser = _context = _page = None

def _ensure_browser():
    """确保浏览器可用：启动 + 注入Cookie + 打开知乎；处理unhuman验证"""
    global _browser, _context, _page, _playwright
    if not _browser_dead():
        return
    print("[浏览器] 正在启动…")
    if _playwright is None:
        print("[浏览器] 启动Playwright驱动…")
        _playwright = sync_playwright().start()
        print("[浏览器] 驱动已就绪")
    try:
        _browser = _playwright.chromium.launch(
            headless=False, args=["--disable-blink-features=AutomationControlled"],
            timeout=30000)
    except Exception as e:
        print("[浏览器] 启动失败：%s" % e)
        raise
    print("[浏览器] 浏览器窗口已打开")
    _context = _browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="zh-CN")
    #优先用保存的登录态，否则注入COOKIE
    cookies = []
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, encoding="utf-8") as f:
                cookies = json.load(f)
        except Exception:
            cookies = []
    if not cookies:
        for pair in COOKIE.split(";"):
            if "=" in pair:
                k, v = pair.strip().split("=", 1)
                cookies.append({"name": k, "value": v, "domain": ".zhihu.com", "path": "/"})
    print("[浏览器] 注入登录态（%d个cookie）…" % len(cookies))
    _context.add_cookies(cookies)
    _page = _context.new_page()
    print("[浏览器] 打开知乎首页…")
    try:
        _page.goto("https://www.zhihu.com/", timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        print("[浏览器] 打开知乎首页失败：%s（继续尝试）" % e)
    time.sleep(3)
    #unhuman验证页：提示但不阻塞（接口fetch不受页面导航影响；用户可顺手完成验证）
    if "unhuman" in _page.url:
        print("[提示] 知乎弹出环境验证页（不影响接口采集，可继续）。")
        print("[提示] 如需更稳定，请在浏览器窗口手动完成验证（等待15秒）…")
        for _ in range(3):
            time.sleep(5)
            try:
                if "unhuman" not in _page.url:
                    break
            except Exception:
                pass
    print("[浏览器] 已就绪，当前页面：%s" % _page.url[:60])

def _browser_fetch(url, params):
    """在浏览器内fetch JSON（同源请求，绕requests风控）。返回dict。"""
    _ensure_browser()
    qs = "&".join("%s=%s" % (k, v) for k, v in params.items())
    full = url + ("?" + qs if qs else "")
    for attempt in range(2):
        try:
            return _page.evaluate(
                """(u) => fetch(u, {credentials: 'include',
                    headers: {'Accept': 'application/json', 'x-requested-with': 'fetch'}}
                   ).then(r => r.json())""", full)
        except Exception as e:
            if _browser_dead():
                print("[自愈] 浏览器崩溃，自动重启重试…")
                _restart_browser()
                _ensure_browser()
                continue
            raise e
    raise RuntimeError("浏览器fetch失败: %s" % url)

def _browser_sleep():
    """浏览器内请求随机停顿（防频率风控）"""
    time.sleep(random.uniform(0.4, 1.2))

#---------------- 解析与采集 -----------------
def _norm_time(raw):
    """知乎时间为unix秒 -> YYYY-MM-DD HH:MM（便于时序演化分析）。"""
    try:
        ts = int(raw)
        if ts > 10 ** 12:
            ts = ts // 1000
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except Exception:
        return str(raw or "")


def _fetch_comments(keyword, otype, oid, title):
    """采集一个回答/文章下的评论（主评论+楼中楼子评论），浏览器内fetch。
    双通道：优先comment_v5（含楼中楼），不可用则回退旧comments接口。"""
    rows = []
    if otype not in ("answer", "article"):
        return rows
    seg = "answers" if otype == "answer" else "articles"
    seen = set()

    def add_comment(c):
        cid = str(c.get("id", ""))
        if cid in seen:
            return
        seen.add(cid)
        rows.append({
            "platform": PLATFORM, "keyword": keyword,
            "id": "cmt_" + cid, "type": "comment", "zhihu_id": cid,
            "title": title or "", "user": (c.get("author") or {}).get("name", ""),
            "created_time": _norm_time(c.get("created_time", "")),
            "time": _norm_time(c.get("created_time", "")),
            "content": boot.clean_text(c.get("content", "")),
            "voteup_count": c.get("like_count", 0),
            "comment_count": c.get("child_comment_count", 0),
        })
        for sc in (c.get("child_comments") or [])[:MAX_SUB_COMMENTS]:
            scid = str(sc.get("id", ""))
            if scid in seen:
                continue
            seen.add(scid)
            rows.append({
                "platform": PLATFORM, "keyword": keyword,
                "id": "scmt_" + scid, "type": "comment", "zhihu_id": scid,
                "title": title or "", "user": (sc.get("author") or {}).get("name", ""),
                "created_time": _norm_time(sc.get("created_time", "")),
                "time": _norm_time(sc.get("created_time", "")),
                "content": boot.clean_text(sc.get("content", "")),
                "voteup_count": sc.get("like_count", 0),
                "comment_count": 0,
            })

    #通道1：comment_v5（含楼中楼），仅当首页有数据时使用
    use_v5 = False
    try:
        j5 = _browser_fetch(
            "https://www.zhihu.com/api/v4/comment_v5/%s/%s/root_comment" % (seg, oid),
            {"limit": 20, "offset": 0, "order_by": "default", "status": "open"})
        use_v5 = bool(j5.get("data"))
    except Exception:
        use_v5 = False
    _browser_sleep()

    if use_v5:
        offset = 0
        while offset < MAX_COMMENTS:
            try:
                j = _browser_fetch(
                    "https://www.zhihu.com/api/v4/comment_v5/%s/%s/root_comment" % (seg, oid),
                    {"limit": 20, "offset": offset, "order_by": "default", "status": "open"})
            except Exception:
                break
            items = j.get("data") or []
            if not items:
                break
            for c in items:
                add_comment(c)
            paging = j.get("paging") or {}
            if paging.get("is_end") or not paging.get("next"):
                break
            offset += 20
            _browser_sleep()
        return rows

    #通道2：旧comments接口（全量可用，无楼中楼）
    for offset in range(0, MAX_COMMENTS, 20):
        try:
            j = _browser_fetch(
                "https://www.zhihu.com/api/v4/%s/%s/comments" % (seg, oid),
                {"limit": 20, "offset": offset})
        except Exception:
            break
        items = j.get("data") or []
        if not items:
            break
        for c in items:
            add_comment(c)
        paging = j.get("paging") or {}
        if paging.get("is_end") or not paging.get("next"):
            break
        _browser_sleep()
    return rows


def fetcher(keyword):
    """采集某关键词下的知乎结果（回答/文章/想法/话题）及回答/文章的评论，深翻页300条。"""
    rows = []
    print("  [%s] 开始采集…" % keyword)
    for offset in range(0, 300, 20):            #每页20条，最多采300条
        try:
            data = _browser_fetch("https://www.zhihu.com/api/v4/search_v3",
                                  {"t": "general", "q": keyword, "offset": offset, "limit": 20})
        except Exception as e:
            print("  [%s] offset=%d 失败：%s" % (keyword, offset, e))
            break
        err = data.get("error")
        if err:
            msg = (err.get("message") or "")[:60]
            print("  [%s] 接口报错：%s" % (keyword, msg))
            break
        items = data.get("data") or []
        for it in items:
            obj = it.get("object") or {}
            otype = obj.get("type", "")
            if otype == "answer":
                content, title = boot.clean_text(obj.get("content")), ""
            elif otype == "article":
                content, title = boot.clean_text(obj.get("excerpt")), obj.get("title", "")
            else:
                content = boot.clean_text(obj.get("content", obj.get("excerpt", "")))
                title = ""
            rows.append({
                "platform": PLATFORM,
                "keyword": keyword,
                "id": str(obj.get("id", "")),
                "type": otype,
                "zhihu_id": str(obj.get("id", "")),
                "title": title,
                "user": (obj.get("author") or {}).get("name", ""),
                "created_time": _norm_time(obj.get("created_time", obj.get("created", ""))),
                "time": _norm_time(obj.get("created_time", obj.get("created", ""))),
                "content": content,
                "voteup_count": obj.get("voteup_count", 0),
                "comment_count": obj.get("comment_count", 0),
            })
            #回答/文章下有评论时，一并采集评论（含楼中楼）
            if otype in ("answer", "article") and int(obj.get("comment_count") or 0) > 0:
                try:
                    rows.extend(_fetch_comments(keyword, otype, str(obj.get("id", "")), title))
                except Exception:
                    pass
        print("  [%s] 进度：本页%d条，累计%d条" % (keyword, len(items), len(rows)))
        if not items:
            break
        _browser_sleep()                        #防封号
    return rows


def main():
    os.makedirs(RESULT_DIR, exist_ok=True)

    #主题审核：清理checkpoint中已混入的无关词（其实/地方/西贝/还是…）
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

    if not COOKIE:
        print("【提示】未配置 COOKIE，知乎需登录后才能采集。请在 collect_zhihu.py 顶部填入 Cookie 后重跑。")
        with open(os.path.join(RESULT_DIR, RESULT_FILE), "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
        print("已生成空结果文件：%s" % os.path.join(RESULT_DIR, RESULT_FILE))
        return

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
        #按csv表头过滤字段：fetcher返回的id/time是给引擎去重/时间筛选用的，不写入csv
        writer.writerows([{k: r.get(k, "") for k in FIELDS} for r in rows])
    print("采集完成：共 %d 条（回答/文章/评论），词库扩展到 %d 个关键词，每轮新增量 %s" % (
        len(rows), len(final_keywords), history))
    print("最终词库：%s" % "、".join(sorted(final_keywords)))
    print("结果文件：%s" % out_path)


if __name__ == "__main__":
    main()
