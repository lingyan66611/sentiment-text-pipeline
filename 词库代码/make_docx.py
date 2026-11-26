# -*- coding: utf-8 -*-
"""生成《预制菜进校园舆情采集关键词词库演变记录.docx》（无模板创建）。"""
import docx
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.oxml.ns import qn, nsdecls
from docx.oxml import parse_xml

TARGET = r"E:\learn\自然语言处理\大作业\预制菜进校园舆情采集词库演变记录.docx"

BLACK = RGBColor(0, 0, 0)

#--------------- 数据（与脚本/日志一致） ---------------
SEED = [
    ("预制菜", "核心词"),
    ("预制菜进校园", "事件词"),
    ("校园预制菜", "事件词"),
    ("预制菜 学校", "场景组合"),
    ("预制菜 家长", "参与者"),
    ("学生餐 预制", "场景词"),
    ("校园餐 卫生", "关注点"),
    ("中央厨房", "供给方式"),
    ("料理包", "供给方式"),
    ("明厨亮灶", "治理举措"),
    ("校外供餐", "供给方式"),
]
TOPICS = [
    "#预制菜进校园#", "#预制菜#", "#该不该抵制预制菜进校园#",
    "#预制菜可作补充但不宜作为主菜#", "#预制菜进校园有学生家长辞职送饭#",
    "#上海家长希望西贝竞标学生餐#", "#上海学生餐#", "#315预制菜#", "#西贝#",
]
NEW_WORDS = [
    ("315", "首次采集·第1轮"),
    ("上海", "首次采集·第1轮"),
    ("全文", "首次采集·第1轮"),
    ("学生", "首次采集·第1轮"),
    ("孩子", "首次采集·第1轮"),
    ("家长", "首次采集·第1轮"),
    ("希望", "首次采集·第1轮"),
    ("西贝", "首次采集·第1轮"),
    ("进校园", "首次采集·第1轮"),
    ("预制", "首次采集·第1轮"),
    ("午餐", "断点续采·第1轮"),
    ("反对", "断点续采·第1轮"),
    ("学校", "断点续采·第1轮"),
    ("问题", "断点续采·第1轮"),
    ("视频", "断点续采·第2轮"),
    ("微博", "断点续采·第3轮"),
]

#--------------- 工具函数 ---------------
def set_run(run, cn='宋体', size=12, bold=False, en='Times New Roman'):
    run.font.name = en
    run._element.rPr.rFonts.set(qn('w:eastAsia'), cn)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = BLACK

def add_para(doc, text, style='Normal'):
    p = doc.add_paragraph(style=style)
    run = p.add_run(text)
    set_run(run, '宋体', 12)
    return p

def add_heading(doc, text, level=1):
    p = doc.add_heading(text, level=level)
    for run in p.runs:
        set_run(run, '黑体', {1: 16, 2: 14, 3: 12}[level], bold=True)
    p.paragraph_format.space_before = Pt({1: 14, 2: 11, 3: 9}[level])
    p.paragraph_format.space_after = Pt({1: 6, 2: 5, 3: 4}[level])
    return p

def _insert_before(container, new_el, tag):
    """把new_el插入container中第一个tag子元素之前（保持OOXML schema顺序）。"""
    for child in container:
        if child.tag == qn(tag):
            child.addprevious(new_el)
            return
    container.append(new_el)

def shade_cell(cell, fill='D9D9D9'):
    shd = parse_xml(r'<w:shd %s w:val="clear" w:fill="%s"/>' % (nsdecls('w'), fill))
    #tcPr子元素顺序：shd须在vAlign之前
    _insert_before(cell._tc.get_or_add_tcPr(), shd, 'w:vAlign')

def set_cell(cell, text, bold=False, align=None, size=10.5):
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    p = cell.paragraphs[0]
    p.alignment = align if align else WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_before = Pt(1)
    p.paragraph_format.space_after = Pt(1)
    run = p.add_run(text)
    set_run(run, '宋体', size, bold=bold)

def set_row_no_split(row):
    trPr = row._tr.get_or_add_trPr()
    trPr.append(parse_xml(r'<w:cantSplit %s/>' % nsdecls('w')))

def set_header_repeat(row):
    trPr = row._tr.get_or_add_trPr()
    trPr.append(parse_xml(r'<w:tblHeader %s/>' % nsdecls('w')))

def add_table(doc, headers, rows, widths=None):
    t = doc.add_table(rows=1 + len(rows), cols=len(headers))
    t.style = 'Table Grid'
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    # 表头
    hdr = t.rows[0]
    set_header_repeat(hdr)
    set_row_no_split(hdr)
    for j, h in enumerate(headers):
        set_cell(hdr.cells[j], h, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
        shade_cell(hdr.cells[j])
    # 数据行
    for i, row in enumerate(rows):
        r = t.rows[i + 1]
        set_row_no_split(r)
        for j, val in enumerate(row):
            align = WD_ALIGN_PARAGRAPH.CENTER if (j == 0 or len(str(val)) <= 12) else WD_ALIGN_PARAGRAPH.LEFT
            set_cell(r.cells[j], str(val), align=align)
    # 列宽（总宽16cm）
    if widths:
        for j, w in enumerate(widths):
            for row in t.rows:
                row.cells[j].width = Cm(w)
    # 单元格边距（tblCellMar须在tblLook之前，保持OOXML schema顺序）
    tblPr = t._tbl.tblPr
    tblCellMar = parse_xml(
        r'<w:tblCellMar %s><w:left w:w="108" w:type="dxa"/><w:right w:w="108" w:type="dxa"/>'
        r'<w:top w:w="30" w:type="dxa"/><w:bottom w:w="30" w:type="dxa"/></w:tblCellMar>'
        % nsdecls('w'))
    _insert_before(tblPr, tblCellMar, 'w:tblLook')
    return t

def add_caption(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(text)
    set_run(run, '宋体', 10.5, bold=True)
    return p

#--------------- 主流程 ---------------
doc = Document()

#页面设置：A4，2.5cm边距
sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21), Cm(29.7)
sec.top_margin = sec.bottom_margin = sec.left_margin = sec.right_margin = Cm(2.5)

#Normal样式：宋体12pt，1.5倍行距，首行缩进2字符，两端对齐
normal = doc.styles['Normal']
normal.font.name = 'Times New Roman'
normal._element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
normal.font.size = Pt(12)
normal.font.color.rgb = BLACK
pf = normal.paragraph_format
pf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
pf.first_line_indent = Pt(24)
pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

#标题样式黑色
for name in ('Title', 'Heading 1', 'Heading 2', 'Heading 3'):
    st = doc.styles[name]
    st.font.color.rgb = BLACK

#Title
tp = doc.add_paragraph(style='Title')
tr = tp.add_run('预制菜进校园舆情采集关键词词库演变记录')
set_run(tr, '黑体', 18, bold=True)
tp.alignment = WD_ALIGN_PARAGRAPH.CENTER
tp.paragraph_format.space_after = Pt(6)

#副标题
sp = doc.add_paragraph()
sr = sp.add_run('同学A数据采集 · 自然语言处理课程大作业')
set_run(sr, '楷体', 12)
sp.alignment = WD_ALIGN_PARAGRAPH.CENTER
sp.paragraph_format.space_after = Pt(12)

#一、文档说明
add_heading(doc, '一、文档说明', 1)
add_para(doc, '本项目为自然语言处理课程大作业“预制菜进校园热点事件的网络文本时序情感演化分析”，同学A负责数据采集环节。微博采集采用“人工设定初始词库＋主动学习自举扩展”的策略：以初始关键词为起点，通过话题页、搜索接口与PC高级搜索按月分段采集网络文本，并从已采相关文本中自动提取高频新词扩充词库，多轮迭代直至词库收敛。')
add_para(doc, '本记录展示词库的初始构成与主动学习扩展结果，作为数据采集过程的可追溯依据，供后续预处理与情感分析环节核对词表口径。')

#二、初始查找词库
add_heading(doc, '二、初始查找词库', 1)
add_para(doc, '初始词库由人工根据事件脉络与舆情热词设定，共20个，分为种子关键词（11个）与微博话题标签（9个）两类。')

add_heading(doc, '（一）种子关键词', 2)
add_para(doc, '种子关键词覆盖事件核心词、参与主体、关注点与治理举措，作为搜索接口与主动学习的起点。')
add_caption(doc, '表1  种子关键词（11个）')
add_table(doc, ['序号', '关键词', '类别说明'],
          [(i + 1, w, c) for i, (w, c) in enumerate(SEED)],
          widths=[1.5, 5.0, 9.5])

add_heading(doc, '（二）微博话题标签', 2)
add_para(doc, '话题标签为微博自带的话题聚合入口，采集时按话题页接口翻页，可获取该话题下的全部微博文本。')
add_caption(doc, '表2  微博话题标签（9个）')
add_table(doc, ['序号', '话题标签'],
          [(i + 1, w) for i, w in enumerate(TOPICS)],
          widths=[1.5, 14.5])

#三、主动学习新增词
add_heading(doc, '三、主动学习新增词', 1)
add_para(doc, '主动学习引擎以初始词库为种子，每轮采集后从相关文本中提取高频新词（jieba分词＋停用词过滤），经伪标签相关性分类器判定后纳入词库，直到新增词不再带来显著新文本（词库收敛）。经多轮迭代，共新增16个关键词。')
add_heading(doc, '（一）首次采集扩展', 2)
add_para(doc, '首次全量采集第1轮新增10个关键词：315、上海、全文、学生、孩子、家长、希望、西贝、进校园、预制；第2轮、第3轮无新增，词库初步收敛。')
add_heading(doc, '（二）断点续采扩展', 2)
add_para(doc, '断点续采继续扩展6个关键词：第1轮新增午餐、反对、学校、问题；第2轮新增视频；第3轮新增微博。')
add_heading(doc, '（三）新增词汇总', 2)
add_caption(doc, '表3  主动学习新增关键词（16个）')
add_table(doc, ['序号', '新增词', '扩展轮次'],
          [(i + 1, w, c) for i, (w, c) in enumerate(NEW_WORDS)],
          widths=[1.5, 6.0, 8.5])

#四、词库汇总与收敛机制
add_heading(doc, '四、词库汇总与收敛机制', 1)
add_heading(doc, '（一）词库汇总', 2)
add_caption(doc, '表4  词库构成汇总')
add_table(doc, ['词库类别', '数量', '说明'],
          [('初始·种子关键词', 11, '人工设定，覆盖事件核心词与参与主体'),
           ('初始·话题标签', 9, '人工设定，微博话题聚合入口'),
           ('主动学习新增', 16, '自举扩展，两轮采集会话共新增16个'),
           ('合计', 36, '最终词库，供预处理阶段使用')],
          widths=[4.0, 2.0, 10.0])

add_heading(doc, '（二）收敛机制', 2)
add_para(doc, '采集引擎设置收敛判据：连续两轮新增文本量低于3条时停止扩展。每轮产生的候选新词写入候选词文件，供人工审核后再决定是否纳入，保证词库扩展可控、可追溯。')

#五、词库用途
add_heading(doc, '五、词库用途', 1)
add_para(doc, '最终词库（36个）将作为预处理阶段的相关性筛选依据：对采集到的原始文本按关键词命中与排除词规则过滤，结合时间窗口与近似去重，形成用于时序情感演化分析的干净语料。词库记录保证“哪些词是初始设定的、哪些词是自动学习的”全程可溯源，支撑实验的可复现性。')

#页脚页码（居中，阿拉伯数字）
from docx.oxml import OxmlElement
footer = sec.footer
fp = footer.paragraphs[0]
fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = fp.add_run()
fld1 = OxmlElement('w:fldChar'); fld1.set(qn('w:fldCharType'), 'begin')
instr = OxmlElement('w:instrText'); instr.text = 'PAGE'
fld2 = OxmlElement('w:fldChar'); fld2.set(qn('w:fldCharType'), 'end')
run._r.append(fld1); run._r.append(instr); run._r.append(fld2)
set_run(run, '宋体', 10.5)

doc.save(TARGET)
print('saved:', TARGET)
