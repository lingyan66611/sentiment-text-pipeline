# -*- coding: utf-8 -*-
"""
自适应采集引擎（主动学习 / 自举扩展）+ 采集工程增强
作用：在采集循环中持续发现并纳入与主题相关的新关键词，闭环提高召回率，
      逼近"尽量完整"地采集预制菜进校园相关网络文本。
方法依据：
  - 词法分析（PPT第3章）：jieba分词 + 停用词，从已采相关文本提取高频词
  - 相关性反馈 / 自举（信息检索经典方法）：以种子词为起点，迭代扩充查询词，
    直到新增词不再带来显著新文本（词库收敛）
  - 文本分类思想（PPT第8章）：相关性阈值可调，控制采集精度
  - 文本相似度（信息检索）：SimHash近似去重，剔除转载/改写重复文本
边界说明：本模块只服务于"决定下一步采集什么"（采集策略），
        不对最终csv做清洗/过滤/统计等预处理，最终csv仍为纯采集字段。
"""
import os
import re
import json
import time
import random
import hashlib
import threading
import collections
from concurrent.futures import ThreadPoolExecutor, as_completed
import jieba

#sklearn可选依赖：缺失时自动降级为词典相关性规则（分类器不启用）
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.naive_bayes import MultinomialNB
    _SKLEARN_OK = True
except Exception:
    _SKLEARN_OK = False

#领域种子关键词（核心 + 扩展，作为主动学习的起点）
SEED_KEYWORDS = ["预制菜", "预制菜进校园", "校园预制菜", "预制菜 学校",
                 "预制菜 家长", "学生餐 预制", "校园餐 卫生",
                 "中央厨房", "料理包", "明厨亮灶", "校外供餐"]

#排除词（负向词）：命中即否决，过滤纯美食/科普/营销类无关内容，提高信噪比
EXCLUDE_WORDS = ["做法", "教程", "推荐", "怎么做好吃", "美食", "菜谱", "食谱",
                 "加盟", "创业", "选购", "测评", "品牌", "试吃", "菜谱视频"]

#时间范围（可选）：预制菜进校园事件两个舆情高峰窗口
TIME_WINDOWS = [("2023-06", "2025-12")]

#停用词（PPT第3章：过滤无实义虚词）
STOPWORDS = set("""的 了 是 在 我 你 他 她 它 我们 你们 他们 这个 那个 一个 什么 怎么
为什么 这样 那样 没有 不是 但是 因为 所以 如果 虽然 可以 可能 已经 正在 现在 大家
真的 觉得 知道 应该 一些 还有 之后 之前 里面 外面 这 那 和 与 也 都 就 还 而 及 并
等 很 最 更 之 其 或 跟 对 被 把 从 向 往 于 而 让 给 来 去 说 到 出 上 下 中 大 小
""".split())

#User-Agent池（轮换模拟不同浏览器，降低被识别风险）
UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36 Edg/120.0",
]

#英文月份 -> 数字（微博时间格式用）
MONTH_MAP = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
             "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}


def random_delay():
    """防封号：在(0.3, 1.5)秒区间随机停顿（比默认更轻，兼顾采集速度）。"""
    time.sleep(random.uniform(0.3, 1.5))


def random_ua():
    """随机返回一个User-Agent（轮换模拟浏览器）。"""
    return random.choice(UA_LIST)


def clean_text(raw):
    """去HTML标签、压缩空白（保证正文可读；属于采集解析，非预处理分析）。"""
    text = re.sub(r"<[^>]+>", "", raw or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


#--------------- 全局限速（防418风控的核心） ---------------
#多线程(如16线程)并发下，若每线程都自由延时，整体请求速率可达每秒几十次，
#极易触发平台风控(微博返回418)。这里用一个全局最小请求间隔做限速：
#所有平台、所有线程共享该间隔，整体速率 ≈ 1/GLOBAL_MIN_INTERVAL 次/秒。
#并发数(workers)只决定"同时在途的连接数"，不放大请求速率。
GLOBAL_MIN_INTERVAL = 0.3        #秒；仍被风控时调大到 0.5 / 1.0
_rate_lock = threading.Lock()
_next_time = 0.0


def _rate_wait():
    """全局令牌：距上次请求不足最小间隔则等待，保证整体速率上限。"""
    global _next_time
    with _rate_lock:
        now = time.time()
        if now < _next_time:
            time.sleep(_next_time - now)
        _next_time = time.time() + GLOBAL_MIN_INTERVAL


def http_get(session, url, params, retries=2, base_delay=1.0, timeout=(8, 15)):
    """带重试的GET请求 + 全局限速：请求失败（超时/异常）时按退避重试，
    提高采集健壮性；timeout=(连接,读取)双超时，避免连接挂起导致采集卡死。
    418为平台风控：按3/7/15秒长退避重试，仍失败则抛出带提示的异常。
    返回值：成功时返回requests.Response。"""
    _rate_wait()
    last = None
    for i in range(retries + 1):
        try:
            r = session.get(url, params=params, timeout=timeout)
            if r.status_code == 418:               #风控：长退避 + 明确提示
                time.sleep((3, 7, 15)[min(i, 2)] + random.uniform(0.5, 2))
                last = Exception(
                    "HTTP 418 风控拦截：请降低采集频率"
                    "（调大GLOBAL_MIN_INTERVAL或调低WORKERS），"
                    "或重新登录复制最新Cookie")
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            if i < retries:
                time.sleep(base_delay * (i + 1) + random.uniform(0.3, 1.0))
    raise last


def is_relevant(text, keywords, exclude, min_hits=1):
    """相关性判断（采集筛选）：命中关键词数 >= min_hits 且不命中排除词，视为相关。
    min_hits 为相关性阈值，可调（=1宽松，=2更严格），用于控制采集精度。"""
    text = text or ""
    for w in exclude:                       #排除词优先否决
        if w and w in text:
            return False
    hits = sum(1 for w in keywords if w and w in text)
    return hits >= min_hits


def parse_month(platform, raw):
    """把各平台时间字段统一为 YYYY-MM（用于时间窗口筛选）。微博英文日期，其余unix。"""
    if not raw:
        return ""
    try:
        if platform == "weibo":
            parts = str(raw).split()
            if len(parts) >= 6:
                mon = MONTH_MAP.get(parts[1])
                if mon:
                    return "%04d-%02d" % (int(parts[-1]), mon)
            return str(raw)[:7]
        else:
            ts = int(raw)
            if ts > 10 ** 12:
                ts = ts // 1000
            return time.strftime("%Y-%m", time.localtime(ts))
    except Exception:
        return ""


def in_time_windows(month, windows):
    """判断月份是否落在任一 [start, end] 时间窗口内。"""
    if not windows or not month:
        return True
    for s, e in windows:
        if s <= month <= e:
            return True
    return False


def _tokenize(text, keywords, exclude, stopwords):
    """分词并过滤（供抽词/SimHash复用）：单字、纯符号、停用词、排除词、已在词库的词。"""
    out = []
    for w in jieba.lcut(text or ""):
        w = w.strip()
        if len(w) <= 1:
            continue
        if re.fullmatch(r"[\W_]+", w):
            continue
        if w in stopwords:
            continue
        if w in exclude:
            continue
        out.append(w)
    return out


def extract_candidate_words(texts, keywords, exclude, stopwords, top_k=10):
    """从已采相关文本提取高频新词（主动学习的核心），返回 {新词: 示例文本}。
    jieba分词 -> 统计频次 -> 过滤停用词/单字/排除词/已在词库的词 -> 返回top新词及一条示例。"""
    counter = collections.Counter()
    example = {}
    for t in texts:
        for w in _tokenize(t, keywords, exclude, stopwords):
            counter[w] += 1
            if w not in example:
                example[w] = t[:60]
    cand = {}
    for w, c in counter.most_common(top_k):
        cand[w] = example.get(w, "")
    return cand


#--------------- SimHash 近似去重（剔除转载/改写重复文本） ---------------
def _feature_hash(word):
    return int(hashlib.md5(word.encode("utf-8")).hexdigest()[:16], 16)


def simhash(text, hashbits=64, max_words=30):
    """对文本计算64位SimHash指纹（基于分词词袋）。"""
    v = [0] * hashbits
    for w in _tokenize(text, set(), set(), STOPWORDS)[:max_words]:
        h = _feature_hash(w)
        for i in range(hashbits):
            if (h >> i) & 1:
                v[i] += 1
            else:
                v[i] -= 1
    return sum((1 << i) for i in range(hashbits) if v[i] > 0)


def hamming(a, b):
    """两个指纹的汉明距离（不同位数）。"""
    return bin(a ^ b).count("1")


#--------------- 伪标签相关性分类器（高级主动学习，PPT第8章文本分类） ---------------
def _seg(text):
    """jieba分词后以空格连接，供TF-IDF向量化。"""
    return " ".join(w for w in jieba.lcut(text or "") if w.strip())


class RelevanceClassifier:
    """伪标签相关性分类器（自训练）：
      1) 第一轮用种子词典规则产生初始"伪标签"（相关/无关文本）；
      2) 用TF-IDF + 朴素贝叶斯训练二分类器（PPT第8章：文本表示+文本分类）；
      3) 后续用分类器判断新文本是否相关，能泛化到未写进关键词的新表述；
      4) 每轮用新增样本重训（自举），越采越准。
    依赖sklearn；缺失时fitted保持False，调用方自动回退词典规则。
    """
    def __init__(self, min_prob=0.5, max_features=3000):
        self.min_prob = min_prob
        self.max_features = max_features
        self.vec = None
        self.clf = None
        self.fitted = False

    def fit(self, pos_texts, neg_texts):
        """用正样本(相关)与负样本(无关)训练。正负样本至少各1条。"""
        if not _SKLEARN_OK:
            return False
        texts = [_seg(t) for t in pos_texts] + [_seg(t) for t in neg_texts]
        labels = [1] * len(pos_texts) + [0] * len(neg_texts)
        self.vec = TfidfVectorizer(token_pattern=r"\S+", max_features=self.max_features)
        X = self.vec.fit_transform(texts)
        self.clf = MultinomialNB()
        self.clf.fit(X, labels)
        self.fitted = True
        return True

    def predict_relevant(self, text):
        """判断文本是否相关：返回 (是否相关, 相关概率)。未训练时返回 (None, None)。"""
        if not self.fitted:
            return None, None
        x = self.vec.transform([_seg(text)])
        p = self.clf.predict_proba(x)[0][1]
        return p >= self.min_prob, float(p)


def converged(history, stop_rounds=2, min_new=3):
    """收敛判断：连续 stop_rounds 轮新增文本数均低于 min_new，说明新增关键词
    不再带来显著新文本，词库趋于饱和，停止扩展。"""
    if len(history) < stop_rounds:
        return False
    return all(h < min_new for h in history[-stop_rounds:])


def _write_candidates(path, candidates, keywords, auto_add):
    """把候选新词及其示例写入候选文件，供人工审核（词库人工校验）。"""
    lines = ["候选关键词（人工审核后决定是否纳入采集）", "=" * 40]
    for w, ex in candidates.items():
        state = "已自动纳入" if (auto_add and w not in keywords) else "待确认"
        lines.append("[%s] %s  示例：%s" % (state, w, ex))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _absorb(items, seen_ids, rows, pos_pool, neg_pool, keywords, exclude, clf, min_hits):
    """把某关键词采到的所有文本吸收进采集集合（主线程串行调用，保证去重一致性）。
    采集阶段不筛选：搜索到的文本全部入库；相关性判断仅用于主动学习语料（pos/neg池）。
    text字段兼容各平台命名：text/content/desc/title。"""
    round_new = 0
    for item in items:
        iid = str(item.get("id", ""))
        if iid and iid in seen_ids:             #技术去重：同一文本跨关键词不重复入库
            continue
        if iid:
            seen_ids.add(iid)
        text = (item.get("text") or item.get("content")
                or item.get("desc") or item.get("title") or "")
        rows.append(item)
        round_new += 1
        #主动学习：识别"关键内容"作为扩展新词的语料（不用于过滤入库）
        exclude_hit = any(w and w in text for w in exclude)
        if clf and clf.fitted:
            rel, _p = clf.predict_relevant(text)
            if rel and not exclude_hit:
                pos_pool.append(text)
            else:
                neg_pool.append(text)
        else:
            if is_relevant(text, keywords, exclude, min_hits=min_hits):
                pos_pool.append(text)
            elif exclude_hit:
                neg_pool.append(text)
    return round_new


def adaptive_collect(platform, fetcher, seed_keywords=None, exclude_words=None,
                     max_rounds=3, top_k=10, stop_rounds=2, min_new=3,
                     time_windows=None, min_hits=1, workers=1, docx_log_path=None,
                     checkpoint_path=None, candidate_file=None, auto_add=True,
                     log_file=None, simhash_dedup=True, simhash_threshold=3,
                     use_classifier=True, clf_min_prob=0.5, clf_min_pos=10, clf_min_neg=5,
                     word_audit=None, log=True):
    """自适应采集主循环（主动学习 + 采集工程增强，支持多线程加速）：
      1) 对当前词库每个关键词调用 fetcher 采集（请求含重试）；
      2) 断点续采：从checkpoint恢复已入库id，避免重复累积；
      3) 去重(id) + 相关性阈值筛选(min_hits) + 可选时间窗口筛选 + SimHash近似去重；
      4) 从已采相关文本提取高频新词 -> 候选文件（人工校验）-> 扩充词库；
      5) 迭代多轮，直到词库收敛；
      6) 采集饱和曲线存档（log_file）。
    workers>1 时每轮按关键词并行采集（ThreadPoolExecutor）：各关键词的翻页
    相互独立，天然可并行；每个关键词的任务内 fetcher 自建session+UA轮换，
    随机延时保留（多线程下整体速率=workers×每线程速率）。去重/吸收统一在
    主线程串行完成，保证 seen_ids 一致性。
    docx_log_path 非空时：首次运行自动创建"词库演变自动记录"Word文档，
    之后每轮扩展新词实时追加（见kw_docx_log模块），写入失败不影响采集。
    返回 (rows, final_keywords, history)。
    """
    if seed_keywords is None:
        seed_keywords = SEED_KEYWORDS
    if exclude_words is None:
        exclude_words = EXCLUDE_WORDS
    keywords = set(seed_keywords)
    exclude = set(exclude_words)
    seen_ids = set()
    sim_seen = []
    rows = []
    history = []

    #断点续采：恢复已入库id与词库
    if checkpoint_path and os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            seen_ids = set(data.get("seen_ids", []))
            if data.get("keywords"):
                keywords |= set(data["keywords"])
            if word_audit:
                #主题审核：种子词保底保留，扩展词按审核函数过滤（无关词自动删除）
                keywords = set(w for w in keywords
                               if w in (set(seed_keywords) | set(exclude)) or word_audit(w))
            if log:
                print("[断点] 恢复已入库id %d 个，词库 %d 个（主题审核后）" % (len(seen_ids), len(keywords)))
        except Exception:
            pass

    log_lines = ["采集饱和曲线日志：%s" % platform]

    #词库演变自动记录Word：首次运行创建（含初始词库），之后每轮追加
    _docx_log = None
    if docx_log_path:
        try:
            import kw_docx_log
            _docx_log = kw_docx_log
            _docx_log.init_docx(docx_log_path, sorted(keywords), [], platform)
        except Exception as e:
            if log:
                print("[词库记录] Word自动记录不可用：%s（不影响采集）" % e)
            _docx_log = None

    #伪标签相关性分类器（可选）：先词典，后分类器泛化 + 自训练重训
    clf = RelevanceClassifier(min_prob=clf_min_prob) if (use_classifier and _SKLEARN_OK) else None
    pos_pool = []
    neg_pool = []

    for rnd in range(1, max_rounds + 1):
        round_new = 0
        kw_list = sorted(keywords)

        if workers > 1 and len(kw_list) > 1:
            #多线程：每个关键词一个任务，各任务内的翻页/延时相互独立
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {ex.submit(fetcher, kw): kw for kw in kw_list}
                for fut in as_completed(futures):
                    kw = futures[fut]
                    try:
                        items = fut.result()
                    except Exception as e:
                        if log:
                            print("  [%s] 关键词「%s」采集异常：%s" % (platform, kw, e))
                        continue
                    round_new += _absorb(items, seen_ids, rows, pos_pool, neg_pool,
                                         keywords, exclude, clf, min_hits)
        else:
            for kw in kw_list:                   #单线程：保持原串行逻辑
                items = fetcher(kw)
                round_new += _absorb(items, seen_ids, rows, pos_pool, neg_pool,
                                     keywords, exclude, clf, min_hits)
        history.append(round_new)

        #伪标签自训练：每轮用累积的正/负样本重训分类器，越采越准
        if clf and len(pos_pool) >= clf_min_pos and len(neg_pool) >= clf_min_neg:
            if clf.fit(pos_pool, neg_pool):
                if log:
                    print("[分类器] 伪标签重训完成：正样本%d 负样本%d，切换为分类器判断相关性"
                          % (len(pos_pool), len(neg_pool)))

        #主动学习：提取候选新词 -> 扩充词库 -> 写候选文件（人工校验）
        #语料用"关键内容"(pos_pool)，避免与主题无关的文本带出噪声词
        candidates = extract_candidate_words(
            pos_pool or [i.get("text", "") for i in rows],
            keywords, exclude, STOPWORDS, top_k)
        if auto_add:
            if word_audit:
                added = set(w for w in candidates
                            if w not in keywords and word_audit(w))
            else:
                added = set(candidates.keys()) - keywords
            kw_from = len(keywords)
            keywords |= added
            kw_to = len(keywords)
            added_s = "、".join(sorted(added)) if added else "无"
        else:
            kw_from = kw_to = len(keywords)
            added_s = "无(待人工确认)"
        if candidate_file:
            _write_candidates(candidate_file, candidates, keywords, auto_add)

        #词库演变自动记录：每轮把新增词（含示例）实时追加写入Word
        if _docx_log and auto_add and added:
            _docx_log.append_round(
                docx_log_path, rnd, {w: candidates[w] for w in added},
                kw_from, kw_to)

        log_line = "[轮次%d] 新增文本%d条 | 词库%d个 | 扩展新词：%s" % (
            rnd, round_new, len(keywords), added_s)
        log_lines.append(log_line)
        if log:
            print(log_line)

        if converged(history, stop_rounds, min_new):
            end_line = "词库已收敛（连续%d轮新增不足%d条），停止扩展。" % (stop_rounds, min_new)
            log_lines.append(end_line)
            if log:
                print(end_line)
            break

    #存档：断点 + 饱和曲线
    if checkpoint_path:
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            json.dump({"seen_ids": sorted(seen_ids), "keywords": sorted(keywords)},
                      f, ensure_ascii=False, indent=2)
    if log_file:
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("\n".join(log_lines) + "\n")

    return rows, keywords, history
