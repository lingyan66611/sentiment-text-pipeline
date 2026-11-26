# -*- coding: utf-8 -*-
"""
词库演变自动记录模块
作用：把采集程序运行过程中的词库扩展结果，实时自动追加写入Word文档，
      无需人工维护；每轮主动学习新增关键词后调用 append_round() 自动落盘。
用法（在采集脚本中）：
    import kw_docx_log
    kw_docx_log.init_docx(PATH, seed_keywords, topic_tags, platform)
    kw_docx_log.append_round(PATH, rnd, {新词: 示例文本}, kw_from, kw_to)
说明：文档首次运行时自动创建（含标题、说明、初始词库），之后每轮追加；
      写入失败不影响采集主流程（内部try/except）。
"""
import os
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn

BLACK = RGBColor(0, 0, 0)


def _set_run(run, cn='宋体', size=12, bold=False):
    """统一设置run字体（中文字体走eastAsia，西文Times New Roman）。"""
    run.font.name = 'Times New Roman'
    run._element.rPr.rFonts.set(qn('w:eastAsia'), cn)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = BLACK


def _add_title(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(6)
    _set_run(p.add_run(text), '黑体', 18, bold=True)
    return p


def _add_heading(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after = Pt(4)
    _set_run(p.add_run(text), '黑体', 14, bold=True)
    return p


def _add_body(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
    _set_run(p.add_run(text), '宋体', 12)
    return p


def init_docx(path, seed_keywords, topic_tags, platform='weibo'):
    """创建词库演变自动记录文档；已存在则跳过（保留历史，不重建）。
    内容：标题、初始查找词库（种子关键词＋话题标签）。"""
    if not path or os.path.exists(path):
        return
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin = sec.bottom_margin = sec.left_margin = sec.right_margin = Cm(2.5)
    normal = doc.styles['Normal']
    normal.font.name = 'Times New Roman'
    normal._element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
    normal.font.size = Pt(12)
    normal.font.color.rgb = BLACK

    _add_title(doc, '预制菜进校园舆情采集·词库演变自动记录')
    _add_heading(doc, '初始查找词库（共%d个）' % len(seed_keywords))
    _add_body(doc, '、'.join(seed_keywords))
    if topic_tags:
        _add_heading(doc, '微博话题标签（共%d个）' % len(topic_tags))
        _add_body(doc, '、'.join(topic_tags))
    _add_heading(doc, '主动学习扩展记录')
    doc.save(path)


def append_round(path, rnd, new_words, kw_from, kw_to):
    """把一轮主动学习新增的关键词追加写入Word文档。
    new_words: {新词: 任意}（仅取词本身，不写入示例）；
    kw_from/kw_to：扩展前后的词库数量。每个新词单独一行。"""
    if not path or not os.path.exists(path) or not new_words:
        return False
    try:
        doc = Document(path)
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(8)
        p.paragraph_format.space_after = Pt(2)
        _set_run(p.add_run('第%d轮扩展：新增%d个关键词（词库%d→%d）'
                            % (rnd, len(new_words), kw_from, kw_to)), '宋体', 12, bold=True)
        for w in sorted(new_words):
            _add_body(doc, '· %s' % w)
        doc.save(path)
        return True
    except Exception:
        return False
