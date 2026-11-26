# -*- coding: utf-8 -*-
"""
下载 THUOCL 清华大学开放中文词库（饮食类，可选成语类），合并去重后写入
词典库\\userdict.txt，供 preprocess.py 作为 jieba 用户词典加载。

运行：
  C:\\Users\\lysix\\AppData\\Local\\Programs\\Python\\Python313\\python.exe fetch_thuocl.py

来源：THUOCL: Tsinghua Open Chinese Lexicon, THUNLP, Tsinghua University.
  官网 http://thuocl.thunlp.org/
"""
import os
import requests

# 是否同时下载成语类（True=下载，False=只下饮食类）
INCLUDE_CHENGYU = True

SOURCES = {
    "food": "http://thuocl.thunlp.org/source/THUOCL_food.txt",
    "chengyu": "http://thuocl.thunlp.org/source/THUOCL_chengyu.txt",
}

DICT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "词典库")
OUT_FILE = os.path.join(DICT_DIR, "userdict.txt")

HEADERS = {"User-Agent": "Mozilla/5.0"}


def main():
    os.makedirs(DICT_DIR, exist_ok=True)
    urls = dict(SOURCES)
    if not INCLUDE_CHENGYU:
        urls.pop("chengyu", None)

    words = []
    seen = set()
    for tag, url in urls.items():
        resp = requests.get(url, timeout=20, headers=HEADERS)
        resp.raise_for_status()
        lines = resp.content.decode("utf-8", errors="replace").splitlines()
        n_before = len(seen)
        for line in lines:
            w = line.strip()
            if not w:
                continue
            head = w.split("\t")[0].split(" ")[0]
            if head and head not in seen:
                seen.add(head)
                words.append(w)
        print("  [%s] 下载 %d 行，新增 %d 词" % (tag, len(lines), len(seen) - n_before))

    with open(OUT_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(words) + "\n")
    print("已合并去重 %d 词 -> %s" % (len(words), OUT_FILE))


if __name__ == "__main__":
    main()
