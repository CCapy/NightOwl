#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从小基地事件反编译JS中提取 变体type -> 真身 nameId(FMG) 映射."""
import re
import glob
import os

HERE = os.path.dirname(os.path.abspath(__file__))

def load_fmg(p):
    return {int(m.group(1)): m.group(2) for m in re.finditer(r'<text id="(\d+)">([^<]*)</text>', open(p, encoding='utf-8-sig').read())}

EN = load_fmg(os.path.join(HERE, 'fmg', 'en_NpcName.fmg.xml'))
ZH = load_fmg(os.path.join(HERE, 'fmg', 'zh_NpcName.fmg.xml'))

_v0_cache = {}


def _family_has_v0(family):
    """该家族(family*10=smallBaseMapId)的 variationId 是否含 0"""
    if family not in _v0_cache:
        base = str(family)
        has = False
        p = os.path.join(HERE, 'data', 'LotResultSmallBaseAndSpot.csv')
        if os.path.exists(p):
            import csv as _csv
            for r in _csv.DictReader(open(p, encoding='utf-8-sig')):
                if r['smallBaseMapId'] == base and r['variationId'] == '0':
                    has = True
                    break
        _v0_cache[family] = has
    return _v0_cache[family]


def extract():
    result = {}
    for js in glob.glob(os.path.join(HERE, 'event', '*.emevd.dcx.js')):
        base = os.path.basename(js)
        m = re.match(r'm(\d\d)_(\d\d)_00_00\.emevd', base)
        if not m:
            continue
        area, sub = int(m.group(1)), int(m.group(2))
        family = area * 100 + sub
        text = open(js, encoding='utf-8').read()
        # 90015000 事件签名: (eventFlagId, chrEntityId, nameId, ...) -> 第3参是 nameId
        for mm in re.finditer(r'\$InitializeCommonEvent\(\d+,\s*90015000,\s*(\d+),\s*(\d+),\s*(\d{9})', text):
            entity, nameid = int(mm.group(2)), int(mm.group(3))
            slot = entity % 1000  # 实体尾段 (兼容 46720810 / 46625870 两种编号格式)
            if not 800 <= slot <= 890:
                continue
            # 槽位规则按家族二选一 (实测校准, 种子211/4670家族证实):
            #   变体从 v1 开始的家族(如 4662: v1~v8 <-> 800~870): vN -> 8(N-1)0
            #   含 v0 的家族(如 4670: v0~v4):                     vN -> 8N0
            _off = 0 if _family_has_v0(family) else 1
            var = (slot - 800) // 10 + _off
            if not (0 <= var <= 9):
                continue
            t = family * 10 + var
            result[t] = (entity, nameid, EN.get(nameid, "?"), ZH.get(nameid, "?"))
    return result

if __name__ == "__main__":
    result = extract()
    print("提取到 %d 个变体真身" % len(result))
    for t, expect in ((46651, "丘陵飞龙?"), (46584, "熔岩土龙"), (46772, "狮子混种"),
                      (46695, "鸦人骑士?"), (46672, "红袍活尸?"), (49432, "亚人城?")):
        r = result.get(t)
        print("  %d (期望%s): %s" % (t, expect,
              ("%s -> %s / %s" % r[1:]) if r else "未提取到"))
    print("--- 夜Boss槽位映射 ---")
    nb = night_boss_map()
    for t in (4961, 4964, 4966, 4975):
        print(" ", t, "->", nb.get(t))


def night_boss_map():
    """夜Boss槽位(49XX) -> nameId。

    权威来源是 event/m49_XX_00_00.emevd.dcx.js (nameId 明写, 取首个 904 段;
    Gael 4966 的文本实际挂在 904970000, E33 4975 用角色名段 14xxxx, 公式推不出)。
    无事件文件时回退公式 904{槽位}000 (Gaius 4964->904964000 这类主规律)。
    """
    import glob as _glob
    result = {}
    for js in _glob.glob(os.path.join(HERE, 'event', 'm49_??_00_00.emevd.dcx.js')):
        m = re.match(r'm49_(\d\d)_00_00', os.path.basename(js))
        if not m:
            continue
        slot = 4900 + int(m.group(1))
        text = open(js, encoding='utf-8').read()
        # 优先 904 段(夜Boss主命名段), 其次任意 90 段, 最后 14 段角色名(E33->140001)
        for pat in (r'\b(904\d{6})\b', r'\b(90\d{7})\b', r'\b(14\d{4})\b'):
            mm = re.search(pat, text)
            if mm:
                result[slot] = int(mm.group(1))
                break
    return result
