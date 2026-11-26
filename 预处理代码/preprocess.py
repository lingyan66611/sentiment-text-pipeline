# -*- coding: utf-8 -*-
"""
高质量预处理与语料统计分析流水线（含主题相关性审核）
对应课程PPT与历次上机方法：
  - PPT第3章《词法分析》：jieba分词 + 停用词过滤 + 领域词典
  - PPT第8章《文本表示/特征提取》：TF-IDF向量化，提取每个时间段的高权重关键词
  - 时序分析基础：统一时间格式（word要求），按时间聚合文本量，识别舆情高峰拐点

流程（自上而下）：
  1) 读入 result/ 下 5 个平台的采集csv，统一时间格式，按 2023-06-01 ~ 2025-12-31 时间窗过滤
  2) 主题相关性审核（论文口径）：不按关键词标签删，逐条按文本内容判定，见 topic_audit()
  3) 合并语料 / 词频 / 分月TF-IDF / 时间分布

输出目录：预处理结果/（与 result/ 采集原始数据分开，不覆盖采集结果）
  - topic_audit.csv          逐条审核明细（判定、得分、命中词、父级判定、文本片段）
  - topic_stats.csv          按平台、按原关键词标签的保留/剔除统计
  - merged_corpus.csv        审核通过后的统一语料（platform, date, text, author,
                             like_count, comment_count, extra_count, repeat_count）
  - word_freq.csv            全语料词频（分词+停用词+同义词归一后）
  - tfidf_keywords.csv       按月的TF-IDF高权重关键词
  - time_distribution.csv    按月文本量分布，用于定位舆情高峰
  - cleaning_stats.csv       各平台各阶段剔除/保留数量统计（交接说明回填用）
"""
import os
import csv
import re
import datetime
import collections
import jieba
import jieba.analyse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

#--------------- 路径 ---------------
RESULT_DIR = r"E:\learn\自然语言处理\大作业\result"          # 采集原始数据（只读）
PROCESS_DIR = r"E:\learn\自然语言处理\大作业\预处理结果"       # 预处理产物（新建，全部输出写这里）

MERGE_FILE = "merged_corpus.csv"
FREQ_FILE = "word_freq.csv"
TFIDF_FILE = "tfidf_keywords.csv"
TIME_FILE = "time_distribution.csv"
AUDIT_FILE = "topic_audit.csv"
STATS_FILE = "topic_stats.csv"
CLEAN_FILE = "cleaning_stats.csv"

#各平台采集结果文件：平台标识, 文件名, 正文列, 时间列, 父级id列, 父级标题列,
# 作者列, 点赞列, 评论/回复列, 额外互动量列（转发/播放等；无则 None）
PLATFORM_SOURCES = [
    ("weibo",      "weibo_comments.csv",         "text",    "created_at",   "weibo_id", None,    "user", "likes_count",  "comments_count", "reposts_count"),
    ("zhihu",      "zhihu_comments.csv",         "content", "created_time", "zhihu_id", "title", "user", "voteup_count", "comment_count",  None),
    ("xiaohongshu","xiaohongshu_comments.csv",   "content", "create_time",  "note_id",  None,    "user", "like_count",   "reply_count",    None),
    ("bilibili",   "bilibili_comments.csv",      "content", "create_time",  "bvid",     None,    "user", "like_count",   "comment_count",  "view_count"),
    ("weibo_reply","weibo_comments_replies.csv", "content", "created_at",   "weibo_id", None,    "user", None,           None,             None),
]

#分析时间窗（与设计说明一致）：窗口外的文本剔除；无时间字段的记录保留，仅参与词频
DATE_START = "2023-06-01"
DATE_END = "2025-12-31"

#英文月份 -> 数字（微博时间格式用）
MONTH_MAP = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
             "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}

#--------------- 停用词（权威公开表 + 内置补充） ---------------
#词典统一放在脚本同目录“词典库”文件夹；停用词表 stopwords.txt 由用户自行
#下载合并（哈工大、百度、四川大学机器智能实验室三套公开表），与下方内置表
#（页面UI残留词 + 本语料口语补充）合并。文件缺失时回退内置表，不影响运行。
DICT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "词典库")
STOPWORD_FILE = os.path.join(DICT_DIR, "stopwords.txt")

_BUILTIN_STOP = """的 了 是 在 我 你 他 她 它 我们 你们 他们 这个 那个 一个 什么 怎么
为什么 这样 那样 没有 不是 但是 因为 所以 如果 虽然 可以 可能 已经 正在 现在 大家
真的 觉得 知道 应该 一些 还有 之后 之前 里面 外面 这 那 和 与 也 都 就 还 而 及 并
等 很 最 更 之 其 或 跟 对 被 把 从 向 往 于 而 让 给 来 去 说 到 出 上 下 中 大 小
展开 收起 查看 图片 全文 阅读 回复 转发 点赞 评论 关注 分享 收藏 举报 链接 网页
点击 查看全文 展开全文 查看更多 回复我 表情 视频 图片评论 发布于 来自 客户端 微博
东西 感觉 那种 一点 只能 天天 地方 有人 几个 确实 一份 不用 有没有 做好 情况 不想
这是 不到 打开 一种 全是 大部分 遥遥领先 哈哈哈
""".split()


def _load_stopwords():
    sw = set(_BUILTIN_STOP)
    if os.path.exists(STOPWORD_FILE):
        with open(STOPWORD_FILE, encoding="utf-8") as f:
            for line in f:
                w = line.strip()
                if w:
                    sw.add(w)
    return sw


STOPWORDS = _load_stopwords()

#--------------- 同义词/异形词归并 ---------------
#分词后统一映射到规范词，避免同一个概念在词频里被拆成多个词。
#只做“确定等价”的归并，不做语义扩展（不把“学校食堂”并进“预制菜”）。
SYNONYM_MAP = {
    "预支菜": "预制菜", "预置菜": "预制菜", "预制莱": "预制菜",
    "預製菜": "预制菜", "预制菜品": "预制菜", "预制菜产品": "预制菜",
    "料理包": "预制菜", "速食包": "预制菜", "预包装食品": "预制菜",
    "学生营养餐": "学生餐", "营养午餐": "学生餐",
    "中央食堂": "中央厨房",
}

#--------------- 领域词典：外挂用户词典 + 内置兜底 ---------------
#用户把从权威渠道下载的分词词典存到“词典库”文件夹 userdict.txt，启动时自动加载。
#jieba 用户词典格式：每行一个词，可带“词频 词性”（空格/制表符分隔），UTF-8 编码；
#文件缺失时用下方内置核心主题词兜底，不影响运行。
USER_DICT_FILE = os.path.join(DICT_DIR, "userdict.txt")
if os.path.exists(USER_DICT_FILE):
    jieba.load_userdict(USER_DICT_FILE)

DOMAIN_WORDS = ["预制菜", "预制菜进校园", "进校园", "校园餐", "学生餐", "学校食堂",
                "中央厨房", "校园配餐", "食品安全", "市场监管", "配餐", "餐食",
                "绿捷", "添加剂", "明厨亮灶", "校外供餐", "食堂", "营养餐", "净菜",
                "料理包", "速食包", "预包装食品", "半成品菜", "半成品", "防腐剂",
                "保质期", "现做", "炒菜", "快餐", "罐头", "食材"]
for _w in DOMAIN_WORDS:
    jieba.add_word(_w)


#--------------- 词法分析：分词 + 去停用词 + 同义归一（PPT第3章） ---------------
_BRACKET_EMOJI = re.compile(r"\[[^\[\]]*\]")


def _strip_emoji(text):
    """剥除方括号表情（[doge] [星星眼] 等），避免被切成英文/单字噪声。"""
    return _BRACKET_EMOJI.sub(" ", text or "")


def norm_word(w):
    """把异形词/等价说法归并到规范词"""
    return SYNONYM_MAP.get(w, w)


def tokenize(text):
    """jieba分词，过滤单字/纯符号/纯数字/纯英文/停用词，并把同义词归一到规范词"""
    words = jieba.lcut(_strip_emoji(text))
    out = []
    for w in words:
        w = w.strip()
        if not w:
            continue
        if len(w) <= 1:                    #过滤单字
            continue
        if re.fullmatch(r"[\W_]+", w):      #过滤纯符号
            continue
        if re.search(r"[0-9０-９]", w):      #过滤含数字（价格/重量/百分比/编码口令）
            continue
        if re.fullmatch(r"[A-Za-z]+", w):   #过滤纯英文（表情名/昵称残留）
            continue
        if w in STOPWORDS:                 #过滤停用词/UI残留词
            continue
        out.append(norm_word(w))
    return out


def topic_tokens(text):
    """主题审核专用分词：保留单字（吃/饭/餐/菜 这类场景判断要用），只去停用词与符号"""
    out = []
    for w in jieba.lcut(_strip_emoji(text)):
        w = w.strip()
        if not w:
            continue
        if re.fullmatch(r"[\W_]+", w):
            continue
        if re.search(r"[0-9０-９]", w):
            continue
        if re.fullmatch(r"[A-Za-z]+", w):
            continue
        if w in STOPWORDS:
            continue
        out.append(norm_word(w))
    return out


#--------------- 统一时间解析 ---------------
def parse_time(platform, raw):
    """把各平台的时间字段统一为 YYYY-MM-DD。支持三种原始形态：
    1) 字符串日期：2026-01-18 13:24 / 2026-10-12 08:31:52 / 2026-01-18 / 2026年1月18日
    2) 微博英文日期串：Tue Sep 16 06:53:37 +0800 2025（年份在最后一段）
    3) unix 时间戳（秒或毫秒）
    无法识别时返回空字符串。
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""

    #1) 标准字符串日期（知乎、小红书、B站的时间字段）
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if not m:
        m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return "%04d-%02d-%02d" % (y, mo, d)

    #2) 微博英文日期串：Wed Sep 20 12:00:00 +0800 2023
    parts = s.split()
    if len(parts) >= 6:
        mon = MONTH_MAP.get(parts[1])
        if mon:
            try:
                day = int(parts[2])
                year = int(parts[-1])      #最后一段才是年份
            except ValueError:
                day, year = 0, 0
            if day and 2000 <= year <= 2100:
                return "%04d-%02d-%02d" % (year, mon, day)

    #3) unix 时间戳
    try:
        ts = int(float(s))
        if ts > 10 ** 12:                  #毫秒转秒
            ts = ts // 1000
        dt = datetime.datetime.fromtimestamp(ts)
        if 2000 <= dt.year <= 2100:
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass

    return ""


#--------------- 读取并合并各平台语料 ---------------
def _mask_author(name):
    """作者昵称脱敏：保留首字，其余以星号代替；空值返回空串。"""
    s = (name or "").strip()
    if not s:
        return ""
    if len(s) == 1:
        return s + "*"
    return s[0] + "***"


def _int_or_blank(v):
    """互动量转整数；缺失或非数字时返回空串。"""
    try:
        return str(int(float(str(v).strip())))
    except Exception:
        return ""


def load_and_merge():
    """读取各平台采集csv，统一为 rows(platform, date, text, pid, kw, file)。
    - 时间窗外的记录剔除（时间无法解析的不剔除，date 留空，仅参与词频）
    - pid 用于主题审核时的“父级上下文”（同一帖子/视频下聚合判断）
    - kw 保留采集时的关键词标签，仅用于事后统计，不参与剔除判断
    """
    rows = []
    stats = []
    for platform, fname, text_col, time_col, pid_col, title_col, author_col, like_col, comment_col, extra_col in PLATFORM_SOURCES:
        path = os.path.join(RESULT_DIR, fname)
        if not os.path.exists(path):
            continue
        total = kept = outside = 0
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for rec in reader:
                text = (rec.get(text_col) or "").strip()
                if not text:
                    continue
                total += 1
                date = parse_time(platform, rec.get(time_col)) if time_col else ""
                if date and (date < DATE_START or date > DATE_END):
                    outside += 1          #时间窗外，剔除
                    continue
                title = (rec.get(title_col) or "").strip() if title_col else ""
                kept += 1
                rows.append({
                    "platform": platform,
                    "date": date,
                    "text": text,
                    "pid": (rec.get(pid_col) or "").strip(),
                    "kw": (rec.get("keyword") or "").strip(),
                    "parent_text": title,
                    "file": fname,
                    "author": _mask_author(rec.get(author_col)) if author_col else "",
                    "like_count": _int_or_blank(rec.get(like_col)) if like_col else "",
                    "comment_count": _int_or_blank(rec.get(comment_col)) if comment_col else "",
                    "extra_count": _int_or_blank(rec.get(extra_col)) if extra_col else "",
                })
        stats.append({"platform": platform, "file": fname,
                      "total": total, "kept": kept, "outside": outside})
    return rows, stats


#--------------- 主题相关性审核（论文口径，按内容不按标签） ---------------
#四类主题词表（分词后的“词”级匹配，不做子串包含，避免“预制板”误判）
CORE_WORDS = {"预制菜", "料理包", "速食包", "预包装食品", "中央厨房",
              "绿捷", "明厨亮灶", "半成品菜", "净菜"}
LINK_WORDS = {"校园餐", "学生餐", "营养餐", "校园配餐", "校外供餐",
              "配餐", "供餐", "团餐", "中央食堂"}
SCENE_WORDS = {"校园", "学校", "学生", "孩子", "家长", "老师", "食堂", "午餐",
               "饭菜", "餐", "饭", "早餐", "晚餐", "幼儿园", "中小学"}
FOOD_WORDS = {"吃", "饭", "餐", "菜", "食", "食品", "食物", "菜单", "口味", "食材"}
SOFT_WORDS = {"添加剂", "冷链", "食品安全", "消毒", "配送", "监管", "卫生"}

#主题描述：用于给“场景+食物”这类模糊文本打语义相似度分
TOPIC_DESC = ("预制菜进入中小学幼儿园校园引发的争议。家长反对学校食堂使用预制菜料理包，"
              "要求校园配餐公开透明。中央厨房集中供餐、学生营养餐、校外供餐的食品安全"
              "与添加剂问题，监管部门推进明厨亮灶，家长与学生讨论饭菜质量与健康。")

SIM_TH = float(os.environ.get("TOPIC_SIM_TH", "0.12"))        #相似度阈值，可用环境变量调
AUDIT_ONLY = os.environ.get("TOPIC_AUDIT_ONLY", "") == "1"    #仅标记不剔除

#平台级阈值：小红书采集到的样本最少（时间窗内仅数百条），评论又极短、口语化，
#严格阈值会把剩下样本砍到没法做月度统计，因此单独放宽。
PLATFORM_SIM_TH = {
    "xiaohongshu": float(os.environ.get("XHS_SIM_TH", "0.07")),
}
#父级判定放宽的平台：小红书笔记正文未采集，父级只能靠同一 note_id 下的评论聚合，
#条件放宽为“父级文本含任一场景词或食物词”即判相关。
RELAX_PARENT_PLATFORM = {"xiaohongshu"}


def judge_tokens(tokens):
    """单条文本的初判，返回 (判定类别, 命中词)
    类别：core / link / soft / candidate / none
    candidate = 只有场景词+食物词，需要用语义相似度进一步判断
    """
    tok = set(tokens)
    hit = tok & CORE_WORDS
    if hit:
        return "core", "、".join(sorted(hit)[:3])
    hit = tok & LINK_WORDS
    if hit:
        return "link", "、".join(sorted(hit)[:3])
    hit = tok & SOFT_WORDS
    soft = bool(hit)
    has_scene = bool(tok & SCENE_WORDS)
    has_food = bool(tok & FOOD_WORDS)
    if soft and (has_scene or has_food):
        return "soft", "、".join(sorted(hit)[:3])
    if has_scene and has_food:
        m = (tok & SCENE_WORDS) | (tok & FOOD_WORDS)
        return "candidate", "、".join(sorted(m)[:3])
    return "none", ""


def topic_audit(rows):
    """主题相关性审核：三层判断 + 父级上下文兜底
    第1层：主题核心词/关联词/软词命中 -> 直接判相关
    第2层：只有“场景词+食物词” -> 用TF-IDF余弦相似度对主题描述打分，超过阈值判相关
    第3层：单条判不出来（短评论、纯情绪词）-> 看父级（同一帖子/视频下其他文本）判定，
           父级相关则该条继承保留，避免误杀“支持”“太对了”这类短表态

    返回 (保留rows, 审核明细, 统计dict)
    """
    #先把每条文本分词，判定类别
    recs = []
    core_segs = []           #用于训练主题向量空间的正例句（分词后），最多取 6 万条
    for r in rows:
        toks = topic_tokens(r["text"])
        kind, hit = judge_tokens(toks)
        recs.append({"row": r, "tokens": toks, "kind": kind, "hit": hit, "score": ""})
        if kind in ("core", "link", "soft") and len(core_segs) < 60000:
            core_segs.append(" ".join(toks))

    #主题向量空间：只在“已经确定相关”的句子上 fit，词典来自真实正例
    vec = None
    q_vec = None
    if len(core_segs) >= 100:
        try:
            vec = TfidfVectorizer(token_pattern=r"\S+", min_df=2, max_df=0.95, max_features=8000)
            vec.fit(core_segs)
            q_vec = vec.transform([" ".join(topic_tokens(TOPIC_DESC))])
        except Exception:
            vec = None

    #第2层：模糊文本打相似度分
    cand_idx = [i for i, rec in enumerate(recs) if rec["kind"] == "candidate"]
    if vec is not None and cand_idx:
        texts = [" ".join(recs[i]["tokens"]) for i in cand_idx]
        try:
            sim = cosine_similarity(vec.transform(texts), q_vec).ravel()
            for k, i in enumerate(cand_idx):
                recs[i]["score"] = round(float(sim[k]), 4)
        except Exception:
            pass

    #父级聚合：同一 platform+pid 下取前 6 条文本拼成上下文；知乎有标题则直接用标题
    agg = collections.defaultdict(list)
    for rec in recs:
        r = rec["row"]
        pid = r["pid"]
        if not pid:
            continue
        key = (r["platform"], pid)
        if r["parent_text"]:                   #知乎：标题就是父级主题
            if not agg[key]:
                agg[key].append(r["parent_text"])
            continue
        cur = agg[key]
        if len(cur) >= 6 or sum(len(x) for x in cur) >= 400:
            continue
        cur.append(r["text"])

    parent_judge = {}
    for key, parts in agg.items():
        if not parts:
            continue
        plat = key[0]
        toks = topic_tokens(" ".join(parts))
        kind, _hit = judge_tokens(toks)
        ok = kind in ("core", "link", "soft")
        if not ok and plat in RELAX_PARENT_PLATFORM:
            tok = set(toks)
            if (tok & SCENE_WORDS) or (tok & FOOD_WORDS):
                ok = True                 #小红书：父级出现场景/食物词即判相关
        if not ok and kind == "candidate" and vec is not None:
            try:
                s = float(cosine_similarity(vec.transform([" ".join(toks)]), q_vec)[0][0])
                ok = s >= PLATFORM_SIM_TH.get(plat, SIM_TH)
            except Exception:
                ok = False
        parent_judge[key] = ok

    #第3层：汇总保留/剔除
    kept = []
    audit_rows = []
    stat = collections.Counter()
    for rec in recs:
        r = rec["row"]
        kind = rec["kind"]
        label = ""
        if not rec["tokens"]:                     #纯表情/符号，分词后无有效词，无法分析
            ok = False
            label = "内容-无有效词"
        elif kind in ("core", "link", "soft"):
            label = "内容-主题词"
            ok = True
        elif kind == "candidate":
            sc = rec["score"]
            th = PLATFORM_SIM_TH.get(r["platform"], SIM_TH)
            ok = bool(sc != "" and float(sc) >= th)
            label = "内容-相似度"
        else:
            ok = False
            label = "内容-未命中"
        #父级兜底：单条判不出来，但所属帖子/视频整体是相关的
        if not ok and rec["tokens"]:
            if parent_judge.get((r["platform"], r["pid"])):
                ok = True
                label = "父级继承"

        stat[(r["platform"], "keep" if ok else "drop")] += 1
        stat[(r["platform"], r["kw"] or "-", "keep" if ok else "drop")] += 1
        audit_rows.append({
            "platform": r["platform"],
            "keyword": r["kw"],
            "judge": label,
            "score": rec["score"],
            "hit_words": rec["hit"],
            "parent_ok": "Y" if parent_judge.get((r["platform"], r["pid"])) else "N",
            "text": (r["text"] or "")[:80].replace("\n", " ").replace("\r", " "),
        })
        if ok or AUDIT_ONLY:
            kept.append({
                "platform": r["platform"],
                "date": r["date"],
                "text": r["text"],
                "author": r.get("author", ""),
                "like_count": r.get("like_count", ""),
                "comment_count": r.get("comment_count", ""),
                "extra_count": r.get("extra_count", ""),
            })

    return kept, audit_rows, stat


#--------------- 极短/无有效词过滤（大纲3.2：去广告/灌水短文本） ---------------
MIN_TEXT_LEN = 2


def is_too_short(text):
    """极短或无有效词：空串/单字，或分词后有效词为 0。两字表态（支持/反对等）保留。"""
    clean = re.sub(r"\s+", "", text or "")
    if len(clean) < MIN_TEXT_LEN:
        return True
    if not tokenize(text):
        return True
    return False


def short_filter(kept):
    """剔除极短/无有效词记录，返回(保留列表, 按平台剔除计数)。"""
    passed, dropped = [], collections.Counter()
    for r in kept:
        if is_too_short(r["text"]):
            dropped[r["platform"]] += 1
        else:
            passed.append(r)
    return passed, dropped


#--------------- 去重合并（大纲3.1：完全重复文本仅保留一条，记录重复次数） ---------------
def _norm_text(text):
    """去首尾空白、把连续空白压成一个空格，作为去重分组键。"""
    return re.sub(r"\s+", " ", (text or "").strip())


def dedup_corpus(kept):
    """按归一化文本合并重复记录。
    同一文本保留一条代表记录（有日期中最早的一条；无日期兜底首条），
    repeat_count 记录该文本在审核通过语料中出现的总次数（1=唯一）。
    """
    groups = {}
    order = []
    for r in kept:
        norm = _norm_text(r["text"])
        if not norm:
            continue
        if norm not in groups:
            groups[norm] = []
            order.append(norm)
        groups[norm].append(r)

    out = []
    for norm in order:
        recs = groups[norm]
        dated = [x for x in recs if x["date"]]
        rep = min(dated, key=lambda x: x["date"]) if dated else recs[0]
        out.append({
            "platform": rep["platform"],
            "date": rep["date"],
            "text": rep["text"],
            "author": rep.get("author", ""),
            "like_count": rep.get("like_count", ""),
            "comment_count": rep.get("comment_count", ""),
            "extra_count": rep.get("extra_count", ""),
            "repeat_count": len(recs),
        })
    return out


#--------------- 词频统计 ---------------
def word_freq(rows):
    counter = collections.Counter()
    for r in rows:
        for w in tokenize(r["text"]):
            counter[w] += 1
    return counter


#--------------- TF-IDF关键词提取（PPT第8章文本表示） ---------------
def tfidf_keywords(rows, top_k=20):
    """按月份窗口对文本做TF-IDF，提取每个时间窗口的高权重关键词"""
    month_docs = collections.OrderedDict()
    for r in rows:
        if not r["date"]:
            continue
        month_docs.setdefault(r["date"][:7], []).append(r["text"])
    result = []
    for month, texts in month_docs.items():
        if len(texts) < 3:                 #文本过少不计算
            continue
        segs = [" ".join(tokenize(t)) for t in texts]
        try:
            vec = TfidfVectorizer(token_pattern=r"\S+", max_features=500)
            X = vec.fit_transform(segs)
            names = vec.get_feature_names_out()
            mean_w = X.mean(axis=0).A1
            order = mean_w.argsort()[::-1][:top_k]
            words = [names[i] for i in order]
            result.append({"month": month, "doc_count": len(texts), "keywords": " ".join(words)})
        except Exception:
            continue
    return result


#--------------- 时间分布 ---------------
def time_distribution(rows):
    counter = collections.Counter()
    for r in rows:
        month = r["date"][:7] if r["date"] else "unknown"
        counter[month] += 1
    return sorted(counter.items(), key=lambda x: x[0])


def main():
    os.makedirs(PROCESS_DIR, exist_ok=True)
    rows, stats = load_and_merge()
    if not rows:
        print("【提示】未在 result/ 下找到有效采集数据（采集csv为空或缺失）。")
        return

    print("时间窗：%s ~ %s（窗外剔除，无时间字段保留仅计词频）" % (DATE_START, DATE_END))
    for s in stats:
        print("  %-30s 有效文本 %6d 条 | 保留 %6d 条 | 窗外剔除 %6d 条"
              % (s["file"], s["total"], s["kept"], s["outside"]))
    print("时间窗内合计：%d 条" % len(rows))

    #1. 主题相关性审核
    print("\n[主题审核] 按文本内容判定相关性（不按关键词标签删除）…")
    print("  相似度阈值：默认 %.2f；小红书 %.2f（样本量最少、评论极短，单独放宽，并放宽父级判定）"
          % (SIM_TH, PLATFORM_SIM_TH.get("xiaohongshu", SIM_TH)))
    kept, audit_rows, stat = topic_audit(rows)

    # 极短/无有效词过滤：审核通过后、去重前剔除
    passed, short_drop = short_filter(kept)
    print("\n[短文本过滤] 剔除极短/无有效词 %d 条" % sum(short_drop.values()))

    # 去重合并：归一化文本相同的记录合并为一条，repeat_count 记录出现次数
    deduped = dedup_corpus(passed)
    print("[去重合并] 过滤后 %d 条 -> 去重后 %d 条（合并重复 %d 条）"
          % (len(passed), len(deduped), len(passed) - len(deduped)))

    audit_path = os.path.join(PROCESS_DIR, AUDIT_FILE)
    with open(audit_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["platform", "keyword", "judge",
                                               "score", "hit_words", "parent_ok", "text"])
        writer.writeheader()
        writer.writerows(audit_rows)
    print("  审核明细 %d 条 -> %s" % (len(audit_rows), audit_path))

    #2. 按平台 / 按原关键词标签统计保留率
    stats_path = os.path.join(PROCESS_DIR, STATS_FILE)
    platforms = [p for p, *_ in PLATFORM_SOURCES]
    with open(stats_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["platform", "keyword", "total", "kept", "dropped", "keep_rate"])
        for p in platforms:
            k = stat[(p, "keep")]
            d = stat[(p, "drop")]
            writer.writerow([p, "(全部)", k + d, k, d,
                             "%.2f%%" % (100.0 * k / (k + d)) if (k + d) else "0.00%"])
            kws = sorted({key[1] for key in stat.keys()
                          if len(key) == 3 and key[0] == p})
            for kw in kws:
                kk = stat[(p, kw, "keep")]
                dd = stat[(p, kw, "drop")]
                writer.writerow([p, kw, kk + dd, kk, dd,
                                 "%.2f%%" % (100.0 * kk / (kk + dd)) if (kk + dd) else "0.00%"])
    print("  平台/标签统计 -> %s" % stats_path)

    for p in platforms:
        k = stat[(p, "keep")]
        d = stat[(p, "drop")]
        if k + d:
            print("  %-13s 相关 %6d / %6d（保留 %.1f%%）" % (p, k, k + d, 100.0 * k / (k + d)))
    total_keep = sum(stat[(p, "keep")] for p in platforms)
    print("  合计：相关 %d / %d（%.1f%%）%s" % (
        total_keep, len(rows), 100.0 * total_keep / len(rows) if rows else 0,
        "（仅标记模式，未剔除）" if AUDIT_ONLY else ""))

    #3. 合并语料（去重后，带重复次数列 repeat_count）
    merge_path = os.path.join(PROCESS_DIR, MERGE_FILE)
    with open(merge_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["platform", "date", "text", "author",
                                               "like_count", "comment_count", "extra_count",
                                               "repeat_count"])
        writer.writeheader()
        writer.writerows(deduped)
    print("\n合并语料：共 %d 条 -> %s" % (len(deduped), merge_path))

    #4. 词频统计（频次门槛 MIN_FREQ，低频碎片不进词频表）
    MIN_FREQ = 2
    freq = word_freq(deduped)
    freq_path = os.path.join(PROCESS_DIR, FREQ_FILE)
    kept_freq = [(w, c) for w, c in freq.most_common() if c >= MIN_FREQ]
    with open(freq_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["word", "freq"])
        for w, c in kept_freq[:200]:
            writer.writerow([w, c])
    print("词频统计：%d 个不同词（频次≥%d，写入 Top200），写入 %s"
          % (len(freq), MIN_FREQ, freq_path))

    #5. TF-IDF关键词
    kws = tfidf_keywords(deduped)
    tfidf_path = os.path.join(PROCESS_DIR, TFIDF_FILE)
    with open(tfidf_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["month", "doc_count", "keywords"])
        writer.writeheader()
        writer.writerows(kws)
    print("TF-IDF关键词：覆盖 %d 个时间窗口，写入 %s" % (len(kws), tfidf_path))

    #6. 时间分布
    dist = time_distribution(deduped)
    time_path = os.path.join(PROCESS_DIR, TIME_FILE)
    with open(time_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["month", "count"])
        writer.writerows(dist)
    print("时间分布：跨 %d 个月，写入 %s" % (len(dist), time_path))

    no_time = sum(c for m, c in dist if m == "unknown")
    if no_time:
        print("无时间字段记录：%d 条（仅计入词频，未参与时序与分月TF-IDF）" % no_time)
    timed = [(m, c) for m, c in dist if m != "unknown"]
    if timed:
        peak = max(timed, key=lambda x: x[1])
        print("文本量最高月份：%s（%d条，可能为舆情高峰）" % (peak[0], peak[1]))

    #7. 清洗阶段统计（交接说明回填用：各平台各阶段剔除/保留数量）
    final_by_platform = collections.Counter(r["platform"] for r in deduped)
    clean_path = os.path.join(PROCESS_DIR, CLEAN_FILE)
    stat_by_plat = {s["platform"]: s for s in stats}
    with open(clean_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["platform", "raw_total", "outside_window", "audit_keep",
                          "audit_drop", "short_drop", "dedup_merged", "final_keep"])
        sums = collections.Counter()
        for p in platforms:
            s = stat_by_plat.get(p, {"total": 0, "outside": 0})
            ak, ad = stat[(p, "keep")], stat[(p, "drop")]
            sd = short_drop.get(p, 0)
            fk = final_by_platform.get(p, 0)
            merged = ak - sd - fk
            writer.writerow([p, s["total"], s["outside"], ak, ad, sd, merged, fk])
            sums["raw"] += s["total"]; sums["out"] += s["outside"]
            sums["ak"] += ak; sums["ad"] += ad; sums["sd"] += sd
            sums["mg"] += merged; sums["fk"] += fk
        writer.writerow(["合计", sums["raw"], sums["out"], sums["ak"], sums["ad"],
                         sums["sd"], sums["mg"], sums["fk"]])
    print("清洗阶段统计 -> %s" % clean_path)


if __name__ == "__main__":
    main()
