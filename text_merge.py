# -*- coding: utf-8 -*-
"""翻译文本合并工具 (按项目规定):

优先级(低 -> 高):
  1. 原版翻译:  translations/origin.txt, origin_dlc01.txt   (格式: ID;文本)
  2. MOD翻译:   translations/modX.fmg.xml / modX_dlc01.fmg.xml (按 X 序号依次覆盖/新增)
  3. 人工干预:  translations/manual.csv                      (格式: ID,文本, 最高优先级)

规则:
  - %null% / 空文本视为缺失, 不覆盖已有条目
  - 原版与 dlc 两个文件直接合并, ID 不冲突
"""
import csv
import os
import re

TRANS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "translations")

_XML_ENTRY = re.compile(r'<text id="(\d+)">([^<]*)</text>')


def _load_origin(path, out):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8-sig"):
        tid, _, text = line.partition(";")
        text = text.strip()
        if tid.strip().isdigit() and text:
            out[int(tid)] = text


def _load_xml(path, out):
    """MOD xml: 非空文本覆盖/新增, %null% 视为缺失不覆盖"""
    if not os.path.exists(path):
        return
    for m in _XML_ENTRY.finditer(open(path, encoding="utf-8-sig").read()):
        text = m.group(2)
        if text and text != "%null%":
            out[int(m.group(1))] = text


def _mod_key(fname):
    m = re.match(r"mod(\d+)", fname)
    return int(m.group(1)) if m else 0


def _find_dlc(trans_dir, mod_file):
    """mod1.xml -> mod1_dlc01.xml / mod1_dlc01.fmg.xml (两种命名都认)"""
    base = re.match(r"(mod\d+)(\.fmg)?\.xml$", mod_file).group(1)
    for cand in (base + "_dlc01.xml", base + "_dlc01.fmg.xml"):
        if os.path.exists(os.path.join(trans_dir, cand)):
            return cand
    return None


def load_translations(trans_dir=TRANS_DIR):
    """返回合并后的 {长ID: 中文} 字典: origin -> modX(升序) -> manual.csv"""
    merged = {}
    # 1. 原版 (含 dlc, 直接合并)
    _load_origin(os.path.join(trans_dir, "origin.txt"), merged)
    _load_origin(os.path.join(trans_dir, "origin_dlc01.txt"), merged)

    # 2. MOD 按序号升序覆盖 (每个 mod 的主文件与 dlc 文件一并处理)
    if os.path.isdir(trans_dir):
        mods = sorted((f for f in os.listdir(trans_dir)
                       if re.match(r"mod\d+(\.fmg)?\.xml$", f)), key=_mod_key)
        for mf in mods:
            _load_xml(os.path.join(trans_dir, mf), merged)
            dlc = _find_dlc(trans_dir, mf)
            if dlc:
                _load_xml(os.path.join(trans_dir, dlc), merged)

    # 3. 人工干预 csv (最高优先级)
    manual = os.path.join(trans_dir, "manual.csv")
    if os.path.exists(manual):
        with open(manual, encoding="utf-8-sig") as fp:
            for row in csv.reader(fp):
                if not row or row[0].strip().startswith("#") or not row[0].strip().isdigit():
                    continue
                if len(row) >= 2 and row[1].strip():
                    merged[int(row[0])] = row[1].strip()
    return merged


if __name__ == "__main__":
    d = load_translations()
    print("合并后条目: %d" % len(d))
    for tid in (905040000, 905250600, 903550542, 905320000, 100000, 100010):
        print(tid, "->", d.get(tid))
