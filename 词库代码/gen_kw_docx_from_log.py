# -*- coding: utf-8 -*-
"""
词库演变记录自动生成脚本（无需重新采集）
作用：从已有的真实采集结果（日志词库）自动重建
      "词库演变记录_自动.docx"，文档只包含词库内容：
      初始查找词库 + 主动学习新增词（按轮次）+ 汇总词库。
用法：python gen_kw_docx_from_log.py
"""
import os
import bootstrapper as boot
import collect_weibo as cw
import kw_docx_log

OUT_DOCX = os.path.join(r"E:\learn\自然语言处理\大作业\_work_词库记录", "词库演变记录_自动.docx")

#新增词与轮次归属（来自两次真实采集日志，勿改）：
# 首次全量采集 第1轮 +10（词库20→30）；断点续采 轮1 +4、轮2 +1、轮3 +1
NEW_WORDS_ROUNDS = [
    (1, ["315", "上海", "全文", "学生", "孩子", "家长", "希望", "西贝", "进校园", "预制"], 20, 30),
    (1, ["午餐", "反对", "学校", "问题"], 30, 34),
    (2, ["视频"], 34, 35),
    (3, ["微博"], 35, 36),
]


def main():
    #初始词库（与采集脚本一致）：种子关键词11 + 话题标签9 = 20
    seed = list(boot.SEED_KEYWORDS) + list(cw.TOPIC_TAGS)
    kw_docx_log.init_docx(OUT_DOCX, seed, [], "weibo")
    #逐轮追加新增词（只写词，不带示例）
    for rnd, words, kw_from, kw_to in NEW_WORDS_ROUNDS:
        kw_docx_log.append_round(
            OUT_DOCX, rnd, {w: "" for w in words}, kw_from, kw_to)
    total = len(seed) + sum(len(w) for _, w, _, _ in NEW_WORDS_ROUNDS)
    print("已生成：%s" % OUT_DOCX)
    print("初始词库 %d 个 + 主动学习新增 %d 个 = 最终 %d 个" % (
        len(seed),
        sum(len(w) for _, w, _, _ in NEW_WORDS_ROUNDS), total))


if __name__ == "__main__":
    main()
