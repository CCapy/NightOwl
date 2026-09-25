#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 Smithbox 解包的 Nightreign param CSV 提取地图种子数据。

固定路径(相对本脚本所在目录, 无任何命令行参数):
  data/       数据源: Smithbox 解包的 param CSV
  translations/translations.csv  中文翻译字典(id,中文), MOD 新条目直接在文件末尾追加
  out/        输出目录

输出:
  map_patterns.csv  每张预制种子地图一行的组合规则(对应 1/MAP_PATTERN.csv)
  constructs.csv    每张种子地图包含的建筑/点位(对应 1/CONSTRUCT.csv)
  positions.csv     点位 ID -> 游戏世界坐标(对应 1/坐标.csv, 不含 picX/picY 像素换算)
  labels.csv        被引用 ID 的名称表: name=英文名(来自 param), zh=中文(来自 translations.csv)

用法: python extract.py

数据血缘(已逐列核实):
  map_patterns:
    - LotResultMapPatternFlag.csv  按 patternId 分组(共 520 个 pattern)
        modifierSet=190/160/150 -> Start     (值取 modifier, 为 0 则取 eventFlag)
        modifierSet=800         -> Treasure  (值取 eventFlag, 为 0 则取 modifier)
        modifierSet=3000~3090   -> Event(=modifierSet 本身), EventFlag(=eventFlag)
        modifierSet=3100~3130,3500/3600 -> EvPat(=modifierSet 本身), EvPatFlag(=modifier)
        modifierSet=500~560     -> RotRew; 该行 eventFlag 为 7xxx 时视为事件行(大空洞 DLC)
        rareMap 列              -> Special(earth_shifting, 4=大空洞)
        Name 前缀 [Gladius] 等  -> NightLord(按每个夜王最小 patternId 排序编号 0..N)
    - LotResultPlayAreaParam.csv  每 pattern 一行: playArea1/2, bossId1/2, extraBossId1/2
                                  -> Day1Loc/Day2Loc/Day1Boss/Day2Boss/extra1/extra2
  constructs:
    - LotResultSmallBaseAndSpot.csv  patternId->MAP, attachId->coord_index,
                                     smallBaseMapId*10+variationId->type
  positions:
    - SmallBaseAndSpotAttachPoint.csv(建筑/点位锚点, coord_index 对应此表 ID)
      + PlayAreaCreateParam.csv(夜圈中心 1000+/21000+)
      取 areaNo,gridXNo,gridZNo,posX,posZ(世界坐标)
  names:
    - construct type 按 type//10 查 SmallBaseMapVariationParam; Boss 按 ID 直查

仅依赖标准库。
"""

import csv
import os
import re
import sys
from collections import OrderedDict, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))

# Start 类抽选的 modifierSet 类别(按优先级)
START_SETS = (190, 160, 150)
# Event 类 modifierSet 范围 (30x0: 3000~3090); EvPat 类为 31x0~31x0 及 3500/3600
EVENT_SET_MIN, EVENT_SET_MAX = 3000, 3090
EVPAT_SET_MIN, EVPAT_SET_MAX = 3100, 3130
EVPAT_SETS = (3500, 3600)
# RotRew 类
ROTREW_MIN, ROTREW_MAX = 500, 560
# Treasure 类
TREASURE_SETS = (800, 1000)


def read_csv(path):
    """读取 CSV 为 (headers, rows) 。rows 为 dict 列表(按表头名取值)。"""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        headers = next(reader)
        rows = []
        for raw in reader:
            if not raw or all(not c.strip() for c in raw):
                continue
            row = {}
            for i, h in enumerate(headers):
                row[h.strip()] = raw[i].strip() if i < len(raw) else ""
            row["_raw"] = raw
            rows.append(row)
    return headers, rows


def to_int(v, default=0):
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# LotResultMapPatternFlag -> map_patterns
# ---------------------------------------------------------------------------

def extract_map_patterns(datas):
    path = os.path.join(datas, "LotResultMapPatternFlag.csv")
    if not os.path.exists(path):
        sys.exit("缺少 %s" % path)
    _, rows = read_csv(path)

    patterns = OrderedDict()  # patternId -> {"name":..., "special":..., "lots":[row]}
    for r in rows:
        pid = to_int(r.get("patternId"))
        p = patterns.setdefault(pid, {"name": r.get("Name", ""), "special": to_int(r.get("rareMap")), "lots": []})
        p["lots"].append(r)
        if not p["name"]:
            p["name"] = r.get("Name", "")

    # 夜王编号: 按 Name 前缀 [Xxx] 分组, 每组取最小 patternId, 排序后编号
    prefix_min = {}
    for pid, p in patterns.items():
        name = p["name"]
        if "]" in name:
            prefix = name.split("]", 1)[0] + "]"
            prefix_min[prefix] = min(prefix_min.get(prefix, pid), pid)
    nightlord_of = {prefix: i for i, (prefix, _) in enumerate(sorted(prefix_min.items(), key=lambda kv: kv[1]))}

    # Day1/Day2: LotResultPlayAreaParam
    play_path = os.path.join(datas, "LotResultPlayAreaParam.csv")
    play_of = {}
    if os.path.exists(play_path):
        _, prows = read_csv(play_path)
        for r in prows:
            play_of[to_int(r.get("patternId"))] = r

    out_rows = []
    for pid, p in patterns.items():
        name = p["name"]
        prefix = name.split("]", 1)[0] + "]" if "]" in name else ""
        row = OrderedDict()
        row["ID"] = pid
        row["NightLord"] = nightlord_of.get(prefix, -1)
        row["Special"] = p["special"]
        row["Name"] = name

        # 各 lot 类别解析
        start = treasure = event = event_flag = evpat = evpat_flag = rotrew = 0
        evpat_raw = None  # (mset, eflag, modifier): EvPat 行原始值, 用于大空洞宝箱回填
        evpat31_raw = None  # 仅 31x0 行: DLC 图中兼作 Event
        extras = []  # 其余类别原样保留(300/310/320/350/360/3700/0/99999 等)
        for lot in p["lots"]:
            mset = to_int(lot.get("modifierSet"))
            modifier = to_int(lot.get("modifier"))
            eflag = to_int(lot.get("eventFlag"))
            if mset in START_SETS:
                if start == 0 or (start not in (0,) and mset == 190):
                    val = modifier if modifier != 0 else eflag
                    if val != 0 and (start == 0 or mset == 190):
                        start = val
            elif mset in TREASURE_SETS:
                val = eflag if eflag != 0 else modifier
                if val != 0 and treasure == 0:
                    treasure = val
            elif EVENT_SET_MIN <= mset <= EVENT_SET_MAX:
                event = mset
                event_flag = eflag
            elif EVPAT_SET_MIN <= mset <= EVPAT_SET_MAX or mset in EVPAT_SETS:
                evpat = mset
                evpat_flag = modifier if modifier != 0 else eflag
                evpat_raw = (mset, eflag, modifier)
                if EVPAT_SET_MIN <= mset <= EVPAT_SET_MAX:
                    evpat31_raw = (mset, eflag)
            elif ROTREW_MIN <= mset <= ROTREW_MAX:
                # 5xx 行: eventFlag 为 7xxx 时是事件行(大空洞 DLC 变体), 否则是腐败庇佑
                if 7000 <= eflag <= 7999:
                    if event == 0:
                        event, event_flag = mset, eflag
                elif rotrew == 0:
                    rotrew = modifier if modifier != 0 else eflag
            else:
                val = modifier if modifier != 0 else eflag
                if val != 0:
                    extras.append((mset, val, eflag))

        # DLC 图(patternId>=1000)没有 30x0 行, 参考表把 31x0 同时用作 Event
        if event == 0 and evpat31_raw is not None:
            event, event_flag = evpat31_raw
        # 大空洞 DLC 图没有 800 行, 宝箱藏在 EvPat(3600) 行的 eventFlag(8xxx)
        if treasure == 0 and evpat_raw is not None and 8000 <= evpat_raw[1] <= 8999:
            treasure = evpat_raw[1]

        row["Start_190"] = start
        row["Treasure_800"] = treasure
        row["Event_30*0"] = event
        row["EventFlag"] = event_flag
        row["EvPat_30**"] = evpat
        row["EvPatFlag"] = evpat_flag
        row["RotRew_500"] = rotrew

        pa = play_of.get(pid)
        if pa:
            row["Day1Loc"] = to_int(pa.get("playArea1"))
            row["Day2Loc"] = to_int(pa.get("playArea2"))
            row["Day1Boss"] = to_int(pa.get("bossId1"))
            row["Day2Boss"] = to_int(pa.get("bossId2"))
            row["extra1"] = to_int(pa.get("extraBossId1"), -1)
            row["extra2"] = to_int(pa.get("extraBossId2"), -1)
        else:
            row["Day1Loc"] = row["Day2Loc"] = row["Day1Boss"] = row["Day2Boss"] = 0
            row["extra1"] = row["extra2"] = -1

        # 未归类 lot 结果, 逗号分隔 "set:value" (供新项目自行取用)
        row["ExtraLots"] = " ".join("%d:%d" % (s, v) for s, v, _ in extras)
        out_rows.append(row)
    return out_rows


# ---------------------------------------------------------------------------
# LotResultSmallBaseAndSpot -> constructs
# ---------------------------------------------------------------------------

def extract_constructs(datas):
    path = os.path.join(datas, "LotResultSmallBaseAndSpot.csv")
    if not os.path.exists(path):
        sys.exit("缺少 %s" % path)
    _, rows = read_csv(path)
    out_rows = []
    for r in rows:
        base_id = to_int(r.get("smallBaseMapId"))
        variation = to_int(r.get("variationId"))
        out_rows.append(OrderedDict([
            ("ID", to_int(r.get("ID"))),
            ("MAP", to_int(r.get("patternId"))),
            ("type", base_id * 10 + variation),
            ("coord_index", to_int(r.get("attachId"))),
        ]))
    return out_rows


# ---------------------------------------------------------------------------
# 世界坐标: PlayAreaCreateParam + WorldMapPointParam
# ---------------------------------------------------------------------------

COORD_SOURCES = (
    # 按优先级; 与参考表(坐标.csv 603 行)的 ID 集完全一致:
    # 锚点表(562) + 夜圈表(41), 不含 WorldMapPointParam
    "SmallBaseAndSpotAttachPoint.csv",  # 建筑/点位锚点(coord_index 的直接来源)
    "PlayAreaCreateParam.csv",          # 夜圈中心(Day1Loc/Day2Loc)
)


def extract_positions(datas, wanted_ids=None):
    """wanted_ids 仅用于覆盖率检查提示, 不做过滤(全量导出)。"""
    # 同网格锚点对的格内偏移矫正: param 表中同格的 Major/Minor 锚点,
    # 游戏内实际子位置与表内偏移相反, 互换 posX/posZ。全局生效于所有种子。
    # 证据: 108/307 (格60,44,36) 有 Starter 镜像(125/357)交叉证实;
    #       116/313 (格60,45,38) 及其 DLC 镜像 2116/2313 由游戏内核对(种子239/1044/1146)证实。
    # 注意: 2108([Large Base] Cliff West of Mistwood) 虽是 108 的 +2000 号, 但 DLC 大据点
    # 有独立命名/偏移, 实测(种子1146) 2108/2307 不需要互换, 勿套用 +2000 自动规则。
    _SWAP_POS = {108: 307, 116: 313, 2116: 2313}

    # 手动坐标微调: 锚点ID -> {"posX": x, "posZ": z} (格内偏移, 直接覆盖源表值)。
    # 用 lookup.py 查 "种子/字母" 得锚点ID, 再对照游戏位置改这两个数:
    #   posX 正=东 负=西, posZ 正=南 负=北; 大约 1 单位 ≈ 0.5 标准像素。
    # 改完重跑 extract.py 生效。基准值示例(当前表值):
    #   754/2754: posX=16.55, posZ=59.36   755/2755: posX=57, posZ=85
    _POS_OVERRIDE = {}
    by_id = OrderedDict()
    for fname in COORD_SOURCES:
        path = os.path.join(datas, fname)
        if not os.path.exists(path):
            continue
        _, rows = read_csv(path)
        for r in rows:
            rid = to_int(r.get("ID"))
            if rid in by_id:
                continue
            by_id[rid] = OrderedDict([
                ("ID", rid),
                ("name", r.get("Name", "")),   # 锚点语义(如 Castle - Rooftop Boss)
                ("areaNo", to_int(r.get("areaNo"))),
                ("gridXNo", to_int(r.get("gridXNo"))),
                ("gridZNo", to_int(r.get("gridZNo"))),
                ("posX", r.get("posX", "")),
                ("posZ", r.get("posZ", "")),
            ])
    for a, b in _SWAP_POS.items():   # 互换格内偏移 (b->a 只需执行一半, 用中间量防重复)
        if a in by_id and b in by_id and a < b:
            for key in ("gridXNo", "gridZNo", "posX", "posZ"):
                by_id[a][key], by_id[b][key] = by_id[b][key], by_id[a][key]
    for rid, over in _POS_OVERRIDE.items():   # 手动微调最后应用, 优先级最高
        if rid in by_id:
            by_id[rid].update({k: str(v) for k, v in over.items()})
    return list(by_id.values())


# ---------------------------------------------------------------------------
# 名称表
# ---------------------------------------------------------------------------

NAME_SOURCES = (
    # 建筑/野Boss 名称表: construct type 的 type//10 和 Day1/Day2 Boss ID 均在此表
    "SmallBaseMapVariationParam.csv",
    "LotResultPlayAreaParam.csv",       # 夜圈名称
    "PlayAreaCreateParam.csv",          # 夜圈地点名称
)


def extract_names(datas, zh_path=None, referenced_ids=None):
    """仅包含被引用 ID 的名称表; 英文名取 SmallBaseMapVariationParam
    (construct type 按 type//10 查, Boss 按 ID 查), 中文经 --zh-names 合并。"""
    zh = {}
    if zh_path and os.path.exists(zh_path):
        _, rows = read_csv(zh_path)
        for r in rows:
            zh[to_int(r.get("id", r.get("ID")))] = r.get("name", "")

    name_src = {}
    for fname in NAME_SOURCES:
        path = os.path.join(datas, fname)
        if not os.path.exists(path):
            continue
        _, rows = read_csv(path)
        for r in rows:
            rid = to_int(r.get("ID"))
            name = r.get("Name", "")
            if name and rid not in name_src:
                name_src[rid] = name

    # 变体真名表(由 event/*.js + msb 字段提取, 见 event_extract 逻辑):
    # MOD/原版变体的实际Boss, 优先级最高
    truth = {}
    variant_ni = {}  # 变体ID -> 长ID(nameId), translations 直查用
    tv = os.path.join(HERE, "fmg", "variant_names.csv")
    if os.path.exists(tv):
        with open(tv, encoding="utf-8-sig") as fp:
            rdr = csv.reader(fp)
            next(rdr, None)
            for row in rdr:
                if len(row) >= 3 and row[0].isdigit():
                    truth[int(row[0])] = (row[1], row[2])
                if len(row) >= 4 and row[0].isdigit() and row[3].isdigit():
                    variant_ni[int(row[0])] = int(row[3])

    # 事件链自动推导的 变体ID -> (长ID, en, zh); 槽位规则 vN -> 8(N-1)0 (实测校准)
    # 覆盖 variant_names.csv 没收录的变体
    try:
        from event_extract import extract as event_extract_map
        ev_map = {t: v for t, v in event_extract_map().items()}
    except Exception:
        ev_map = {}

    # 夜Boss槽位(49XX) -> 长ID: 读 m49_XX 事件 (Gael/E33 等公式推不出的槽位)
    try:
        from event_extract import night_boss_map
        night_map = night_boss_map()
    except Exception:
        night_map = {}

    # 文本主来源: translations/ (origin -> modX 依次覆盖 -> manual.csv 人工干预, 见 text_merge.py)
    try:
        from text_merge import load_translations
        trans = load_translations()
    except Exception:
        trans = {}
    # 次级来源: 旧 zh NpcName FMG (translations 导出未覆盖的长ID, 如 903550542)
    zf = os.path.join(HERE, "fmg", "zh_NpcName.fmg.xml")
    if os.path.exists(zf):
        for m in re.finditer(r'<text id="(\d+)">([^<]*)</text>',
                             open(zf, encoding="utf-8-sig").read()):
            if m.group(2) and int(m.group(1)) not in trans:
                trans[int(m.group(1))] = m.group(2)

    # thefifthmatt 的变体级命名库(SoulsIds/dist/NR/Names/PatternPoint.txt):
    # 原版 86 个变体的真身名
    var_names = {}
    pp = os.path.join(HERE, "fmg", "PatternPoint.txt")
    if os.path.exists(pp):
        import re as _re
        pat = _re.compile(r"- ID: (\d+)\n(?:  Variation: (\d+)\n)?  Category: \w+\n  Name: ([^\n]+)")
        for m in pat.finditer(open(pp, encoding="utf-8").read()):
            var_names[int(m.group(1)) * 10 + int(m.group(2) or 0)] = m.group(3).strip()

    out = []
    for rid in sorted(referenced_ids or []):
        # 长ID: 优先人工核对的 variant_names, 其次事件链自动推导(vN->8(N-1)0),
        # 夜Boss槽位走 m49_XX 事件映射 (Gael 904970000 / E33 140001 等)
        ni = variant_ni.get(rid) or (ev_map.get(rid) or (0, 0, "", ""))[1] or night_map.get(rid) or None
        en = (truth.get(rid) or (None, None))[0] or var_names.get(rid) \
            or name_src.get(rid) or name_src.get(rid // 10) or ""
        # 中文优先级: translations[长ID] > 旧链(zh字典/truth/基础ID回退)
        zh_text = trans.get(ni) or zh.get(rid) or (truth.get(rid) or (None, None))[1] or zh.get(rid // 10) or ""
        out.append(OrderedDict([("id", rid), ("name", en), ("zh", zh_text)]))
    return out




# ---------------------------------------------------------------------------
# 写出 / 验证
# ---------------------------------------------------------------------------

def write_csv(path, rows):
    if not rows:
        open(path, "w", encoding="utf-8-sig").close()
        return
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def run_extract(datas, out_dir):
    zh_path = os.path.join(HERE, "translations", "translations.csv")  # 中文翻译字典

    if not os.path.isdir(datas):
        sys.exit("数据源目录不存在: %s (把 Smithbox 解包的 param CSV 放到该目录)" % datas)
    os.makedirs(out_dir, exist_ok=True)

    print("读取 param: %s" % datas)
    patterns = extract_map_patterns(datas)
    constructs = extract_constructs(datas)
    wanted = set(to_int(r["coord_index"]) for r in constructs)
    wanted |= set(to_int(r["Day1Loc"]) for r in patterns) | set(to_int(r["Day2Loc"]) for r in patterns)
    positions = extract_positions(datas, wanted)

    # names: 仅收集被 map_patterns / constructs 引用的 ID
    referenced = set(wanted)
    for r in patterns:
        referenced |= {to_int(r[k]) for k in ("Day1Boss", "Day2Boss",
                                              "Event_30*0", "EventFlag", "EvPat_30**", "EvPatFlag",
                                              "RotRew_500", "Treasure_800", "Start_190")}
        referenced |= {to_int(r["extra1"]), to_int(r["extra2"])}
    referenced |= set(to_int(r["type"]) for r in constructs)
    referenced.discard(0)
    referenced.discard(-1)
    names = extract_names(datas, zh_path, referenced)

    write_csv(os.path.join(out_dir, "map_patterns.csv"), patterns)
    write_csv(os.path.join(out_dir, "constructs.csv"), constructs)
    write_csv(os.path.join(out_dir, "positions.csv"), positions)
    write_csv(os.path.join(out_dir, "labels.csv"), names)
    extract_boss_stats(datas, out_dir, constructs, patterns)

    print("输出: %s" % out_dir)
    print("  map_patterns.csv  %d 行" % len(patterns))
    print("  constructs.csv    %d 行" % len(constructs))
    print("  positions.csv     %d 行" % len(positions))
    print("  labels.csv        %d 行" % len(names))

    # 覆盖率检查: 每个 coord_index / Loc 都应能查到坐标
    pos_ids = set(to_int(r["ID"]) for r in positions)
    missing = sorted((wanted - pos_ids) - {0})
    if missing:
        print("警告: %d 个引用 ID 无坐标: %s" % (len(missing), missing[:20]))


STAT_COLS = (
    # (CSV列名, NpcParam列名) — 物理/属性四维 + 韧性 + 异常五维
    ("phys_n", "neutralDamageCutRate"), ("phys_b", "blowDamageCutRate"),
    ("phys_s", "slashDamageCutRate"), ("phys_t", "thrustDamageCutRate"),
    ("mag", "magicDamageCutRate"), ("fire", "fireDamageCutRate"),
    ("thunder", "thunderDamageCutRate"), ("holy", "darkDamageCutRate"),
    ("poise", "superArmorDurability"),
    ("blood", "resist_blood"), ("poison", "resist_poison"),
    ("rot", "resist_desease"), ("frost", "resist_freeze"),
)


def _empty_stat(t):
    return OrderedDict([("type", t), ("npc_id", ""), ("name_id", "")]
                       + [(k, "") for k, _ in STAT_COLS] + [("name_en", "")])


def extract_boss_stats(datas, out_dir, constructs_ref=(), patterns_ref=()):
    """变体ID -> NPCParam 抗性数据: 事件槽位(vN->8(N-1)0) -> MSB实体 -> NPCParam。
    输出 out/boss_stats.csv, 供左侧数据面板使用。"""
    import glob as _glob
    npc = {}
    for _r in csv.DictReader(open(os.path.join(datas, "NpcParam.csv"), encoding="utf-8-sig")):
        npc[_r["ID"]] = _r
    # 实体 -> NPCParamID (MSB 反解目录)
    ent_npc = {}
    for xml in _glob.glob(os.path.join(HERE, "msb", "*", "Part", "Enemy", "*.xml")):
        try:
            t = open(xml, encoding="utf-8-sig").read()
        except OSError:
            continue
        for _m in re.finditer(r"<EntityID>(\d+)</EntityID>.*?<NPCParamID>(\d+)</NPCParamID>", t, re.S):
            ent_npc.setdefault(_m.group(1), _m.group(2))

    out_rows = []
    for js in _glob.glob(os.path.join(HERE, "event", "m4?_??_00_00.emevd.dcx.js")):
        _m = re.match(r"m(\d\d)_(\d\d)_00_00", os.path.basename(js))
        if not _m:
            continue
        family = int(_m.group(1)) * 100 + int(_m.group(2))
        text = open(js, encoding="utf-8").read()
        for mm in re.finditer(r"\$InitializeCommonEvent\(\d+,\s*90015000,\s*(\d+),\s*(\d+),\s*(\d{9})", text):
            entity, nameid = mm.group(2), mm.group(3)
            slot = int(entity) % 1000
            var = (slot - 800) // 10 + 1          # vN -> 实体 8(N-1)0 (实测校准)
            if not (1 <= var <= 9):
                continue
            t = family * 10 + var
            pid = ent_npc.get(entity)
            row = npc.get(pid) if pid else None
            rec = OrderedDict([("type", t), ("npc_id", pid or ""), ("name_id", nameid)])
            for out_k, npc_k in STAT_COLS:
                rec[out_k] = row.get(npc_k, "") if row else ""
            rec["name_en"] = row.get("Name", "") if row else ""
            out_rows.append(rec)

    # 同 type 多行去重(保留首个有数据的)
    dedup = OrderedDict()
    for r in out_rows:
        k = str(r["type"])   # key 统一为字符串, 与后续 constructs/patterns 补充的检查一致
        old = dedup.get(k)
        if old is None or (not old["npc_id"] and r["npc_id"]):
            dedup[k] = r

    # 补充: constructs 里实际用到的强敌 type 若事件未覆盖(如 m46_50/60/80 事件缺失,
    # 监牢Boss 4650x/4660x/4680x 等), 生成空行交给按名回填
    def _is_field_boss(t):
        st = str(t)
        return st.startswith(("45", "46")) and not st.startswith("460") and (
            st.startswith("4551") or t // 1000 != 45) and t != 46780
    for _r in constructs_ref:
        t = to_int(_r.get("type"))
        if t and _is_field_boss(t) and str(t) not in dedup:
            dedup[str(t)] = _empty_stat(t)
    # 夜晚Boss (map_patterns 的 Day1/Day2Boss, 如 4958) 与夜王 (4900+序号, 名取自 bosses.csv)
    for _p in patterns_ref:
        for k in ("Day1Boss", "Day2Boss"):
            t = to_int(_p.get(k))
            if t and t > 0 and str(t) not in dedup:
                dedup[str(t)] = _empty_stat(t)
        nl = to_int(_p.get("NightLord"))
        if 0 <= nl <= 9 and str(4900 + nl) not in dedup:
            en = {0: "Gladius - Beast of Night", 1: "Adel - Baron of Night",
                  2: "Gnoster - Wisdom of Night", 3: "Maris - Augur of Night",
                  4: "Libra - Equilibrious Beast", 5: "Fulghor - Darkdrift Knight",
                  6: "Caligo - Fissure in the Fog", 7: "Heolstor - Night Aspect",
                  8: "Harmonia - Balancers", 9: "Straghess - Dreglord"}.get(nl, "")
            rec = _empty_stat(4900 + nl)
            rec["name_en"] = en
            dedup[str(4900 + nl)] = rec

    # 补全: 无 MSB 数据的变体按英文名匹配 NpcParam (同怪各版本抗性一致, 已实测)
    labels_en = {}
    try:
        for _r in csv.DictReader(open(os.path.join(out_dir, "labels.csv"), encoding="utf-8-sig")):
            if _r.get("name"):
                labels_en[int(_r["id"])] = _r["name"]
    except OSError:
        pass
    npc_lower = [(nid, row, row["Name"].lower()) for nid, row in npc.items() if row.get("Name")]
    filled = 0
    for t, r in dedup.items():
        if r["npc_id"]:
            continue
        en = labels_en.get(int(t), "") or r.get("name_en", "")
        if not en:
            en = {46543: "Ghostflame Dragon", 46753: "Ghostflame Dragon",
                  46762: "Ghostflame Dragon"}.get(int(t), "")
        key = en.split("- ")[-1].strip().lower()
        if key in ("beast of night", "baron of night", "wisdom of night", "augur of night",
                   "equilibrious beast", "darkdrift knight", "fissure in the fog",
                   "night aspect", "balancers", "dreglord"):
            key = en.split(" - ")[0].strip().lower()   # 夜王: 用本体名 (gladius/adel/...)
        if key.startswith("night boss"):
            key = key[len("night boss"):].strip(" -")
        if "/" in key:   # 复合名 (如 draconic tree sentinel/royal cavalrymen) 取前半
            key = key.split("/")[0].strip()
        if "," in key:   # 带地名后缀 (Gnoster, West ... -> gnoster)
            key = key.split(",")[0].strip()
        if not key:
            continue
        def _clean(nm):
            return not any(b in nm for b in ("prelude", "raid", "helper", "dummied"))
        hits = ([row for _nid, row, nm in npc_lower if nm == key and _clean(nm)]
                or [row for _nid, row, nm in npc_lower if nm.startswith(key) and _clean(nm)]
                or [row for _nid, row, nm in npc_lower if nm == key]
                or [row for _nid, row, nm in npc_lower if nm.startswith(key)]
                or [row for _nid, row, nm in npc_lower if key in nm and _clean(nm)]
                or [row for _nid, row, nm in npc_lower if key in nm])

        if not hits:  # 复数骑士队/双生等别名 -> 单怪 NpcParam
            key_bare = key.split(" (")[0].strip()
            _alias = {"abductor virgins": "abductor virgin", "lordsworn knights": "lordsworn captain",
                      "cuckoo knights": "cuckoo knight", "royal army knights": "leyndell knight",
                      "redmane knights": "redmane knight", "mausoleum knights": "mausoleum knight",
                      "haligtree knights": "haligtree knight",
                      "frenzied duelist": "frenzied grave warden duelist",
                      "decaying rancor dragon": "rancor dragon", "lake glintstone dragon": "glintstone dragon smarag",
                      "rotten duelist": "grave warden duelist", "nox warriors": "nox swordstress",
                      "beastmen of farum azula": "beastman",
                      "hollow manservant": "hollow manserving servant",
                      "stoneskin lords": "crystalian", "beastly brigade": "misbegotten",
                      "decaying rancor dragon": "ghostflame dragon",
                      "regal ancestor spirit": "regal ancestor", "rennala phase 2": "rennala",
                      "godfrey phase 1": "godfrey", "messmer phase 1": "messmer",
                      "malenia phase 1": "malenia", "gaping dragon": "great gaping dragon",
                      "duke's dear freja": "freja", "bell-bearing hunter (elemer)": "bell bearing hunter",
                      "regal ancestor spirit": "ancestor spirit (regal)", "gaping dragon": "great gaping",
                      "e33": "straghess"}
            ak = _alias.get(key) or _alias.get(key_bare)
            if ak:
                hits = [row for _nid, row, nm in npc_lower if nm.startswith(ak)]
        if not hits and len(key) > 4:  # 去复数/去括号再试 (Crystalians -> Crystalian)
            k2 = key.split(" (")[0]
            if k2.endswith("s") and not k2.endswith("ss"):
                k2 = k2[:-1]
            if k2 != key:
                hits = [row for _nid, row, nm in npc_lower if nm == k2] or \
                       [row for _nid, row, nm in npc_lower if k2 in nm]
        if not hits:  # 再试名字主干 (去掉 of/duo 等修饰, 如 "红狼of王夫" -> "red wolf")
            stem = key.split(" of ")[0].replace(" duo", "").replace(" (dismounted)", "").strip()
            if stem and len(stem) >= 4:
                hits = [row for _nid, row, nm in npc_lower
                        if nm.startswith(stem) and "prelude" not in nm]
        if hits:
            row = hits[0]
            r["npc_id"] = "by-name:" + row["ID"]
            r["name_en"] = row["Name"]
            for out_k, npc_k in STAT_COLS:
                r[out_k] = row.get(npc_k, "")
            filled += 1
    write_csv(os.path.join(out_dir, "boss_stats.csv"), list(dedup.values()))
    print("  boss_stats.csv    %d 行 (按名补全 %d)" % (len(dedup), filled))


def main():
    # 唯一数据集: data/(用户用 Smithbox 导出的 param) -> out/
    run_extract(os.path.join(HERE, "data"), os.path.join(HERE, "out"))


if __name__ == "__main__":
    main()
