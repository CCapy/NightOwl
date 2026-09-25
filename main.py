#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
地图种子识别程序。

用法: python main.py
输入: input/map.jpg (游戏内完整地图截图)
输出: 控制台打印匹配的种子及信息; output/ 下保存调试图片

流程 (模仿 nightreign-overlay-helper 的 MapDetector, 但为单张图片离线识别):
  1. 特殊地形识别: 与 assets/maps/0~5.jpg 逐张做尺度/偏移扫掠像素差匹配
  2. 夜王识别: 截取地图左下夜王头像区域, 与夜王图标模板匹配
  3. SIFT 对齐: 将输入图对齐到 assets/maps_poi_match/ 干净底图 (750x750 标准坐标)
  4. POI 识别: 在已知点位上, 把候选建筑图标合成到底图上与截图比对
  5. 种子打分: 识别出的 POI 组合与每张种子比对, 按 error/score 排序输出 top5

数据: out/*.csv (由 extract.py 生成), 图标: assets/
"""
import csv
import os
import time
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(HERE, "input")
OUT_DIR = os.path.join(HERE, "output")
RESULT_PATH = os.path.join(HERE, "result.txt")
DATA_DIR = os.path.join(HERE, "out")
ASSET_DIR = os.path.join(HERE, "assets")

# 调试模式: True 时所有显示名称带 ID 前缀, 如 "46571_猎犬骑士" (方便核对数据)
DEBUG = True

# 游戏截图中地图区域的左上角坐标与(宽,高); DEBUG(测试)模式用测试分辨率
SCREEN_CROP = ((1434, 318), (880, 890)) if DEBUG else ((1450, 230), (1000, 1000))

STD_MAP_SIZE = (750, 750)
Position = tuple[int, int]

# ---- 阈值 (取自原项目 config.yaml) ----
EARTH_SHIFTING_ERROR_THRESHOLD = 50
SUBICON_TEMPLATE_MATCH_THRESHOLD = 0.15  # 原项目为0.06, 实测本流程真实截图需0.15
NIGHTLORD_SCORE_THRESHOLD = 0.2     # 夜王匹配置信度低于此值才采信, 否则视为未知夜王
POI_MATCH_SAMPLE_RATIO = 1.0
TOPK = 5
SAVE_DEBUG = False  # 识别过程调试图是否写盘(overlay 按 config 开关)

# ---- 世界坐标 -> 750x750 标准坐标 标定系数 ----
# 由原项目 坐标.csv (603 点) 最小二乘拟合, 592/603 点残差为 0, 剩余为人工标注抖动点
# std_x = a_g*gridXNo + a_p*posX + c
CALIB_STD = dict(ax_g=123.035567, ax_p=0.4806080, cx=-4979.95638,      # 普通地形 X
                 ay_g=-123.035567, ay_p=-0.4806080, cy=4991.61664)     # 普通地形 Y
CALIB_TGH = dict(ax_g=154.1485555, ax_p=0.6021430, cx=-6307.01775,     # 大空洞 X
                 ay_g=-154.1485555, ay_p=-0.6021430, cy=6146.37630)    # 大空洞 Y


def world_to_std(gx, px, gz, pz, tgh=False):
    c = CALIB_TGH if tgh else CALIB_STD
    return (int(c["ax_g"] * gx + c["ax_p"] * px + c["cx"]),
            int(c["ay_g"] * gz + c["ay_p"] * pz + c["cy"]))


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Construct:
    type: int
    pos_index: int
    is_underground: bool = False
    is_rooftop: bool = False      # 主城楼顶 Boss 锚点
    is_basement: bool = False     # 主城地下室 Boss 锚点
    pos: Position = None


@dataclass
class MapPattern:
    id: int
    nightlord: int
    earth_shifting: int
    day1_boss: int
    day2_boss: int
    day1_pos: Position
    day2_pos: Position
    day1_extra: int
    day2_extra: int
    treasure: int
    rot_rew: int
    event_value: int
    event_flag: int
    evpat_value: int
    evpat_flag: int
    pos_constructions: dict = field(default_factory=dict)
    constructs: list = field(default_factory=list)  # 完整建筑列表(含坐标碰撞的)
    day2_pos_idx: int = 0


# POI 大类 -> 图标缩放 (原项目 POI_ICON_SCALE)
POI_ICON_SCALE = {
    30: 0.35, 32: 0.5, 34: 0.4, 37: 0.4, 38: 0.3, 40: 0.4, 41: 0.38,
    510: 0.38, 511: 0.4, 500: 0.15, 501: 0.2, 524: 0.3, 525: 0.4,
    535: 0.15, 536: 0.18,
}
POI_ICON_OFFSET = {500: (0, -3), 501: (0, 3), 535: (-3, -3), 536: (0, -2)}
# 可作为 POI 识别的 construct type 前缀 (原项目 POI_CONSTRUCTS)
POI_PREFIXES = [30, 32, 34, 37, 38, 40, 41, 50, 5110, 510, 52, 5358, 5359, 5367, 5368]
# 大空洞地下区域坐标 ID
TGH_UNDERGROUND_COORDS = [1160, 1159, 1107, 1110, 1153, 1175, 1174]
STD_POI_SIZE = (45, 45)

ATTR_KEYS = ["fire", "magic", "thunder", "holy"]
COND_KEYS = ["bleed", "frost", "death", "frenzy", "corruption", "poison", "sleep"]
# 子图标映射 (原项目 CTYPE_SUBICON_MAP 完整复制)
CTYPE_SUBICON_MAP: dict[int, tuple[str, str]] = {  # ctype -> (类别, 名)
    30301: ("attribute", "magic"),
    32101: ("attribute", "fire"), 32102: ("attribute", "thunder"),
    32200: ("attribute", "fire"), 32201: ("condition", "frenzy"),
    34001: ("condition", "bleed"), 34002: ("condition", "frost"), 34003: ("attribute", "holy"),
    34100: ("condition", "poison"), 34101: ("condition", "poison"),
    34102: ("attribute", "magic"), 34103: ("condition", "frost"), 34104: ("condition", "sleep"),
    34200: ("condition", "death"), 34300: ("attribute", "thunder"),
    38000: ("attribute", "holy"), 38100: ("attribute", "fire"),
    50001: ("condition", "poison"), 50011: ("condition", "poison"),
    50020: ("condition", "frost"), 50030: ("condition", "sleep"), 50040: ("condition", "corruption"),
    50050: ("condition", "frenzy"),
    50102: ("condition", "bleed"), 50103: ("attribute", "thunder"), 50104: ("attribute", "holy"),
    50113: ("attribute", "fire"), 50114: ("condition", "poison"), 50116: ("attribute", "thunder"),
    53580: ("attribute", "fire"), 53590: ("attribute", "magic"),
    53670: ("condition", "corruption"), 53680: ("attribute", "holy"),
    52500: ("condition", "bleed"), 52520: ("condition", "poison"),
    52570: ("condition", "sleep"), 52550: ("attribute", "magic"),
    52450: ("condition", "sleep"), 52460: ("attribute", "thunder"),
    52400: ("attribute", "holy"), 52420: ("condition", "frost"),
}


def read_csv_rows(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        headers = next(reader)
        for raw in reader:
            if raw:
                yield headers, raw


def match_prefix(ctype, poi_key):
    s = str(ctype)
    p = str(poi_key)
    return s.startswith(p)


def has_same_base_icon(a, b):
    ka = a // 100 if a // 100 in POI_ICON_SCALE else a // 1000
    kb = b // 100 if b // 100 in POI_ICON_SCALE else b // 1000
    return ka == kb


def get_poi_key(ctype):
    k = ctype // 100 if ctype // 100 in POI_ICON_SCALE else ctype // 1000
    return k if k in POI_ICON_SCALE else None


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------

def load_data():
    data_dir = DATA_DIR
    # positions: ID -> 世界坐标 (两套标准坐标); name 列携带锚点语义
    pos_std, pos_tgh, pos_name = {}, {}, {}
    for _, r in read_csv_rows(os.path.join(data_dir, "positions.csv")):
        # 列: ID,name,areaNo,gridXNo,gridZNo,posX,posZ
        rid, gx, gz = int(r[0]), int(r[3]), int(r[4])
        px, pz = float(r[5]), float(r[6])
        name = r[1] if len(r) > 6 else ""
        pos_std[rid] = world_to_std(gx, px, gz, pz)
        pos_tgh[rid] = world_to_std(gx, px, gz, pz, tgh=True)
        pos_name[rid] = name
        # 主城地下室 Boss 锚点: param 里与楼顶同坐标, 原项目靠手工改 picX 分离
        # (坐标.csv 756=2124.78, 757=2290, 差 165pic ≈ 27 标准像素), 在此复刻
        if "Basement" in name:
            x, y = pos_std[rid]
            pos_std[rid] = (x + 27, y)

    # names (out/labels.csv 列: id,name,zh; 中文优先, 调试模式加 ID 前缀)
    names = {}
    for _, r in read_csv_rows(os.path.join(data_dir, "labels.csv")):
        text = r[2] or r[1] or str(r[0])
        names[int(r[0])] = text   # ID前缀由渲染层按运行配置添加

    # 变体ID -> 文本ID (fmg/variant_names.csv 第4列 name_id, 如 45514 -> 904978000)
    name_ids = {}
    vn = os.path.join(HERE, "fmg", "variant_names.csv")
    if os.path.exists(vn):
        with open(vn, encoding="utf-8-sig") as fp:
            rdr = csv.reader(fp)
            next(rdr, None)
            for row in rdr:
                if len(row) >= 4 and row[0].isdigit() and row[3].isdigit():
                    name_ids[int(row[0])] = row[3]
    # 事件链自动推导补充 (槽位规则 vN -> 8(N-1)0): variant_names 未收录的变体
    try:
        from event_extract import extract as _ev_extract
        for t, v in _ev_extract().items():
            name_ids.setdefault(t, str(v[1]))
    except Exception:
        pass
    # 夜Boss槽位(49XX) -> 长ID: m49_XX 事件映射
    try:
        from event_extract import night_boss_map as _nb_map
        for t, v in _nb_map().items():
            name_ids.setdefault(t, str(v))
    except Exception:
        pass

    # 强敌抗性数据 (out/boss_stats.csv: 变体ID -> NpcParam 抗性)
    boss_stats = {}
    bs_path = os.path.join(data_dir, "boss_stats.csv")
    if os.path.exists(bs_path):
        for _, r in read_csv_rows(bs_path):
            if r and r[0].isdigit():
                boss_stats[int(r[0])] = dict(zip(
                    ("npc_id", "name_id", "phys_n", "phys_b", "phys_s", "phys_t",
                     "mag", "fire", "thunder", "holy", "poise",
                     "blood", "poison", "rot", "frost", "name_en"), r[1:17]))

    # 夜王/地形字典 (code/bosses.csv, code/terrains.csv)
    bosses = {}
    for h, r in read_csv_rows(os.path.join(HERE, "bosses.csv")):
        text = r[2] or r[1]
        bosses[int(r[0])] = (r[1], text)
    terrains = {}
    for h, r in read_csv_rows(os.path.join(HERE, "terrains.csv")):
        text = r[2] or r[1]
        terrains[int(r[0])] = (r[1], text)
    # 主城类型字典 (code/castles.csv: type,name_zh; MOD 24 种城, 优先级高于 labels)
    castles = {}
    for h, r in read_csv_rows(os.path.join(HERE, "castles.csv")):
        castles[int(r[0])] = r[1]

    # constructs (out/constructs.csv 列: ID,MAP,type,coord_index)
    map_constructs: dict[int, list] = {}
    for _, r in read_csv_rows(os.path.join(data_dir, "constructs.csv")):
        map_id, ctype, coord_idx = int(r[1]), int(r[2]), int(r[3])
        map_constructs.setdefault(map_id, []).append(
            Construct(type=ctype, pos_index=coord_idx,
                      is_underground=coord_idx in TGH_UNDERGROUND_COORDS,
                      is_rooftop="Rooftop" in pos_name.get(coord_idx, ""),
                      is_basement="Basement" in pos_name.get(coord_idx, "")))

    # patterns
    patterns = []
    for h, r in read_csv_rows(os.path.join(data_dir, "map_patterns.csv")):
        d = dict(zip(h, r))
        pid = int(d["ID"])
        es = int(d["Special"])
        pd_ = pos_tgh if es == 4 else pos_std
        p = MapPattern(
            id=pid, nightlord=int(d["NightLord"]), earth_shifting=es,
            day1_boss=int(d["Day1Boss"]), day2_boss=int(d["Day2Boss"]),
            day1_pos=pd_[int(d["Day1Loc"])], day2_pos=pd_[int(d["Day2Loc"])],
            day1_extra=int(d["extra1"]), day2_extra=int(d["extra2"]),
            day2_pos_idx=int(d["Day2Loc"]),
            treasure=int(d["Treasure_800"]), rot_rew=int(d["RotRew_500"]),
            event_value=int(d["Event_30*0"]), event_flag=int(d["EventFlag"]),
            evpat_value=int(d["EvPat_30**"]), evpat_flag=int(d["EvPatFlag"]),
        )
        for c in map_constructs.get(pid, []):
            if c.pos_index in pd_:
                c.pos = pd_[c.pos_index]
                p.pos_constructions[c.pos] = c
        # 完整列表(按锚点ID去重): 标准坐标存在四舍五入碰撞(如锚点108/2108/2357 同坐标),
        # pos_constructions 按坐标做 key 会互相覆盖, 渲染必须用本列表
        p.constructs = list({c.pos_index: c for c in map_constructs.get(pid, [])
                             if c.pos_index in pd_}.values())
        patterns.append(p)

    # POI 可能出现的位置/类型索引
    poi_ctypes = set()
    for p in patterns:
        for c in p.pos_constructions.values():
            if any(str(c.type).startswith(str(pf)) for pf in POI_PREFIXES):
                poi_ctypes.add(c.type)
    all_poi_pos: dict[tuple, set] = {}
    possible_poi_types: dict[tuple, set] = {}
    all_poi_construct_type: dict[tuple, set] = {}
    for p in patterns:
        key = (p.earth_shifting, p.nightlord)
        for c in p.pos_constructions.values():
            if c.type in poi_ctypes:
                if p.earth_shifting == 4 and c.is_underground:
                    continue
                all_poi_pos.setdefault(key, set()).add(c.pos)
                all_poi_construct_type.setdefault(key, set()).add(c.type)
                possible_poi_types.setdefault((p.earth_shifting, p.nightlord, c.pos), set()).add(c.type)
    for (es, nl, pos) in list(possible_poi_types.keys()):
        all_poi_construct_type.setdefault((es, nl), set()).add(0)
        for p in patterns:
            if p.earth_shifting == es and p.nightlord == nl:
                con = p.pos_constructions.get(pos)
                if con is None or con.type not in poi_ctypes:
                    possible_poi_types[(es, nl, pos)].add(0)

    # 全局锚点字母表(按全部锚点ID排序, 跨种子稳定; 调试模式下用于点位交流)
    anchor_letters = {aid: letter for aid, letter in zip(
        sorted(pos_std), __import__("render").anchor_letter_iter())}

    return dict(patterns=patterns, names=names, name_ids=name_ids, boss_stats=boss_stats, bosses=bosses, terrains=terrains, castles=castles,
                anchor_letters=anchor_letters if DEBUG else None,
                pos_std=pos_std, pos_tgh=pos_tgh,
                all_poi_pos=all_poi_pos, possible_poi_types=possible_poi_types,
                poi_ctypes=poi_ctypes)


# ---------------------------------------------------------------------------
# 图像资源
# ---------------------------------------------------------------------------

def open_cv2_img(path, size=None):
    img = cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2RGB)
    if size is not None:
        img = cv2.resize(img, size, interpolation=cv2.INTER_CUBIC)
    return img


def open_pil_img(path, size=None):
    img = Image.open(path).convert("RGBA")
    if size is not None:
        img = img.resize(size, resample=Image.Resampling.BICUBIC)
    return img


class Assets:
    MATCH_EARTH_SHIFTING_SIZE = (100, 100)
    MATCH_EARTH_SHIFTING_REGION = (20, 20, 60, 60)
    MATCH_NIGHTLORD_SIZE = (300, 300)

    def __init__(self):
        self.map_bgs = {i: open_cv2_img(os.path.join(ASSET_DIR, "maps", f"{i}.jpg"))
                        for i in range(6)}
        self.poi_bg_index = {0: 0, 1: 0, 2: 0, 3: 0, 4: 4, 5: 0}

        # 夜王图标: 合成到统一底板, 取中心区域作模板 (模仿原项目)
        nl = [(None, open_pil_img(os.path.join(ASSET_DIR, "nightlord", "unk.png")))]
        for i in range(10):
            nl.append((i, open_pil_img(os.path.join(ASSET_DIR, "nightlord", f"{i}.png"))))
        for i in range(9):
            nl.append((i, open_pil_img(os.path.join(ASSET_DIR, "nightlord", f"e{i}.png"))))
        bg = open_pil_img(os.path.join(ASSET_DIR, "nightlord", "bg.png"))
        self.nightlord_icons = []
        for nightlord, icon in nl:
            target = bg.copy()
            cx, cy = target.size[0] // 2, target.size[1] // 2
            scale = 0.8 if nightlord is not None else 0.9
            icon = icon.resize((int(icon.size[0] * scale), int(icon.size[1] * scale)),
                               resample=Image.Resampling.BICUBIC)
            target.alpha_composite(icon, (cx - icon.size[0] // 2, cy - icon.size[1] // 2))
            target = target.resize((60, 60), resample=Image.Resampling.BICUBIC)
            w, h = target.size
            target = target.crop((int(w * 0.3), int(h * 0.3), int(w * 0.7), int(h * 0.7)))
            self.nightlord_icons.append((nightlord, np.array(target)[..., :3]))

        # POI 图标 (合成到 45x45 画布)
        self.poi_images: dict[int, Image.Image] = {}
        self.subicon_images: dict[tuple, Image.Image] = {}
        for k in ("attribute", "condition"):
            d = os.path.join(ASSET_DIR, k)
            for f in os.listdir(d):
                if f.endswith(".png"):
                    idx = int(f.split(".")[0])
                    key = ("attribute", ATTR_KEYS[idx]) if k == "attribute" else ("condition", COND_KEYS[idx])
                    self.subicon_images[key] = open_pil_img(os.path.join(d, f))
        for ctype in list(CTYPE_SUBICON_MAP.keys()):
            img = self._compose_poi_image(ctype, with_subicon=True)
            if img is not None:
                self.poi_images[ctype] = img
        # 无建筑
        self.poi_images[0] = Image.new("RGBA", STD_POI_SIZE, (0, 0, 0, 0))
        # POI 图标 (dx,dy,s) 变体缓存: 按 PIL 图对象 id 缓存, 检测时避免重复缩放合成
        self._poi_variant_cache: dict[int, list] = {}

    def poi_icon_variants(self, icon, key):
        """返回 [(dx, dy, rgba_ndarray)]: 图标在 45x45 画布上所有偏移/缩放变体。
        key==0 时为单个空变体。结果按图标缓存(位置无关部分全部预计算)。"""
        if key == 0:
            return [(0, 0, np.zeros((0, 0, 4), np.uint8))]
        cid = id(icon)
        if cid not in self._poi_variant_cache:
            variants = []
            for dx in range(-4, 5, 2):
                for dy in range(-4, 5, 2):
                    for s in np.linspace(0.9, 1.1, 5):
                        size = (int(STD_POI_SIZE[0] * s), int(STD_POI_SIZE[1] * s))
                        resized = icon.resize(size, resample=Image.Resampling.BICUBIC)
                        variants.append((dx, dy, np.array(resized)))
            self._poi_variant_cache[cid] = variants
        return self._poi_variant_cache[cid]

    def _compose_poi_image(self, ctype, with_subicon):
        poi_key = ctype // 100 if ctype // 100 in POI_ICON_SCALE else ctype // 1000
        if poi_key not in POI_ICON_SCALE:
            return None
        path = os.path.join(ASSET_DIR, "construct", f"{poi_key}.png")
        if not os.path.exists(path):
            return None
        off_x, off_y = POI_ICON_OFFSET.get(poi_key, (0, 0))
        x, y = STD_POI_SIZE[0] // 2, STD_POI_SIZE[1] // 2
        img = Image.new("RGBA", STD_POI_SIZE, (0, 0, 0, 0))
        icon = open_pil_img(path)
        scale = POI_ICON_SCALE[poi_key]
        size = (int(icon.size[0] * scale), int(icon.size[1] * scale))
        icon = icon.resize(size, resample=Image.Resampling.BICUBIC)
        img.alpha_composite(icon, (x - size[0] // 2 + off_x, y - size[1] // 2 + off_y))
        if with_subicon and ctype in CTYPE_SUBICON_MAP:
            sub = self.subicon_images.get(CTYPE_SUBICON_MAP[ctype])
            if sub is not None:
                ssize = (int(750 * 0.0195), int(750 * 0.0195))
                sub = sub.resize(ssize, resample=Image.Resampling.BICUBIC)
                img.alpha_composite(sub, (int(x - ssize[0] / 2 + 750 * 0.013),
                                          int(y + ssize[1] / 2 + 750 * -0.007)))
        return img


# ---------------------------------------------------------------------------
# 识别流程
# ---------------------------------------------------------------------------

def match_template(image, template, scales):
    best_val = float("inf")
    for scale in np.linspace(scales[0], scales[1], num=scales[2], endpoint=True):
        resized = cv2.resize(template, (int(template.shape[1] * scale), int(template.shape[0] * scale)))
        if resized.shape[0] > image.shape[0] or resized.shape[1] > image.shape[1]:
            continue
        result = cv2.matchTemplate(image, resized, cv2.TM_SQDIFF_NORMED)
        min_val, _, _, _ = cv2.minMaxLoc(result)
        best_val = min(best_val, min_val)
    return best_val


def align_image(img, target, region):
    """SIFT 特征对齐, 将 img 变换到 target 坐标系。"""
    x, y, w, h = region
    roi_img = cv2.cvtColor(img[y:y + h, x:x + w], cv2.COLOR_RGB2GRAY)
    roi_target = cv2.cvtColor(target[y:y + h, x:x + w], cv2.COLOR_RGB2GRAY)
    sift = cv2.SIFT_create()
    kp1, des1 = sift.detectAndCompute(roi_img, None)
    kp2, des2 = sift.detectAndCompute(roi_target, None)
    if des1 is None or des2 is None:
        raise ValueError("对齐失败: 区域内无特征点")
    flann = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=50))
    matches = flann.knnMatch(des1, des2, k=2)
    good = [m for m, n in matches if m.distance < 0.7 * n.distance]
    if len(good) < 4:
        raise ValueError(f"对齐失败: 匹配点不足 ({len(good)})")
    src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    src[:, 0, 0] += x
    src[:, 0, 1] += y
    dst[:, 0, 0] += x
    dst[:, 0, 1] += y
    matrix, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC)
    if matrix is None:
        raise ValueError("对齐失败: 无法计算变换矩阵")
    return cv2.warpAffine(img, matrix, (target.shape[1], target.shape[0]),
                          flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)


_EARTH_SHIFTING_PATCH_CACHE = {}


def match_earth_shifting(assets, img):
    """特殊地形识别。参考图的所有(缩放,偏移)切片是常量, 首次调用时预计算缓存,
    之后每次识别只需向量化比对, 免去逐切片的 Python 循环与重复缩放。"""
    size = Assets.MATCH_EARTH_SHIFTING_SIZE
    img = cv2.resize(img, size, interpolation=cv2.INTER_CUBIC)
    x, y, w, h = Assets.MATCH_EARTH_SHIFTING_REGION
    img = img[y:y + h, x:x + w].astype(np.int16)

    if "patches" not in _EARTH_SHIFTING_PATCH_CACHE:
        cache = {}
        for map_id, map_img in assets.map_bgs.items():
            blocks, blocks_ds = [], []
            for scale in np.linspace(0.95, 1.05, 7):
                msize = (int(size[0] * scale), int(size[1] * scale))
                m = cv2.resize(map_img, msize, interpolation=cv2.INTER_CUBIC)
                for dx in range(-5, 6):
                    for dy in range(-5, 6):
                        patch = m[y + dy:y + h + dy, x + dx:x + w + dx]
                        if patch.shape[:2] == (h, w):
                            blocks.append(patch.astype(np.int16))
            cache[map_id] = (np.array(blocks), np.array(blocks)[:, ::2, ::2])
        _EARTH_SHIFTING_PATCH_CACHE["patches"] = cache

    img_ds = img[::2, ::2].astype(np.int16)
    best_id, best_score = None, float("inf")
    for map_id, (blocks, blocks_ds) in _EARTH_SHIFTING_PATCH_CACHE["patches"].items():
        # 两阶段: 下采样(1/4 数据量)粗筛 top-8 候选, 仅对候选做全分辨率精确 median
        dds = np.abs(img_ds[None] - blocks_ds)
        dds[dds > 100] = 0
        coarse = np.linalg.norm(dds, axis=3).mean(axis=(1, 2))
        cand = np.argpartition(coarse, min(7, len(coarse) - 1))[:8]
        dd = np.abs(img[None].astype(np.int16) - blocks[cand])
        dd[dd > 100] = 0
        score = float(np.median(np.linalg.norm(dd, axis=3).reshape(len(cand), -1), axis=1).min())
        if score < best_score:
            best_score, best_id = score, map_id
    return best_id, best_score


def match_nightlord(assets, img):
    img = cv2.resize(img, Assets.MATCH_NIGHTLORD_SIZE, interpolation=cv2.INTER_CUBIC)
    h, w = img.shape[:2]
    img = img[-int(h * 0.15):-int(h * 0.05), int(w * 0.06):int(w * 0.16)]
    best_nl, best_score = None, float("inf")
    for nightlord, icon in assets.nightlord_icons:
        score = match_template(img, icon, (0.9, 1.1, 7))
        if score < best_score:
            best_score, best_nl = score, nightlord
    return best_nl, best_score


def match_poi(assets, img, map_bg, pos, possible_ctypes, info):
    half_x, half_y = STD_POI_SIZE[0] // 2, STD_POI_SIZE[1] // 2
    patch = img[pos[1] - half_y:pos[1] - half_y + STD_POI_SIZE[1],
                pos[0] - half_x:pos[0] - half_x + STD_POI_SIZE[0]]
    if patch.shape[:2] != STD_POI_SIZE[::-1]:
        return 0, 0.0
    bg = map_bg[pos[1] - half_y:pos[1] - half_y + STD_POI_SIZE[1],
                pos[0] - half_x:pos[0] - half_x + STD_POI_SIZE[0]]

    # 大类: 把图标合成到底图上比对
    DS = (16, 16)
    patch_ds = cv2.resize(patch, DS, interpolation=cv2.INTER_CUBIC)
    h, w, _ = patch_ds.shape
    h0, h1, w0, w1 = int(h * 0.2), int(h * 0.8), int(w * 0.2), int(w * 0.6)
    patch_roi = patch_ds[h0:h1, w0:w1]
    bg_pil = Image.fromarray(bg).convert("RGBA")

    # 收集该位置候选大类
    cate_keys = set()
    for ctype in possible_ctypes:
        k = get_poi_key(ctype)
        if k is not None:
            cate_keys.add(k)
    if not possible_ctypes or 0 in possible_ctypes:
        cate_keys.add(0)

    bg_arr = bg  # 底图裁剪(ndarray), 各大类共用, 不再逐类转 PIL

    best_key, best_key_score = 0, float("inf")
    for key in cate_keys:
        # 该大类的代表图标: 从候选 ctype 里取同大类的, 带子图标渲染
        icon = None
        if key != 0:
            for ct in sorted(possible_ctypes):
                if get_poi_key(ct) == key:
                    icon = assets.poi_images.get(ct) or assets._compose_poi_image(ct, with_subicon=ct in CTYPE_SUBICON_MAP)
                    if icon is not None:
                        break
            if icon is None:
                continue
        # (dx,dy,s) 变体的图标位图与 alpha 掩码按图标缓存(位置无关), 合成用 numpy
        variants = assets.poi_icon_variants(icon, key)
        targets = []
        for (dx, dy, rgba) in variants:
            out = bg_arr.copy()
            ih, iw = rgba.shape[:2]
            x0, y0 = max(0, dx), max(0, dy)
            x1, y1 = min(bg_arr.shape[1], dx + iw), min(bg_arr.shape[0], dy + ih)
            if x1 > x0 and y1 > y0:
                sub = rgba[y0 - dy:y1 - dy, x0 - dx:x1 - dx]
                a = (sub[..., 3:4].astype(np.float32) / 255.0)
                region = out[y0:y1, x0:x1].astype(np.float32)
                out[y0:y1, x0:x1] = (region * (1 - a) + sub[..., :3] * a).astype(np.uint8)
            arr = cv2.resize(out, DS, interpolation=cv2.INTER_CUBIC)
            targets.append(arr[h0:h1, w0:w1])
        targets = np.array(targets, dtype=np.float32)
        diffs = np.mean((targets - patch_roi.astype(np.float32)) ** 2, axis=(1, 2, 3))
        score = diffs.min()
        if score < best_key_score:
            best_key_score, best_key = score, key

    # 子图标: 模板匹配属性/异常角标, 确定具体 ctype
    best_ctype, best_sub_score = best_key * 100 if best_key else 0, 0.0
    sub_candidates = sorted(ct for ct in possible_ctypes
                            if has_same_base_icon(ct, best_key * 100 if best_key else 0)
                            or (best_key == 0 and ct == 0))
    if best_key != 0 and sub_candidates:
        DS2 = (64, 64)
        patch2 = cv2.resize(patch, DS2, interpolation=cv2.INTER_CUBIC)
        h, w, _ = patch2.shape
        patch2 = cv2.GaussianBlur(patch2[int(h * 0.4):, int(w * 0.4):], (3, 3), 0)
        best_ctype, best_sub_score = sub_candidates[0], float("inf")
        for ct in sub_candidates:
            sub_key = CTYPE_SUBICON_MAP.get(ct)
            if sub_key is None:
                continue
            sub = assets.subicon_images.get(sub_key)
            if sub is None:
                continue
            for s in np.linspace(0.9, 1.1, 5):
                size = (int(DS2[0] * s * 0.3), int(DS2[1] * s * 0.3))
                t = sub.resize(size, resample=Image.Resampling.NEAREST).convert("RGB")
                t = np.array(t)
                t = cv2.GaussianBlur(t, (3, 3), 0)
                th, tw = t.shape[:2]
                t = t[int(th * 0.1):int(th * 0.9), int(tw * 0.1):int(tw * 0.9)]
                if t.shape[0] > patch2.shape[0] or t.shape[1] > patch2.shape[1]:
                    continue
                res = cv2.matchTemplate(patch2, t, cv2.TM_SQDIFF_NORMED)
                val = cv2.minMaxLoc(res)[0]
                if val < best_sub_score:
                    best_sub_score, best_ctype = val, ct
        if best_sub_score > SUBICON_TEMPLATE_MATCH_THRESHOLD:
            best_ctype = next((ct for ct in sub_candidates if ct not in CTYPE_SUBICON_MAP), sub_candidates[0])
    return best_ctype, best_key_score * (best_sub_score if best_sub_score < float("inf") else 1.0)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def load_map_image(path):
    """读取图片; 2560x1440 的完整游戏截图按预设坐标裁出地图区域, 其余原样使用。"""
    img = open_cv2_img(path)
    if img.shape[1] == 2560 and img.shape[0] == 1440:
        (x, y), (w, h) = SCREEN_CROP
        img = img[y:y + h, x:x + w]
    return img


def detect_image(info, assets, source, tag=""):
    """识别单张图片, source 可为文件路径或 BGR/RGB ndarray, 返回 (最佳种子ID | None, 完整结果列表)。"""
    img = load_map_image(source) if isinstance(source, str) else source

    # 1. 特殊地形
    es, es_score = match_earth_shifting(assets, img)
    es_str = "识别失败" if es_score > EARTH_SHIFTING_ERROR_THRESHOLD else str(es)
    print("\n[特殊地形] %s (score=%.2f)" % (es_str, es_score))
    if es_score > EARTH_SHIFTING_ERROR_THRESHOLD:
        print("特殊地形识别置信度过低, 跳过该图")
        return None, []

    # 2. 夜王 (置信度不足时按未知处理, 不限定夜王池)
    img_std = cv2.resize(img, STD_MAP_SIZE, interpolation=cv2.INTER_CUBIC)
    nightlord, nl_score = match_nightlord(assets, img_std)
    if nl_score > NIGHTLORD_SCORE_THRESHOLD:
        print("[夜王] %s (score=%.4f, 置信度不足按未知处理)" % (nightlord, nl_score))
        nightlord = None
    else:
        nl_name = info["bosses"].get(nightlord, (str(nightlord),))[0] if nightlord is not None else "?"
        print("[夜王] %s / %s (score=%.4f)" % (nightlord, nl_name, nl_score))

    # 3. 对齐
    bg_path = os.path.join(ASSET_DIR, "maps_poi_match", f"{assets.poi_bg_index[es]}.jpg")
    map_bg = open_cv2_img(bg_path, STD_MAP_SIZE)
    try:
        region = (int(750 * 0.2), int(750 * 0.2), int(750 * 0.6), int(750 * 0.6))
        img_std = align_image(img_std, map_bg, region)
        print("[对齐] 成功")
    except Exception as e:
        print("[对齐] 失败(%s), 使用未对齐图像继续" % e)
    if SAVE_DEBUG:
        cv2.imwrite(os.path.join(OUT_DIR, "aligned%s.jpg" % tag), cv2.cvtColor(img_std, cv2.COLOR_RGB2BGR))

    # 4. POI 识别
    nightlords = [nightlord] if nightlord is not None else sorted(
        {p.nightlord for p in info["patterns"]})
    all_poi_pos = set()
    for nl in nightlords:
        all_poi_pos.update(info["all_poi_pos"].get((es, nl), set()))
    poi_pos = sorted(all_poi_pos)
    print("[POI] 待匹配点位: %d" % len(poi_pos))

    poi_result: dict[Position, int] = {}
    debug_img = img_std.copy()
    for pos in poi_pos:
        possible = set()
        for nl in nightlords:
            possible.update(info["possible_poi_types"].get((es, nl, pos), set()))
        ctype, score = match_poi(assets, img_std, map_bg, pos, possible, info)
        poi_result[pos] = ctype
        icon = assets.poi_images.get(ctype if ctype in assets.poi_images else 0)
        if icon is not None:
            arr = np.array(icon)[..., :3]
            debug_img[pos[1] - 22:pos[1] + 23, pos[0] - 22:pos[0] + 23] = arr
        cv2.circle(debug_img, pos, 2, (255, 0, 0), 2)
    if SAVE_DEBUG:
        cv2.imwrite(os.path.join(OUT_DIR, "poi_result%s.jpg" % tag), cv2.cvtColor(debug_img, cv2.COLOR_RGB2BGR))

    # 5. 种子匹配打分 (与原项目相同的计分规则)
    results = []
    for p in info["patterns"]:
        if p.earth_shifting != es:
            continue
        if nightlord is not None and p.nightlord != nightlord:
            continue
        score, error = 0, 0
        for pos, ctype in poi_result.items():
            expect = p.pos_constructions.get(pos)
            expect_ctype = expect.type if expect else 0
            sub = CTYPE_SUBICON_MAP.get(ctype)
            expect_sub = CTYPE_SUBICON_MAP.get(expect_ctype)
            if not has_same_base_icon(ctype, expect_ctype):
                if sub == expect_sub:
                    score, error = score + 1, error + 3
                else:
                    error += 10
            else:
                if sub == expect_sub:
                    score, error = score + 10, error + 0
                elif sub or expect_sub:
                    error += 10
                else:
                    score, error = score + 3, error + 1
        results.append((error, -score, p))
    results.sort(key=lambda x: (x[0], x[1]))
    return results[0][2].id if results else None, results


def print_results(info, results):
    names = info["names"]
    bosses, terrains = info["bosses"], info["terrains"]
    shown = results[:TOPK]
    ties = [r for r in results if (r[0], r[1]) == (shown[0][0], shown[0][1])]
    if len(ties) > 1:
        print("\n注意: 有 %d 个种子与第一名完全并列 (error=%d), 通常需夜王头像区分: %s"
              % (len(ties), shown[0][0], [p.id for _, _, p in ties]))
    print("\n===== 匹配结果 (top %d) =====" % TOPK)
    for i, (error, neg_score, p) in enumerate(shown):
        nl_name = bosses.get(p.nightlord, ("?", "?"))[1]
        es_name = terrains.get(p.earth_shifting, ("?", "?"))[1]
        print("\n#%d 种子 %d  (error=%d score=%d)" % (i + 1, p.id, error, -neg_score))
        print("  夜王: %s | 特殊地形: %s" % (nl_name, es_name))
        print("  Day1 Boss: %s @ %s" % (names.get(p.day1_boss, p.day1_boss), p.day1_pos))
        print("  Day2 Boss: %s @ %s" % (names.get(p.day2_boss, p.day2_boss), p.day2_pos))
        if p.day1_extra >= 0:
            print("  Day1 额外: %s" % names.get(p.day1_extra, p.day1_extra))
        if p.day2_extra >= 0:
            print("  Day2 额外: %s" % names.get(p.day2_extra, p.day2_extra))
        print("  宝藏: %s | 腐败庇佑: %s" % (p.treasure or "-", p.rot_rew or "-"))
        if p.event_flag:
            print("  事件: %s %s" % (names.get(p.event_flag, p.event_flag),
                                     names.get(p.event_value, p.event_value)))


def main():
    # 批量识别 input/ 下所有 jpg, 输出 result.txt (每行: 图片名称_种子ID)
    files = sorted(f for f in os.listdir(INPUT_DIR) if f.lower().endswith(".jpg"))
    if not files:
        print("input/ 下没有 jpg 文件")
        return
    os.makedirs(OUT_DIR, exist_ok=True)

    t0 = time.time()
    print("加载数据...")
    info = load_data()
    assets = Assets()
    print("数据加载完成 (%.2fs), 种子数: %d" % (time.time() - t0, len(info["patterns"])))

    rows = []
    for f in files:
        print("\n======== %s ========" % f)
        tag = "_" + os.path.splitext(f)[0]
        try:
            best_id, results = detect_image(info, assets, os.path.join(INPUT_DIR, f), tag)
        except Exception as e:
            print("识别失败: %s" % e)
            best_id, results = None, []
        if results:
            print_results(info, results)
        else:
            print("无法识别")
        rows.append((f, best_id if best_id is not None else -1))

    with open(RESULT_PATH, "w", encoding="utf-8") as fp:
        for f, pid in rows:
            name = os.path.splitext(f)[0]
            fp.write("%s_%d\n" % (name, pid))
    print("\n结果已写入 %s (%d 张, 识别失败以 -1 表示)" % (RESULT_PATH, len(rows)))
    print("调试图片已保存到 %s" % OUT_DIR)


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
