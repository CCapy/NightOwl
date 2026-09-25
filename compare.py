#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对比两次解析输出(如 MOD 版 out/ 与原版 out_origin/)的差异。

用法: python compare.py [目录A 目录B]
默认对比 out/ 与 out_origin/ (相对本脚本目录)。
"""
import csv
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))


def rd(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8-sig') as f:
        return list(csv.reader(f))


def main():
    dir_a = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out")
    dir_b = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "out_origin")
    print("对比: %s  vs  %s\n" % (dir_a, dir_b))

    # ---- map_patterns ----
    a, b = rd(os.path.join(dir_a, "map_patterns.csv")), rd(os.path.join(dir_b, "map_patterns.csv"))
    ha = a[0]
    A = {r[0]: dict(zip(ha, r)) for r in a[1:]}
    B = {r[0]: dict(zip(ha, r)) for r in b[1:]}
    print("== map_patterns: A %d 行 / B %d 行" % (len(A), len(B)))
    om = sorted(set(A) - set(B), key=lambda x: int(x))
    ob = sorted(set(B) - set(A), key=lambda x: int(x))
    print("   仅A(MOD): %d 个 %s" % (len(om), om[:10]))
    print("   仅B(原版): %d 个 %s" % (len(ob), ob[:10]))
    cols = ["NightLord", "Special", "Start_190", "Treasure_800", "Event_30*0", "EventFlag",
            "EvPat_30**", "EvPatFlag", "RotRew_500", "Day1Boss", "Day1Loc", "Day2Boss",
            "Day2Loc", "extra1", "extra2"]
    c = Counter()
    ex = []
    for k in set(A) & set(B):
        for col in cols:
            if A[k].get(col) != B[k].get(col):
                c[col] += 1
                if len(ex) < 10:
                    ex.append("%s.%s: A=%s B=%s" % (k, col, A[k].get(col), B[k].get(col)))
    print("   共有种子的差异列统计: %s" % (dict(c) or "无"))
    for e in ex:
        print("     " + e)

    # ---- constructs ----
    a, b = rd(os.path.join(dir_a, "constructs.csv")), rd(os.path.join(dir_b, "constructs.csv"))
    key = lambda r: (r[1], r[3])  # (MAP, coord_index)
    A = {key(r): r[2] for r in a[1:]}
    B = {key(r): r[2] for r in b[1:]}
    print("\n== constructs: A %d 行 / B %d 行" % (len(A), len(B)))
    diff = [k for k in set(A) & set(B) if A[k] != B[k]]
    print("   同位置 type 不同: %d" % len(diff))
    for k in sorted(diff)[:10]:
        print("     种子%s 位置%s: A type=%s B type=%s" % (k[0], k[1], A[k], B[k]))
    # type 集合差异
    ta, tb = Counter(A.values()), Counter(B.values())
    new_types = {t: n for t, n in (ta - tb).items()}
    gone_types = {t: n for t, n in (tb - ta).items()}
    print("   A 新增 type (type:出现次数): %d 种 %s" % (len(new_types), sorted(new_types.items())[:15]))
    print("   B 独有 type: %d 种 %s" % (len(gone_types), sorted(gone_types.items())[:15]))

    # ---- positions ----
    a, b = rd(os.path.join(dir_a, "positions.csv")), rd(os.path.join(dir_b, "positions.csv"))
    A = {r[0]: r for r in a[1:]}
    B = {r[0]: r for r in b[1:]}
    print("\n== positions: A %d 行 / B %d 行" % (len(A), len(B)))
    print("   仅A: %s" % sorted(set(A) - set(B), key=lambda x: int(x))[:10])
    print("   仅B: %s" % sorted(set(B) - set(A), key=lambda x: int(x))[:10])
    moved = []
    for k in set(A) & set(B):
        if A[k][1:] != B[k][1:]:
            moved.append((k, A[k], B[k]))
    print("   坐标变化: %d 个" % len(moved))
    for k, ra, rb in moved[:5]:
        print("     %s: A=%s B=%s" % (k, ra[1:5], rb[1:5]))

    # ---- names ----
    a, b = rd(os.path.join(dir_a, "names.csv")), rd(os.path.join(dir_b, "names.csv"))
    A = {r[0]: r for r in a[1:]}
    B = {r[0]: r for r in b[1:]}
    print("\n== names: A %d 行 / B %d 行" % (len(A), len(B)))
    new_ids = sorted(set(A) - set(B), key=lambda x: int(x))
    print("   仅A ID: %d 个" % len(new_ids))
    # 给新 ID 标注用途(是否为 construct type / 引用计数)
    con = rd(os.path.join(dir_a, "constructs.csv"))
    con_types = Counter(r[2] for r in con[1:])
    for i in new_ids[:40]:
        r = A[i]
        use = "construct出现%d次" % con_types[i] if con_types.get(i) else ""
        print("     %s  en=%s  zh=%s  %s" % (i, r[1], r[2], use))


if __name__ == "__main__":
    main()
