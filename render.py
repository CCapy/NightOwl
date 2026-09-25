#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
悬浮层内容渲染: 把种子信息(夜圈/Boss/建筑名/宝藏布局/图标)画到一张 RGBA 图上。
输出尺寸 1000x1000, 对应屏幕上 1450,230 起的地图区域。
渲染用 PIL, 由 overlay.py 显示。
"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ASSET_DIR = os.path.join(HERE, "assets")

SIZE = 1000  # 悬浮窗边长 (对应 750 标准坐标 x 4/3)
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"

def anchor_letter(n):
    """0->A, 25->Z, 26->AA ..."""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def anchor_letter_iter():
    """全局锚点字母序列生成器: A, B, ..., Z, AA, AB, ..."""
    i = 0
    while True:
        yield anchor_letter(i)
        i += 1

# 标准坐标 -> 本图坐标
def s(v):
    return int(v * SIZE / 750)

# 颜色
C_SEED = (255, 213, 74, 255)
C_SUB = (142, 202, 230, 255)
C_BOSS = (255, 138, 61, 255)
C_NIGHT = (210, 210, 255, 255)
C_POI = (200, 220, 150, 255)
C_CASTLE = (255, 255, 0, 255)
C_TOWER = (210, 255, 200, 255)
C_TGH_IN = (200, 255, 200, 255)
C_TGH_OUT = (255, 200, 200, 255)
C_EVENT = (255, 150, 200, 255)
C_WHITE = (255, 255, 255, 255)
OUTLINE = (16, 16, 24, 255)

# 图标缓存
_icon_cache = {}


def icon(name, size):
    key = (name, size)
    if key not in _icon_cache:
        path = os.path.join(ASSET_DIR, "%s.png" % name)
        if not os.path.exists(path):
            _icon_cache[key] = None
        else:
            _icon_cache[key] = Image.open(path).convert("RGBA").resize((size, size), Image.Resampling.BICUBIC)
    return _icon_cache[key]


def treasure_layout(treasure_id):
    path = os.path.join(ASSET_DIR, "treasures", "treasure_%d.png" % treasure_id)
    if not os.path.exists(path):
        return None
    return Image.open(path).convert("RGBA")


def font(size, bold=False):
    return ImageFont.truetype(FONT_PATH, size)


def _simple_text_image(text) -> Image.Image:
    """单行提示文字的透明图 (识别中/失败提示等)。"""
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    draw_text(d, (18, 18), text, 20, C_SEED)
    return img


def draw_text(img_draw, pos, text, size, color, anchor="la", outline=OUTLINE):
    img_draw.text(pos, text, font=font(size), fill=color, anchor=anchor,
                  stroke_width=max(1, size // 10), stroke_fill=outline)


def paste_center(img, ic, pos):
    img.alpha_composite(ic, (pos[0] - ic.size[0] // 2, pos[1] - ic.size[1] // 2))


# 大空洞神授塔 Boss 锚点 -> (塔侧, 楼层)  [原项目语义: LT=左上塔, RB=右下塔]
TGH_TOWER_POS = {1111: ("RB", 1), 1112: ("RB", 2), 1113: ("RB", 3),
                 1114: ("LT", 1), 1115: ("LT", 2), 1116: ("LT", 3)}

# 主城家族: 4941x/4942x/4943x -> 城名 (变体只改敌人构成, 城名按家族)
CASTLE_FAMILY_NAMES = {4941: "失乡城", 4942: "山妖城", 4943: "熔炉城"}


def castle_label(ctype) -> str:
    return CASTLE_FAMILY_NAMES.get(ctype // 10)
# 原项目的 Boss 分类图标 (仅原版 type; MOD 新 Boss 只画名字)
# 按 ctype//10 (去掉 variationId) 匹配, 兼容 MOD 修改变体编号的情况
BOSS1_BASES = {4651, 4657, 4659, 4662, 4665, 4669, 4671, 4672, 4677, 4681,
               4682, 4686, 4688, 4691, 4695, 4551, 4655}
BOSS2_BASES = {4652, 4653, 4654, 4656, 4663, 4664, 4666, 4667, 4668, 4674, 4687, 4658}


def is_field_boss(ctype):
    """野Boss判定 (与 render 内一致): 45/46 开头且非 460 系; 4551x 黑刀刺客; 排除 46780"""
    st = str(ctype)
    return st.startswith(("45", "46")) and not st.startswith("460") and \
        (st.startswith("4551") or ctype // 1000 != 45) and ctype != 46780


def is_evergaol_anchor(pos_index):
    """永恒监狱锚点: 基础 601-607, DLC 镜像 2601-2607"""
    return 601 <= pos_index <= 607 or 2601 <= pos_index <= 2607


def boss_category(c):
    """强敌分类: 监狱锚点上=监牢; 野锚上按家族分 红名(BOSS2)/白名(BOSS1)/白名兜底"""
    if is_evergaol_anchor(c.pos_index):
        return "jail"
    return "boss_strong" if c.type // 10 in BOSS2_BASES else "boss"


BOSS_PANEL_W = 300  # 侧边信息栏宽 (图像坐标)

DISPLAY_DEFAULTS = {"sidebar": False, "pos": False, "long_id": False, "short_id": False,
                    "name": True, "show_icons": False}

# 文字样式 (config.json "style" 段可覆盖): 按类型划分
# 每类: color 文字色(RGB), outline 描边色(RGB), size 字号, offset 该类标签偏移[dx,dy]
STYLE_DEFAULTS = {
    "poi":         {"color": [200, 220, 150], "outline": [0, 0, 0], "size": 14, "offset": [0, 0]},
    "blood":       {"color": [255, 255, 0],   "outline": [0, 0, 0], "size": 14, "offset": [0, 0]},
    "boss":        {"color": [255, 255, 255], "outline": [0, 0, 0], "size": 16, "offset": [0, 0]},
    "boss_strong": {"color": [255, 80, 80],   "outline": [0, 0, 0], "size": 16, "offset": [0, 0]},
    "jail":        {"color": [110, 170, 255], "outline": [0, 0, 0], "size": 16, "offset": [0, 0]},
    "castle":      {"color": [255, 255, 0],   "outline": [0, 0, 0], "size": 16, "offset": [0, 0]},
    "label_offset": [0, 0],   # 全局标签偏移(叠加在各类 offset 之上)
}
HL_DEFAULTS = {"hl_weak": [255, 110, 40],    # 弱点高亮 (橙红)
               "hl_resist": [90, 140, 255]}  # 抗性高亮 (蓝)


def _hl_color(style, key):
    c = (style or {}).get(key) or HL_DEFAULTS[key]
    return tuple(c) + (255,)

STYLE_KEYS = ("poi", "blood", "boss", "boss_strong", "jail", "castle")
STYLE_LABELS = {"poi": "据点", "blood": "血瓶", "boss": "白名强敌", "boss_strong": "红名强敌",
                "jail": "监牢", "castle": "主城"}


def _style_cat(style, key):
    """读取某类完整样式 (颜色/描边/字号/偏移)"""
    sty = dict(STYLE_DEFAULTS[key])
    sty.update((style or {}).get(key, {}))
    c = tuple(sty.get("color", [255, 255, 255])) + (255,)
    o = tuple(sty.get("outline", [0, 0, 0])) + (255,)
    off = sty.get("offset", [0, 0])
    return c, o, int(sty.get("size", 14)), s(off[0]), s(off[1])


def _label_parts(c, names, name_ids, letters, disp, with_long=True):
    """按勾选项拼标签: 位置/长ID/短ID/名称, 只含勾选的部分"""
    parts = []
    if disp.get("pos") and letters:
        parts.append(letters.get(c.pos_index, "?"))
    if with_long and disp.get("long_id") and name_ids:
        parts.append(str(name_ids.get(c.type, "0")))
    if disp.get("short_id"):
        parts.append(str(c.type))
    if disp.get("name"):
        parts.append(str(names.get(c.type, c.type)))
    return "/".join(parts)


def boss_list_image(pattern, names, name_ids, anchor_letters, width=BOSS_PANEL_W,
                    display=None):
    """侧边信息栏: 每行按勾选项拼 Boss 信息, 按位置字母排序"""
    disp = dict(DISPLAY_DEFAULTS, **(display or {}))
    img = Image.new("RGBA", (width, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ni = name_ids or {}
    letters = anchor_letters or {}
    rows = sorted((c for c in getattr(pattern, "constructs", [])
                   if is_field_boss(c.type)),
                  key=lambda c: letters.get(c.pos_index, "~"))
    yy = 16
    for c in rows:
        draw_text(d, (10, yy), _label_parts(c, names, ni, letters, disp), 14,
                  (0, 255, 255, 255), anchor="la")
        yy += 24
    return img


STAT_LABELS = [("phys_n", "普"), ("phys_b", "打"), ("phys_s", "斩"), ("phys_t", "刺"),
               ("mag", "魔"), ("fire", "火"), ("thunder", "雷"), ("holy", "圣"),
               ("poise", "韧"), ("blood", "血"), ("poison", "毒"), ("rot", "腐"), ("frost", "冰")]
# 每列独立配色, 便于快速扫读
STAT_COLORS = [(150, 220, 255, 255), (255, 190, 90, 255), (170, 255, 170, 255), (255, 150, 150, 255),
               (140, 180, 255, 255), (255, 120, 80, 255), (255, 230, 100, 255), (255, 255, 200, 255),
               (220, 180, 255, 255), (255, 120, 160, 255), (140, 255, 140, 255), (200, 120, 255, 255),
               (160, 240, 255, 255)]


def _fmt_stats(s):
    """一行抗性文本: 普通1/打击0.9/... | 魔.8 火.6 ... | 韧性80 血112 ..."""
    if not s or not s.get("phys_n"):
        return None

    def v(k):
        x = str(s.get(k, ""))
        return "免疫" if x == "999" else x
    return "%s | %s | %s" % (
        "/".join("%s%s" % (lb, v(k)) for k, lb in STAT_LABELS[:4]),
        " ".join("%s%s" % (lb, v(k)) for k, lb in STAT_LABELS[4:8]),
        " ".join("%s%s" % (lb, v(k)) for k, lb in STAT_LABELS[8:]))


def stats_panel_image(pattern, names, stats, castles, bosses=None, width=600, height=None, style=None,
                     font_size=14):
    """屏幕左侧强敌数据面板: 表格布局, 表头只画一次, 每行仅数值"""
    img = Image.new("RGBA", (width, height or SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    stats = stats or {}
    castles = castles or {}

    def boss_cat(c):
        return boss_category(c)

    # 列: 名字 + 13 项数值 (列宽随字号缩放)
    fs = int(font_size)
    NAME_W = fs * 11
    COL_W = int(fs * 3.3)
    cols = [lb for _k, lb in STAT_LABELS]
    x0 = 12
    vxs = [x0 + NAME_W + i * COL_W for i in range(len(cols))]

    def v(s, k):
        x = str(s.get(k, ""))
        if not x:
            return "-"
        if x == "999":
            return "免"
        try:  # 最多2位有效数字, 去尾零 (1.20->1.2, 0.90->0.9)
            return ("%g" % float(x))
        except ValueError:
            return x

    ROW_H = fs + 6   # 行高, 数值列在行内居中

    def cell_center(i):
        """第 i 列的单元格中心"""
        return (vxs[i] + (COL_W - 4) // 2, yy + ROW_H // 2)

    def draw_stat_cells(s):
        """画一行数值: 弱点/抗性并列全标, 半透明背景色块, 数值列居中"""
        weak, resist = group_hl(s, [k for k, _ in STAT_LABELS[:4]])
        w2, r2 = group_hl(s, [k for k, _ in STAT_LABELS[4:8]])
        weak |= w2
        resist |= r2
        wc, rc = _hl_color(style, "hl_weak"), _hl_color(style, "hl_resist")
        for i, (k, _lb) in enumerate(STAT_LABELS):
            cx, cy = cell_center(i)
            if k in weak:
                d.rectangle((vxs[i] - 2, yy, vxs[i] + COL_W - 4, yy + ROW_H), fill=wc[:3] + (80,))
                draw_text(d, (cx, cy), v(s, k), fs, (255, 255, 255, 255),
                          anchor="mm", outline=(0, 0, 0, 255))
            elif k in resist:
                d.rectangle((vxs[i] - 2, yy, vxs[i] + COL_W - 4, yy + ROW_H), fill=rc[:3] + (80,))
                draw_text(d, (cx, cy), v(s, k), fs, (255, 255, 255, 255), anchor="mm")
            else:
                draw_text(d, (cx, cy), v(s, k), fs, STAT_COLORS[i], anchor="mm")

    def group_hl(s, keys):
        """组内最大(弱点)/最小(抗性)的键集合"""
        vals = []
        for k in keys:
            x = str(s.get(k, ""))
            if x and x != "999":
                try:
                    vals.append((float(x), k))
                except ValueError:
                    pass
        if len(vals) < 2:
            return set(), set()
        mx = max(v for v, _ in vals)
        mn = min(v for v, _ in vals)
        return {k for v, k in vals if v == mx}, {k for v, k in vals if v == mn}

    yy = 16
    # 表头 (每列独立颜色)
    draw_text(d, (x0, yy), "名字", fs - 1, (160, 160, 160, 255), anchor="la")
    for i, lb in enumerate(cols):
        draw_text(d, (vxs[i] + (COL_W - 4) // 2, yy + (fs + 2) // 2), lb, fs - 1,
                  STAT_COLORS[i], anchor="mm")
    yy += 20
    # 分组行: 野锚强敌; 楼顶/地下室 Boss 归入主城
    def is_castle_side(c):
        return c.is_rooftop or c.is_basement or (
            str(c.type).startswith("494") and c.type != 49400)

    groups = [("红名强敌", "boss_strong"), ("白名强敌", "boss"), ("监牢", "jail")]
    for title, key in groups:
        rows = [c for c in getattr(pattern, "constructs", [])
                if is_field_boss(c.type) and not is_castle_side(c) and boss_cat(c) == key]
        if not rows:
            continue
        color = _style_cat(style, key)[0]
        draw_text(d, (x0, yy), title, fs + 1, color, anchor="la")
        yy += 22
        for c in rows:
            if yy > img.height - 24:
                return img
            nm = str(names.get(c.type, c.type))
            s = stats.get(c.type) or {}
            draw_text(d, (x0, yy), nm, fs, (255, 255, 255, 255), anchor="la")
            if not s.get("phys_n"):
                yy += ROW_H
                continue          # 城堡本身无抗性, 不画 "-"
            draw_stat_cells(s)
            yy += ROW_H
        yy += 6
    # 主城: 城名 + 楼顶/地下室 Boss
    castle_rows = [c for c in getattr(pattern, "constructs", []) if is_castle_side(c)]
    if castle_rows:
        draw_text(d, (x0, yy), "主城", fs + 1, _style_cat(style, "castle")[0], anchor="la")
        yy += 22
        for c in castle_rows:
            if yy > img.height - 24:
                break
            if str(c.type).startswith("494"):
                nm = str(castles.get(c.type) or names.get(c.type, c.type))
            else:
                nm = ("楼顶 " if c.is_rooftop else "地下室 ") + str(names.get(c.type, c.type))
            s = stats.get(c.type) or {}
            draw_text(d, (x0, yy), nm, fs, (255, 255, 255, 255), anchor="la")
            if not s.get("phys_n"):
                yy += ROW_H
                continue          # 城堡本身无抗性, 不画 "-"
            draw_stat_cells(s)
            yy += ROW_H
        yy += 6
    # 夜晚Boss (Day1/Day2) 与 夜王
    for tag, bt in (("Day1", pattern.day1_boss), ("Day2", pattern.day2_boss)):
        if bt and bt > 0:
            s = stats.get(bt) or {}
            draw_text(d, (x0, yy), "%s %s" % (tag, names.get(bt, bt)), fs,
                      (255, 255, 255, 255), anchor="la")
            draw_stat_cells(s)
            yy += ROW_H
    nl = getattr(pattern, "nightlord", 0)
    if 0 <= nl <= 9:
        s = stats.get(4900 + nl) or {}
        nl_name = (bosses or {}).get(nl, ("?", "?"))[1]
        draw_text(d, (x0, yy), "夜王 %s" % nl_name, fs,
                  (255, 200, 200, 255), anchor="la")
        draw_stat_cells(s)
        yy += 20
    return img


def render(pattern, names, base=None, note=None, header=(), anchor_letters=None,
           name_ids=None, display=None, castles=None, style=None) -> Image.Image:
    """渲染一张种子的标注层 (SIZE x SIZE RGBA)。
    base: 识别时的地图截图(保留参数, 当前未用); header: 种子号下方的说明行;
    anchor_letters: 锚点字母表; name_ids: 变体ID -> 文本ID 映射;
    display: 显示配置 {sidebar, pos, long_id, short_id, name}。"""
    disp = dict(DISPLAY_DEFAULTS, **(display or {}))
    sidebar = disp.get("sidebar", False)
    ni = name_ids or {}
    _off = list((style or {}).get("label_offset", STYLE_DEFAULTS["label_offset"]))
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 宝藏布局图 (原样使用, (375,375) 为中心点, 同原项目)
    t_img = treasure_layout(pattern.treasure * 10 + pattern.earth_shifting) if pattern.treasure else None
    if t_img is not None:
        t_img = t_img.resize((s(800), s(800)), Image.Resampling.BICUBIC)
        paste_center(img, t_img, (s(375), s(375)))

    # 癫火布局 (事件值3080: 癫火塔事件, 叠加对应 Frenzy 布局图)
    if pattern.event_value == 3080 and pattern.evpat_flag:
        fp = os.path.join(ASSET_DIR, "frenzy", "Frenzy_%d.png" % pattern.evpat_flag)
        if os.path.exists(fp):
            f_img = Image.open(fp).convert("RGBA").resize((s(800), s(800)), Image.Resampling.BICUBIC)
            paste_center(img, f_img, (s(375), s(375)))

    # 腐败森林庇佑: 按 rot_rew 值的专用坐标表绘制 (原项目 ROTREW_POS, 位置固定不随夜圈)
    ROTREW_POS = {1046300590: (477, 583), 1057300590: (600, 452), 1047300590: (423, 500)}
    if pattern.rot_rew in ROTREW_POS:
        ic = icon("rot_rew", s(40))
        if ic is not None:
            rx, ry = ROTREW_POS[pattern.rot_rew]
            paste_center(img, ic, (s(rx), s(ry)))
            draw_text(d, (s(rx), s(ry + 20)), "庇佑", 14, (255, 200, 200, 255), anchor="ma")

    # Day1 / Day2 夜圈 (原版素材, 半透明圆环)
    for pos, label, sub in (
            (pattern.day1_pos, "Day1 " + str(names.get(pattern.day1_boss, pattern.day1_boss)), pattern.day1_extra),
            (pattern.day2_pos, "Day2 " + str(names.get(pattern.day2_boss, pattern.day2_boss)), pattern.day2_extra)):
        x, y = s(pos[0]), s(pos[1])
        ic = icon("night_circle", s(112))
        if ic is not None:
            paste_center(img, ic, (x, y))
        draw_text(d, (x, y + s(42)), label, 18, C_NIGHT, anchor="ma")
        if sub >= 0:
            draw_text(d, (x, y + s(64)), "额外 " + str(names.get(sub, sub)), 14, C_WHITE, anchor="ma")

    # 建筑标注 (用完整列表: 坐标碰撞的建筑都要画, 不能按坐标去重)
    day2_lefttop = getattr(pattern, "day2_pos_idx", None) == 12000
    constructs = getattr(pattern, "constructs", None) or list(pattern.pos_constructions.values())
    # 同坐标构造的标签错开: Boss排最前(不偏移), 其余(血瓶/POI/事件等)依次向下偏移,
    # 否则同锚点的 Boss 名与血瓶/小教堂标签会叠在一起 (如 HP 锚点 755)
    _by_pos = {}
    for c in constructs:
        _by_pos.setdefault(c.pos, []).append(c)
    for _lst in _by_pos.values():
        _lst.sort(key=lambda c: 0 if is_field_boss(c.type) else 1)
    _pos_slot = {id(c): i for _lst in _by_pos.values() for i, c in enumerate(_lst)}
    anchor_labels = anchor_letters or {}  # 全局锚点字母表
    letter_marks = []  # 待画的字母记号 (x, y, 字母), 循环结束后统一防重叠绘制
    label_rects = []   # 已画文字的近似矩形 (x0,y0,x1,y1), 字母记号避开它们

    def dt(px_, py_, text, size, color, anchor="ma", outline=OUTLINE, cat=None):
        """画文字并记录占位矩形; 应用 全局偏移 + 类别偏移, 类别样式覆盖字号颜色"""
        if cat is not None:
            color, outline, size, dx, dy = _style_cat(style, cat)
            px_, py_ = px_ + dx, py_ + dy
        px_, py_ = px_ + s(_off[0]), py_ + s(_off[1])
        draw_text(d, (px_, py_), text, size, color, anchor=anchor, outline=outline)
        if text:
            w = s(size) * max(1, len(str(text))) // 2
            label_rects.append((px_ - w, py_, px_ + w, py_ + s(size)))

    for c in constructs:
        pos = c.pos
        x, y = s(pos[0]), s(pos[1])
        y += s(28) * _pos_slot[id(c)]   # 同坐标第2+个构造整体下移(留足行距, 避免与Boss标签视觉重叠)
        ctype = c.type
        st = str(ctype)
        name = names.get(ctype)

        def startswith(*prefixes):
            return any(st.startswith(str(p)) for p in prefixes)

        def boss_text():
            """Boss标签: 侧边模式地图上只标位置字母; 否则按勾选项拼标签(全不勾时保底名称)"""
            if sidebar:
                return anchor_labels.get(c.pos_index, "?")
            t = _label_parts(c, names, ni, anchor_labels, disp)
            return t or str(name)

        def tagged(text):
            """非Boss标签: 按勾选项拼(位置/短ID/名称), 名称用传入文本(主城=castles表名)"""
            parts = []
            if disp.get("pos") and anchor_labels:
                parts.append(anchor_labels.get(c.pos_index, "?"))
            if disp.get("short_id"):
                parts.append(str(c.type))
            if disp.get("name"):
                parts.append(str(text))
            return "/".join(parts)

        # 野 Boss: 监牢(不在两类列表)只显示文字; 低难度 BOSS1 / 高难度 BOSS2 显示图标
        # 4551x(黑刀刺客, 原版45510 + MOD新增45511~45514)视为野 Boss;
        # 45520/45530/45550 是 Boss Raid, 45000/45010 是车队, 不在此处理
        if startswith(45, 46) and not startswith(460) and (startswith(4551) or ctype // 1000 != 45) and ctype != 46780:
            yy = y + s(15)
            if c.is_rooftop:
                x, yy = x + s(13), y - s(10)
            elif c.is_basement:
                x, yy = x - s(13), y + s(10)
            else:
                base_id = ctype // 10
                if base_id in BOSS1_BASES:
                    b_ic = icon("boss1", s(32))
                elif base_id in BOSS2_BASES:
                    b_ic = icon("boss2", s(32))
                else:
                    b_ic = None
                if b_ic is not None and disp.get("show_icons"):
                    paste_center(img, b_ic, (x, yy - s(20)))
            cat = boss_category(c)   # 监狱锚=监牢; 野锚按强弱
            text = boss_text()
            if text:
                prefix = ("地下室:" if c.is_basement else "楼顶:" if c.is_rooftop else "")
                dt(x, yy, ("↓" if c.is_underground else "") + prefix + text, 16, C_WHITE,
                   anchor="ma", cat=cat)

        # 主城类型: 优先 castles.csv(变体级城名), 回退 labels 名称
        if startswith(494) and ctype != 49400:
            base_label = (castles or {}).get(ctype) or str(name)
            label = tagged(base_label) or base_label
            dt(x - s(15), y - s(30), label, 16, C_CASTLE, cat='castle')
        # 大空洞主城
        if startswith(5358, 5359, 5367, 5368):
            if (t := tagged(str(name))):
                color = C_TGH_IN if (startswith(536) != day2_lefttop) else C_TGH_OUT
                dt(x + s(5), y + s(25), t, 16, color, cat='castle')
        # 法师塔/高塔
        if startswith(400, 5110):
            if (t := tagged(str(name))):
                dt(x, y, t, 14, C_TOWER, cat='poi')
        # 马车 (带文字标签, 避免与邻近建筑标注混淆)
        if startswith(4500, 4501, 51150):
            ic = icon("carriage", s(34))
            if ic is not None:
                paste_center(img, ic, (x, y))
            if (t := tagged(str(name))):
                dt(x, y + s(24), t, 14, (240, 220, 180, 255), cat='poi')
        # 普通 POI 建筑
        if startswith(30, 32, 34, 38, 500, 501, 524, 525):
            if (t := tagged(str(name))):
                color = C_POI if not c.is_underground else (250, 200, 250, 255)
                dt(x, y + s(15), ("↓" if c.is_underground else "") + t, 14, color, cat='poi')
        # 血瓶 (510xx/41xxx 点位)
        if startswith(510, 41) and disp.get("name"):
            dt(x, y + s(15), '血瓶', 14, (255, 255, 0, 255), cat='blood')

        # 大空洞神授塔 Boss: 按楼层画在两座塔的固定位置
        if c.pos_index in TGH_TOWER_POS:
            side, floor = TGH_TOWER_POS[c.pos_index]
            tx, ty = (100, 280) if side == "LT" else (680, 530)
            ty = ty + (3 - floor) * 20
            color = C_TGH_IN if (day2_lefttop != (side == "RB")) else C_TGH_OUT
            if (t := tagged("%dF:%s" % (floor, name))):
                dt(s(tx), s(ty), t, 16, color, cat='boss')
            continue

        # 特殊事件: 2xxxx 槽位名(South Mistwood 等)是事件地点名, 不是事件名;
        # 种子无事件(event_flag=0)时不画, 有事件时按 event_flag/event_value 查名
        if 200 <= ctype // 100 <= 215:
            if pattern.event_flag:
                en = str(names.get(pattern.event_flag, pattern.event_flag))
                if pattern.event_flag in (7705, 7725) and pattern.event_value:
                    en += " " + str(names.get(pattern.event_value, pattern.event_value))
                ic = icon("event", s(45))
                if ic is not None:
                    paste_center(img, ic, (x, y))
                dt(x, y + s(28), en, 14, C_EVENT, cat='poi')


        # (位置字母已并入标签/侧栏, 不再单独绘制青色记号)

    # 字母记号防重叠: 避开其他记号 + 避开已画的文字标签(如 Boss 名/隐藏墙)
    if letter_marks:
        placed = []  # 已落点记号的 (x, y)
        for lx, ly, letter in letter_marks:
            w2 = s(11) * max(1, len(letter)) // 2  # 记号半宽/高
            h2 = s(13) // 2
            for dx, dy in ((0, 0), (0, -18), (0, 18), (14, 0), (-14, 0),
                           (14, -18), (-14, 18), (0, -36), (14, 18), (-14, -18),
                           (0, -54), (28, 0), (-28, 0), (28, -18), (-28, 18)):
                nx, ny = lx + s(dx), ly + s(dy)
                ok = all(abs(nx - qx) >= w2 * 2 or abs(ny - qy) >= h2 * 2 for qx, qy in placed)
                if ok:  # 再避开文字标签矩形
                    for x0, y0, x1, y1 in label_rects:
                        if nx + w2 > x0 and nx - w2 < x1 and ny + h2 > y0 and ny - h2 < y1:
                            ok = False
                            break
                if ok:
                    break
            placed.append((nx, ny))
            draw_text(d, (nx, ny), letter, 13, (0, 255, 255, 255), anchor="ma")

    # 左上信息面板 (纯文字, 不加背景框)
    panel = []
    for h in header:
        panel.append((None, h, C_SUB))
    if pattern.treasure:
        panel.append(("rot_rew" if False else None, "宝藏 %s" % names.get(pattern.treasure, pattern.treasure), C_SUB))
    if pattern.event_flag:
        panel.append(("event", "事件 %s %s" % (names.get(pattern.event_flag, pattern.event_flag),
                                               names.get(pattern.event_value, pattern.event_value)), C_SUB))
    px, py = 16, 16
    draw_text(d, (px, py), "种子 %d" % pattern.id, 20, C_SEED)
    yy = py + 32
    for ic_name, text, color in panel:
        if ic_name:
            ic = icon(ic_name, 18)
            if ic is not None:
                paste_center(img, ic, (px + 9, yy + 9))
            draw_text(d, (px + 24, yy), text, 15, color)
        else:
            draw_text(d, (px, yy), text, 15, color)
        yy += 30
    if note:
        draw_text(d, (px, yy), note, 15, (242, 140, 140, 255))

    # 提供底图时, 把标注层合成到地图截图上 (呈现半透明素材的真实效果)
    if base is not None:
        if not isinstance(base, Image.Image):
            base = Image.fromarray(base)
        base = base.convert("RGBA").resize((SIZE, SIZE), Image.Resampling.BICUBIC)
        img = Image.alpha_composite(base, img)
    return img
