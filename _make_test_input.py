#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合成测试图: 取一张已知种子, 把它的 POI 图标和夜王头像画到干净底图上 -> input/map.jpg"""
import os
import sys
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main as M

HERE = os.path.dirname(os.path.abspath(__file__))

# 选一张种子: 命令行参数指定 ID, 默认取 es=0 nl=0 的第一张
if len(sys.argv) > 1:
    pattern = next(p for p in M.load_data()["patterns"] if p.id == int(sys.argv[1]))
else:
    pattern = next(p for p in M.load_data()["patterns"] if p.earth_shifting == 0 and p.nightlord == 0)
print("使用种子:", pattern.id)

assets = M.Assets()
base = M.open_cv2_img(os.path.join(M.ASSET_DIR, "maps_poi_match", f"{M.Assets().poi_bg_index[pattern.earth_shifting]}.jpg"), M.STD_MAP_SIZE)
img = Image.fromarray(base).convert("RGBA")

# 画 POI
for pos, c in pattern.pos_constructions.items():
    icon = assets.poi_images.get(c.type)
    if icon is None:
        icon = assets._compose_poi_image(c.type, with_subicon=False)
    if icon is not None:
        img.alpha_composite(icon, (pos[0] - 22, pos[1] - 22))

# 画夜王头像 (左下角区域, 模仿原项目匹配区域)
nl_icon = Image.open(os.path.join(M.ASSET_DIR, "nightlord", f"{pattern.nightlord}.png")).convert("RGBA")
nl_bg = Image.open(os.path.join(M.ASSET_DIR, "nightlord", "bg.png")).convert("RGBA")
cx, cy = nl_bg.size[0] // 2, nl_bg.size[1] // 2
nl_icon = nl_icon.resize((int(nl_icon.size[0] * 0.8), int(nl_icon.size[1] * 0.8)))
nl_bg.alpha_composite(nl_icon, (cx - nl_icon.size[0] // 2, cy - nl_icon.size[1] // 2))
# 匹配区域(750坐标) x:45~120, y:637~712, 中心(82,674); 实测头像约 100px 时匹配最稳
region_w = region_h = 100
nl_bg = nl_bg.resize((region_w, region_h))
rx, ry = 82, 674
img.alpha_composite(nl_bg, (rx - region_w // 2, ry - region_h // 2))

os.makedirs(os.path.join(HERE, "input"), exist_ok=True)
img.convert("RGB").save(os.path.join(HERE, "input", "map.jpg"))
print("已生成 input/map.jpg, 该图包含种子 %d 的 %d 个建筑" % (pattern.id, len(pattern.pos_constructions)))
