# -*- coding: utf-8 -*-
"""
微博语料预处理流水线（采集完成 -> 预处理 -> 干净语料交接给情感分析）
对应课程PPT方法：
  - PPT第3章《词法分析》：文本规范化（去HTML/话题/@/空白）、过滤过短噪声
  - PPT第8章《文本分类》：主题相关性二分类，剔除采集混入的无关文本
  - 时序基础：时间统一格式（YYYY-MM-DD）+ 时间窗口过滤（2023-06-01 ~ 2025-12-31）
输入：result/weibo_comments.csv（采集原始语料，14807条）
输出（写入result/）：
  - weibo_cleaned.csv        清洗、去重、主题筛选、时间规整后的干净语料
  - clean_log_weibo.txt      各步处理数量日志（交接凭证）
"""
import os
import csv
import datetime
import data_cleaner as dc

RESULT_DIR = r"E:\learn\自然语言处理\大作业\result"
SRC_CSV = os.path.join(RESULT_DIR, "weibo_comments.csv")
OUT_CSV = os.path.join(RESULT_DIR, "weibo_cleaned.csv")
LOG_TXT = os.path.join(r"E:\learn\自然语言处理\大作业", "clean_log_weibo.txt")

TIME_START = "2023-06-01"   # 舆情起爆点：教育部回复预制菜进校园争议（时间窗口下界）
TIME_END = "2025-12-31"     # 采集截止（时间窗口上界）

#微博英文月份 -> 数字
MONTH_MAP = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
             "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}


def parse_time(raw):
    """微博created_at（如 Wed Sep 20 12:00:00 +0800 2023）-> YYYY-MM-DD。"""
    if not raw:
        return ""
    parts = raw.split()
    if len(parts) >= 6:
        mon = MONTH_MAP.get(parts[1])
        if mon:
            try:
                day = int(parts[2])
                year = int(parts[-1])
                return "%04d-%02d-%02d" % (year, mon, day)
            except Exception:
                return ""
    return raw[:10]


def in_window(date_str):
    if not date_str:
        return False
    try:
        return TIME_START <= date_str <= TIME_END
    except Exception:
        return False


def main():
    if not os.path.exists(SRC_CSV):
        print("未找到原始语料：%s" % SRC_CSV)
        return
    with open(SRC_CSV, "r", encoding="utf-8-sig", newline="") as f:
        recs = list(csv.DictReader(f))
    raw_count = len(recs)

    #1) 清洗（PPT第3章：规范化）+ 2) 去重（按微博id）
    cleaned = []
    seen = set()
    for r in recs:
        text = dc.clean_text(r.get("text") or "")
        if not text:
            continue
        wid = (r.get("weibo_id") or "").strip()
        if wid and wid in seen:
            continue
        if wid:
            seen.add(wid)
        cleaned.append((r, text))
    dedup_count = len(cleaned)

    #3) 过滤过短文本（词法分析：无意义噪声）
    kept_len = [x for x in cleaned if not dc.is_short(x[1])]
    dropped_short = len(cleaned) - len(kept_len)

    #4) 主题相关性筛选（PPT第8章：文本分类，剔除无关文本）
    relevant = [x for x in kept_len if dc.is_relevant(x[1])]
    dropped_noise = len(kept_len) - len(relevant)

    #5) 时间规整 + 时间窗口过滤（时序预处理）
    rows_out = []
    dropped_time = 0
    for r, text in relevant:
        date = parse_time(r.get("created_at") or "")
        if not in_window(date):
            dropped_time += 1
            continue
        rows_out.append({
            "platform": "weibo",
            "weibo_id": r.get("weibo_id", ""),
            "user": r.get("user", ""),
            "date": date,
            "text": text,
            "reposts_count": r.get("reposts_count", ""),
            "comments_count": r.get("comments_count", ""),
            "likes_count": r.get("likes_count", ""),
        })
    rows_out.sort(key=lambda x: x["date"])

    #输出干净语料
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        writer.writeheader()
        writer.writerows(rows_out)

    #输出清洗日志（交接凭证）
    lines = [
        "微博语料预处理日志（PPT第3/6/8章方法）",
        "=" * 40,
        "采集原始条数：%d" % raw_count,
        "清洗+去重后条数：%d" % dedup_count,
        "过滤过短文本：%d" % dropped_short,
        "过滤无关文本（主题相关性分类）：%d" % dropped_noise,
        "时间窗口外剔除（%s ~ %s）：%d" % (TIME_START, TIME_END, dropped_time),
        "最终保留条数：%d" % len(rows_out),
        "时间覆盖：%s ~ %s" % (rows_out[0]["date"], rows_out[-1]["date"]),
        "=" * 40,
    ]
    with open(LOG_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("\n".join(lines))
    print("干净语料已输出：%s" % OUT_CSV)


if __name__ == "__main__":
    main()
