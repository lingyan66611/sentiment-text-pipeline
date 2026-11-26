# -*- coding: utf-8 -*-
"""
高质量采集公共模块
融合课程PPT中的方法实现"高质量采集"：
  - PPT第8章《文本分类》：用词典/规则做主题相关性二分类过滤，剔除采集混入的无关文本
  - PPT第6章《信息抽取》：用基于规则的命名实体识别，抽取地点/机构/事件触发词，丰富结构化字段
  - PPT第3章《词法分析》：分词前的文本规范化与停用噪声过滤
  - 同时提供(0.5, 5)秒随机延时，用于防止请求过快触发平台风控封号
被 collect_weibo.py / collect_zhihu.py / collect_bilibili.py / collect_xiaohongshu.py 共同引用。
"""
import os
import re
import time
import random

#--------------- 主题相关性词典（文本分类：特征词匹配） ---------------
# 核心词：直接描述"预制菜进校园"事件本身
CORE_WORDS = ["预制菜", "进校园", "学校食堂", "学生餐", "校园餐",
              "配餐", "中央厨房", "学校", "食堂"]
# 扩展词：与事件强相关的泛化表述（用于召回绿捷等泛校园食安讨论）
EXT_WORDS = ["食品安全", "营养", "卫生", "添加剂", "防腐剂", "健康",
             "家长", "孩子", "供餐", "食材", "餐食", "绿捷", "食品安全监管"]

#--------------- 命名实体与事件触发词（信息抽取：规则识别） ---------------
# 地点模式：匹配"XX省/市/区/县/镇"
LOC_PATTERN = re.compile(r"([\u4e00-\u9fa5]{2,8}(?:省|市|区|县|镇))")
# 机构词表
ORG_WORDS = ["教育部", "教育局", "市场监管局", "市场监管部门", "公司",
             "学校", "家长委员会", "监管部门", "政府", "卫健委", "疾控"]
# 事件触发词表
EVENT_WORDS = ["回应", "禁止", "反对", "担忧", "质疑", "抵制",
               "调查", "整改", "通报", "召回", "回应", "约谈", "下架"]

MIN_LEN = 3          # 最短有效文本长度（中文词），过短视为无意义
RELEVANT_EXT_HIT = 2 # 扩展词至少命中几个才判定相关


#--------------- 防封号：随机延时 ---------------
def random_delay():
    """在(0.5, 5)秒区间随机停顿，避免请求节奏过于固定而触发平台风控。"""
    t = random.uniform(0.5, 5.0)
    time.sleep(t)
    return round(t, 2)


#--------------- 文本清洗（词法分析：规范化） ---------------
def clean_text(text):
    """去HTML标签、去话题/at符号、压缩空白，统一为干净文本。"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)          # 去HTML标签
    text = re.sub(r"#[^#]+#", "", text)          # 去#话题#符号
    text = re.sub(r"@[\u4e00-\u9fa5A-Za-z0-9_]+", "", text)  # 去@用户名
    text = re.sub(r"\s+", " ", text)             # 压缩空白
    text = text.replace("\u200b", "").strip()
    return text


#--------------- 主题相关性过滤（文本分类：二分类） ---------------
def is_short(text, min_len=MIN_LEN):
    """过短或无意义文本判定：长度不足即剔除（对应word要求的过滤短文本）。"""
    return len(text) < min_len


def is_relevant(text, core_hit=1, ext_hit=RELEVANT_EXT_HIT):
    """主题相关性二分类：
       核心词命中>=core_hit 或 扩展词命中>=ext_hit 判定为相关文本，否则过滤。"""
    core = sum(1 for w in CORE_WORDS if w in text)
    ext = sum(1 for w in EXT_WORDS if w in text)
    return (core >= core_hit) or (ext >= ext_hit)


#--------------- 命名实体抽取（信息抽取：基于规则） ---------------
def extract_entities(text):
    """抽取地点、机构、事件触发词，返回三元组(地点, 机构, 事件)。"""
    loc = ""
    m = LOC_PATTERN.search(text)
    if m:
        loc = m.group(1)
    org = ""
    for w in ORG_WORDS:
        if w in text:
            org = w
            break
    event = ""
    for w in EVENT_WORDS:
        if w in text:
            event = w
            break
    return loc, org, event


#--------------- 清洗日志（word要求：记录过滤过程） ---------------
def write_clean_log(platform, raw_count, dedup_count, kept_count, dropped_short, dropped_noise, out_dir):
    """把清洗各步数量写入 result/clean_log_<platform>.txt，作为报告第二章与交接凭证。"""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "clean_log_%s.txt" % platform)
    lines = [
        "平台：%s" % platform,
        "采集原始条数：%d" % raw_count,
        "去重后条数：%d" % dedup_count,
        "过滤过短文本：%d" % dropped_short,
        "过滤无关文本（相关性分类）：%d" % dropped_noise,
        "最终保留条数：%d" % kept_count,
        "=" * 40,
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path
