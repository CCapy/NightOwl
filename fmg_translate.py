#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用游戏 FMG 文本自动翻译 out/labels.csv 的英文名。

数据链: labels.csv 的英文名(param 描述名) --关键词匹配--> en_NpcName.fmg.xml
        --同一 text id--> zh_NpcName.fmg.xml 的中文

运行顺序: python extract.py && python fmg_translate.py
(extract.py 会重新生成 labels.csv, 翻译结果不落盘到 translations.csv,
 需重跑本工具; translations.csv 的人工翻译优先级更高, 不受影响)

文件:
  fmg/en_NpcName.fmg.xml, fmg/zh_NpcName.fmg.xml   中英 FMG(Smithbox 导出)
  fmg/fmg_alias.csv      关键词别名(可选): 英文关键词,FMG英文片段
        用于词汇不一致的条目, 如 "Fortissax,Lichdragon"
  out/labels.csv         被翻译(原地更新 zh 列)
  out/fmg_unmatched.csv  匹配失败的条目(供人工处理)
"""
import csv
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FMG_DIR = os.path.join(HERE, "fmg")
LABELS_DIR = os.path.join(HERE, "out")
ALIAS = os.path.join(FMG_DIR, "fmg_alias.csv")

# param 名中需要去掉的修饰后缀
SUFFIXES = (" Tier 2", " phase 1", " phase 2", " Duo", " (Wings)")


def load_fmg(path):
    d = {}
    for m in re.finditer(r'<text id="(\d+)">([^<]*)</text>', open(path, encoding="utf-8-sig").read()):
        d[int(m.group(1))] = m.group(2)
    return d


def keyword(en_name):
    """从 param 英文名提取匹配关键词: 去掉 'Night Boss - ' 等类别前缀和修饰后缀。"""
    kw = en_name.split(" - ", 1)[1] if " - " in en_name else en_name
    kw = kw.split(": ", 1)[1] if kw.startswith("Tile") else kw
    for suf in SUFFIXES:
        kw = kw.replace(suf, "")
    return kw.strip()


def singular(w):
    return w[:-1] if w.endswith("s") and not w.endswith("ss") else w


def main():
    LABELS = os.path.join(LABELS_DIR, "labels.csv")
    UNMATCHED = os.path.join(LABELS_DIR, "fmg_unmatched.csv")
    en = load_fmg(os.path.join(FMG_DIR, "en_NpcName.fmg.xml"))
    zh = load_fmg(os.path.join(FMG_DIR, "zh_NpcName.fmg.xml"))
    common = {i: (en[i], zh.get(i, "")) for i in set(en) & set(zh) if zh.get(i)}

    alias = {}
    if os.path.exists(ALIAS):
        with open(ALIAS, encoding="utf-8-sig") as f:
            for row in csv.reader(f):
                if len(row) >= 2 and row[0].strip():
                    alias[row[0].strip().lower()] = row[1].strip().lower()

    # en 文本(小写)索引
    exact_index = {}
    for i, (e, z) in common.items():
        exact_index.setdefault(e.strip().lower(), set()).add(z)

    with open(LABELS, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1:]

    matched, unmatched = 0, []
    for r in body:
        if len(r) < 3 or r[2]:
            continue  # 已有人工翻译
        rid, en_name = r[0], r[1]
        kw = keyword(en_name).lower()
        targets = [kw]
        if kw != singular(kw):
            targets.append(singular(kw))
        if kw in alias:
            targets.insert(0, alias[kw])
        # 每个词也做单数化变体
        targets += [" ".join(singular(w) for w in t.split()) for t in list(targets)]

        result = None
        for t in dict.fromkeys(targets):  # 去重保序
            if t in exact_index:      # 整串精确匹配
                cands = exact_index[t]
                if len(cands) == 1:
                    result = cands.pop()
                    break
        if result is None:            # 包含匹配: 取最短英文(避开 'and more' 类变体)
            best = None
            for t in dict.fromkeys(targets):
                for i, (e, z) in common.items():
                    if t and t in e.strip().lower():
                        if best is None or len(e) < len(best[0]):
                            best = (e, z)
            if best is not None:
                result = best[1]
        if result:
            r[2] = result
            matched += 1
        else:
            unmatched.append((rid, en_name))

    with open(LABELS, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(body)
    with open(UNMATCHED, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name"])
        w.writerows(unmatched)

    print("FMG 自动翻译: 成功 %d 条, 失败 %d 条" % (matched, len(unmatched)))
    print("失败清单: %s (可人工加入 translations.csv, 或在 fmg/fmg_alias.csv 加别名)" % UNMATCHED)


if __name__ == "__main__":
    main()
