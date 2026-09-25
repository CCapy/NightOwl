#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实时地图识别悬浮工具。

用法: python overlay.py
  按 F9   打开/关闭快捷键设置界面
  识别快捷键(默认 F8, 仅键盘, 可在设置界面修改) 截取地图区域并识别
  显隐快捷键(默认 M, 键盘/手柄均可, 可在设置界面修改) 切换悬浮文字显隐
  按 F10  退出程序

悬浮窗为逐像素半透明窗口(PyQt6 无边框置顶透明窗), 只显示标注信息,
地图本体(含玩家位置等动态元素)仍由游戏渲染, 不会被遮挡冻结。
"""
import json
import os
import queue
import sys
import threading
import time

import numpy as np
from PIL import ImageGrab
import keyboard

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton, QCheckBox, QComboBox,
                             QFormLayout, QHBoxLayout, QMessageBox, QColorDialog, QSpinBox, QSizePolicy)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import main as M      # 识别流程
import render as R    # 标注层渲染

SETTINGS_KEY = "f9"   # 固定: 打开/关闭设置界面
QUIT_KEY = "f10"
# 运行时参数: 每次识别前从 config.json 重新读取, 修改无需重启
CONFIG_PATH = os.path.join(HERE, "config.json")
CONFIG_DEFAULTS = {
    # 模式只影响识别区域(screen_crop): normal 正常 / video 视频 / debug 调试
    "mode": "normal",
    "screen_crop": {
        "normal": [1450, 230, 1000, 1000],
        "video": [1450, 230, 1000, 1000],
        "debug": [1450, 230, 1000, 1000],
    },
    "show_anchor_labels": True,
    "save_debug": False,              # 识别调试图(aligned/poi_result)是否落盘
    "hotkeys": {
        "detect": "f8",               # 识别快捷键 (仅键盘)
        "toggle": "m",                # 显隐快捷键 (键盘)
        "gamepad_button": 6,          # 显隐手柄按键 (XInput 按钮号, null 关闭)
        "hide_button": 1,             # 隐藏地图手柄按键 (XInput, 默认 B; null 关闭)
    },
    "style": {                        # 文字样式 (按类型): 颜色/描边 RGB, 字号, 偏移
        "hl_weak": [255, 110, 40],    # 数据面板弱点高亮色
        "hl_resist": [90, 140, 255],  # 数据面板抗性高亮色
        "poi":         {"color": [200, 220, 150], "outline": [0, 0, 0], "size": 14, "offset": [0, 0]},
        "blood":       {"color": [255, 255, 0],   "outline": [0, 0, 0], "size": 14, "offset": [0, 0]},
        "boss":        {"color": [255, 255, 255], "outline": [0, 0, 0], "size": 16, "offset": [0, 0]},
        "boss_strong": {"color": [255, 80, 80],   "outline": [0, 0, 0], "size": 16, "offset": [0, 0]},
        "jail":        {"color": [110, 170, 255], "outline": [0, 0, 0], "size": 16, "offset": [0, 0]},
        "castle":      {"color": [255, 255, 0],   "outline": [0, 0, 0], "size": 16, "offset": [0, 0]},
        "label_offset": [0, 0],
    },
    "display": {                      # 显示配置 (设置界面勾选)
        "sidebar": False,             # 侧边显示: 勾选后名称等信息列在左侧栏, 地图只标位置字母
        "show_icons": False,          # 强敌图标 (boss1/boss2.png)
        "stats": False,               # 屏幕左侧强敌数据面板 (距屏幕左 20px)
        "stats_y": 313,               # 数据面板的屏幕垂直起点
        "stats_font": 14,             # 数据面板字号
        "pos": False,                 # 位置标识
        "long_id": False,             # 长ID
        "short_id": False,            # 短ID
        "name": True,                 # 名称 (默认仅勾选此项)
    },
}
# XInput 手柄按钮号 -> 名称 (pygame joystick 顺序)
GAMEPAD_BUTTONS = [
    (0, "A"), (1, "B"), (2, "X"), (3, "Y"),
    (4, "LB"), (5, "RB"), (6, "View/地图键(推荐)"), (7, "Start/菜单"),
    (8, "左摇杆按下"), (9, "右摇杆按下"),
    (10, "十字键上"), (11, "十字键下"), (12, "十字键左"), (13, "十字键右"),
]
MAP_X, MAP_Y = M.SCREEN_CROP[0]
MAP_W, MAP_H = M.SCREEN_CROP[1]


def reload_config():
    """读取 config.json 更新运行参数; 返回完整配置(含展开后的 crop)。"""
    global MAP_X, MAP_Y, MAP_W, MAP_H
    cfg = json.loads(json.dumps(CONFIG_DEFAULTS))  # 深拷贝, 防止 hotkeys 子字典被覆盖污染
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            file_cfg = json.load(f)
        hotkeys_in_file = file_cfg.get("hotkeys")
        display_in_file = file_cfg.get("display")
        cfg.update(file_cfg)
        if hotkeys_in_file:  # hotkeys 子字典做合并, 允许只写部分键
            cfg["hotkeys"] = {**CONFIG_DEFAULTS["hotkeys"], **hotkeys_in_file}
        if display_in_file:  # display 同样合并
            cfg["display"] = {**CONFIG_DEFAULTS["display"], **display_in_file}
        for _k in R.STYLE_KEYS:  # style 各类别子字典合并
            cfg["style"][_k] = {**CONFIG_DEFAULTS["style"].get(_k, {}),
                                **(file_cfg.get("style") or {}).get(_k, {})}
    except Exception as e:
        print("config.json 读取失败(%s), 使用当前值" % e)
    crops = cfg["screen_crop"]
    # 兼容直接写一组的旧格式
    if isinstance(crops, list):
        crops = {"normal": crops, "test": crops}
    # 模式 (normal/video/debug) 直接读 screen_crop 下同名键, 没有则回退 normal
    mode = cfg.get("mode") or "normal"
    cfg["mode"] = mode
    crop = crops.get(mode) or crops["normal"]
    MAP_X, MAP_Y = int(crop[0]), int(crop[1])
    MAP_W, MAP_H = int(crop[2]), int(crop[3])
    cfg["crop"] = crop
    return cfg


def grab_map_image() -> np.ndarray:
    """截取屏幕上的地图区域, 返回 ndarray (供识别使用)。"""
    shot = ImageGrab.grab(bbox=(MAP_X, MAP_Y, MAP_X + MAP_W, MAP_Y + MAP_H), all_screens=True)
    return np.array(shot.convert("RGB"))


def pil_to_qpixmap(img) -> QPixmap:
    """PIL RGBA 图 -> QPixmap。"""
    data = img.convert("RGBA").tobytes("raw", "RGBA")
    qimg = QImage(data, img.size[0], img.size[1], img.size[0] * 4, QImage.Format.Format_RGBA8888)
    pm = QPixmap.fromImage(qimg)
    # data 必须在 QPixmap 使用期间存活, 拷贝一份脱离原 buffer
    out = pm.copy()
    del qimg
    return out


class OverlayWindow(QWidget):
    """无边框 + 置顶 + 逐像素半透明的悬浮窗, 覆盖在游戏地图区域上。"""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setGeometry(MAP_X, MAP_Y, MAP_W, MAP_H)
        self.label = QLabel(self)
        self.label.setGeometry(0, 0, MAP_W, MAP_H)
        self.label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

    def set_image(self, pil_img, screen_x=None, screen_y=None):
        # 跟随 config.json 的最新裁剪区域调整窗口位置尺寸
        if screen_x is not None:  # 带左侧数据面板: 窗口从屏幕 x=20 铺到地图右缘
            dw = MAP_X + MAP_W - screen_x
            dh = (MAP_Y + MAP_H - screen_y) if screen_y is not None else MAP_H
            self.setGeometry(screen_x, screen_y if screen_y is not None else MAP_Y, dw, dh)
            self.label.setGeometry(0, 0, dw, dh)
            if pil_img.size != (dw, dh):
                pil_img = pil_img.resize((dw, dh), __import__("PIL.Image", fromlist=["Image"]).Resampling.BICUBIC)
            self.label.setPixmap(pil_to_qpixmap(pil_img))
            return
        ox, dw = MAP_X, MAP_W
        if pil_img.width > pil_img.height:  # 侧边模式: 左侧带 300px 信息栏的合成图
            dw = MAP_W + int(R.BOSS_PANEL_W * MAP_W / 1000)
            ox = MAP_X - (dw - MAP_W)
        self.setGeometry(ox, MAP_Y, dw, MAP_H)
        self.label.setGeometry(0, 0, dw, MAP_H)
        # 渲染层为 1000x1000(+信息栏), 按裁剪区域(可能非正方形)缩放显示
        if pil_img.size != (dw, MAP_H):
            pil_img = pil_img.resize((dw, MAP_H), __import__("PIL.Image", fromlist=["Image"]).Resampling.BICUBIC)
        self.label.setPixmap(pil_to_qpixmap(pil_img))

    def clear(self):
        self.label.clear()


# ---------------------------------------------------------------------------
# 快捷键注册 / 手柄监听 / 设置界面
# ---------------------------------------------------------------------------

_hotkey_tokens = []


def register_hotkeys(events_q, ui_q, cfg):
    """按当前配置注册全部全局热键; 重复调用会先注销旧热键(供设置界面热更新)。"""
    global _hotkey_tokens
    for t in _hotkey_tokens:
        try:
            keyboard.remove_hotkey(t)
        except Exception:
            pass
    _hotkey_tokens = []
    hk = cfg.get("hotkeys") or {}

    def add(key, fn, desc):
        try:
            _hotkey_tokens.append(keyboard.add_hotkey(key, fn))
        except Exception as e:
            print("热键注册失败 [%s: %s]: %s" % (desc, key, e))

    add(SETTINGS_KEY, lambda: ui_q.put(("settings", None)), "设置界面")
    add(hk.get("detect") or "f8", lambda: events_q.put(1), "识别")
    add(hk.get("toggle") or "m", lambda: ui_q.put(("toggle", None)), "显隐")
    add(QUIT_KEY, lambda: ui_q.put(("quit", None)), "退出")


GAMEPAD_CFG = {"button": None, "hide": None}   # 手柄按钮: 显隐 / 隐藏地图; None = 未启用

# 按钮号(沿用界面顺序) -> XInput wButtons 位掩码 (微软标准: A=0x1000 B=0x2000 X=0x4000 Y=0x8000)
_XINPUT_BITS = [0x1000, 0x2000, 0x4000, 0x8000,   # A B X Y
               0x0100, 0x0200, 0x0020, 0x0010,    # LB RB View/Back Start
               0x0040, 0x0080,                    # 左/右摇杆按下
               0x0001, 0x0002, 0x0004, 0x0008]    # 十字键 上/下/左/右
_XINPUT_NAME = dict(zip(_XINPUT_BITS, [n for _, n in GAMEPAD_BUTTONS]))

_xinput_state = None   # 惰性初始化的 (dll, XINPUT_STATE)
_xinput_lock = threading.Lock()   # 监听线程与设置界面录制轮询共用, 必须加锁防读脏


def _xinput_buttons():
    """读 0 号手柄当前按钮位掩码; 手柄未连接返回 None, XInput 不可用返回 False。"""
    global _xinput_state
    import ctypes
    with _xinput_lock:
        if _xinput_state is None:
            class _GAMEPAD(ctypes.Structure):
                _fields_ = [("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte),
                            ("bRightTrigger", ctypes.c_ubyte), ("sThumbLX", ctypes.c_short),
                            ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short),
                            ("sThumbRY", ctypes.c_short)]

            class _STATE(ctypes.Structure):
                _fields_ = [("dwPacketNumber", ctypes.c_ulong), ("Gamepad", _GAMEPAD)]

            dll = None
            for name in ("xinput1_4", "xinput9_1_0"):
                try:
                    dll = ctypes.windll.LoadLibrary(name)
                    break
                except OSError:
                    continue
            _xinput_state = (dll, _STATE() if dll else None)
        dll, state = _xinput_state
        if dll is None:
            return False
        if dll.XInputGetState(0, ctypes.byref(state)) != 0:  # 非0 = 未连接
            return None
        return state.Gamepad.wButtons


def gamepad_loop(ui_q):
    """手柄监听线程: XInput 轮询 0 号手柄按钮, 按下沿触发显隐/隐藏。
    20ms 轮询(快速点按不漏检); 瞬时读取失败只等 150ms(不掉线级盲窗)。"""
    if _xinput_buttons() is False:
        print("未找到 XInput 运行库, 手柄支持不可用")
        return
    prev = {"button": False, "hide": False}
    announced = False
    fail = 0
    while True:
        try:
            mask = _xinput_buttons()
            if mask is None:
                fail += 1
                if fail >= 7:   # 连续 ~1s 读不到才视为断开, 期间不丢按键状态
                    prev = {"button": False, "hide": False}
                    announced = False
                time.sleep(0.15)
                continue
            fail = 0
            if not announced:
                print("手柄已连接 (XInput 0 号)")
                announced = True
            for key, action in (("button", "toggle"), ("hide", "hide_only")):
                btn = GAMEPAD_CFG.get(key)
                cur = bool(btn is not None and 0 <= btn < len(_XINPUT_BITS)
                           and mask & _XINPUT_BITS[btn])
                if cur and not prev[key]:
                    ui_q.put((action, None))
                prev[key] = cur
            time.sleep(0.02)
        except Exception:
            time.sleep(0.15)


class SettingsDialog(QWidget):
    """设置界面 (F9 打开/关闭): 快捷键录制 + 显示选项, 保存进 config.json。"""
    MODIFIER_NAMES = {"shift", "left shift", "right shift", "ctrl", "left ctrl", "right ctrl",
                      "alt", "left alt", "right alt", "left windows", "right windows"}

    def __init__(self, cfg, on_apply):
        super().__init__()
        self.setWindowTitle("设置")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self._on_apply = on_apply
        self._captured = None      # (目标字段, 键名) 待 QTimer 消费
        self._hook = None
        hk = cfg.get("hotkeys") or {}
        self._detect = hk.get("detect") or "f8"
        self._toggle = hk.get("toggle") or "m"

        form = QFormLayout(self)
        # 模式切换 (只影响识别区域)
        self.mode_combo = QComboBox()
        for key, label in (("normal", "正常模式"), ("video", "视频模式"), ("debug", "调试模式")):
            self.mode_combo.addItem(label, key)
        cur = cfg.get("mode") or "normal"
        pos = self.mode_combo.findData(cur)
        self.mode_combo.setCurrentIndex(pos if pos >= 0 else 0)
        form.addRow("模式", self.mode_combo)
        self.detect_btn = self._key_button(self._detect)
        self.toggle_btn = self._key_button(self._toggle)
        self.detect_btn.clicked.connect(lambda: self._start_capture("detect", self.detect_btn))
        self.toggle_btn.clicked.connect(lambda: self._start_capture("toggle", self.toggle_btn))
        form.addRow("识别快捷键", self.detect_btn)
        form.addRow("显隐快捷键", self.toggle_btn)

        # 手柄: 与键盘一致的点击录制方式
        self._pad = hk.get("gamepad_button")
        self._pad_capturing = False
        self._pad_wait_release = True
        pad_row = QHBoxLayout()
        self.pad_btn = QPushButton()
        self.pad_btn.setMinimumWidth(220)
        self._refresh_pad_btn()
        self.pad_btn.clicked.connect(self._start_pad_capture)
        pad_off = QPushButton("停用")
        pad_off.clicked.connect(self._disable_pad)
        pad_row.addWidget(self.pad_btn)
        pad_row.addWidget(pad_off)
        form.addRow("显隐手柄按键", pad_row)

        # 隐藏地图手柄按键 (默认 B): 按下只隐藏, 显示仍走显隐键
        self._hide_btn_id = hk.get("hide_button")
        self._hide_capturing = False
        self._hide_wait_release = True
        hide_row = QHBoxLayout()
        self.hide_btn = QPushButton()
        self.hide_btn.setMinimumWidth(220)
        self._refresh_hide_btn()
        self.hide_btn.clicked.connect(self._start_hide_capture)
        hide_off = QPushButton("停用")
        hide_off.clicked.connect(self._disable_hide)
        hide_row.addWidget(self.hide_btn)
        hide_row.addWidget(hide_off)
        form.addRow("隐藏地图手柄按键", hide_row)

        # 显示选项
        self.sidebar_cb = QCheckBox("侧边显示名字")
        self.icons_cb = QCheckBox("显示强敌图标")
        self.stats_cb = QCheckBox("左侧强敌数据")
        disp = cfg.get("display") or {}
        self.sidebar_cb.setChecked(bool(disp.get("sidebar")))
        self.icons_cb.setChecked(bool(disp.get("show_icons")))
        self.stats_cb.setChecked(bool(disp.get("stats")))
        opts_head = QHBoxLayout()
        opts_head.addWidget(self.sidebar_cb)
        opts_head.addWidget(self.icons_cb)
        opts_head.addWidget(self.stats_cb)
        opts_head.addStretch(1)
        form.addRow(opts_head)
        opts = QHBoxLayout()
        self.opt_cbs = {}
        for key, label in (("pos", "位置标识"), ("long_id", "长ID"), ("short_id", "短ID"), ("name", "名称")):
            cb = QCheckBox(label)
            cb.setChecked(bool(disp.get(key, key == "name")))
            self.opt_cbs[key] = cb
            opts.addWidget(cb)
        form.addRow("显示内容", opts)
        dp_row = QHBoxLayout()
        self.stats_font_sb = QSpinBox(); self.stats_font_sb.setRange(10, 28)
        self.stats_font_sb.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.stats_font_sb.setValue(int(disp.get("stats_font", 14)))
        self.stats_font_sb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.weak_btn = self._color_button((cfg.get("style") or {}).get("hl_weak", [255, 110, 40]))
        self.resist_btn = self._color_button((cfg.get("style") or {}).get("hl_resist", [90, 140, 255]))
        for w in (self.stats_font_sb, QLabel("字号"), self.weak_btn, QLabel("弱点"),
                  self.resist_btn, QLabel("抗性")):
            w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            dp_row.addWidget(w, 1)
        form.addRow("数据面板", dp_row)


        # 文字样式 (按类型): 字色/描边/字号/横移/竖移, 顶部有列标题
        self._sty_edits = {}   # key -> dict(color_btn, outline_btn, size_sb, dx_sb, dy_sb)
        hdr = QHBoxLayout()
        for t in ("字色", "描边", "字号", "横移", "竖移"):
            lb = QLabel(t)
            lb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            hdr.addWidget(lb, 1)
        form.addRow(QLabel("样式"), hdr)
        for key in R.STYLE_KEYS:
            sty = (cfg.get("style") or {}).get(key, {})
            row = QHBoxLayout()
            color_btn = self._color_button(sty.get("color", [255, 255, 255]))
            outline_btn = self._color_button(sty.get("outline", [0, 0, 0]))
            size_sb = QSpinBox(); size_sb.setRange(8, 32)
            size_sb.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            size_sb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            size_sb.setValue(int(sty.get("size", 14)))
            dx = QSpinBox(); dx.setRange(-200, 200)
            dx.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            dx.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            dx.setValue(int(sty.get("offset", [0, 0])[0]))
            dy = QSpinBox(); dy.setRange(-200, 200)
            dy.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            dy.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            dy.setValue(int(sty.get("offset", [0, 0])[1]))
            for w in (color_btn, outline_btn, size_sb, dx, dy):
                row.addWidget(w, 0)
            form.addRow(R.STYLE_LABELS[key], row)
            self._sty_edits[key] = dict(color_btn=color_btn, outline_btn=outline_btn,
                                        size_sb=size_sb, dx=dx, dy=dy)
        goff = QHBoxLayout()
        self.gox = QSpinBox(); self.gox.setRange(-200, 200)
        self.gox.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.gox.setValue(int((cfg.get("style") or {}).get("label_offset", [0, 0])[0]))
        self.goy = QSpinBox(); self.goy.setRange(-200, 200)
        self.goy.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.goy.setValue(int((cfg.get("style") or {}).get("label_offset", [0, 0])[1]))
        glx = QLabel("横移"); gly = QLabel("竖移")
        for w in (self.gox, glx, self.goy, gly):
            goff.addWidget(w, 1)
        form.addRow("全局偏移", goff)

        btns = QHBoxLayout()
        ok = QPushButton("应用并保存")
        ok.clicked.connect(self._apply)
        close = QPushButton("关闭")
        close.clicked.connect(self.hide)
        btns.addWidget(ok)
        btns.addWidget(close)
        form.addRow(btns)

        self._pad_timer_start = 0.0
        QTimer(self, interval=50, timeout=self._poll).start()

    def _pad_name(self):
        if self._pad is None:
            return "未启用"
        for idx, name in GAMEPAD_BUTTONS:
            if idx == self._pad:
                return name
        return str(self._pad)

    def _refresh_pad_btn(self, capturing=False):
        if capturing:
            self.pad_btn.setText("请按下手柄按键... (8秒超时)")
        else:
            self.pad_btn.setText("【%s】" % self._pad_name())

    def _start_pad_capture(self):
        if self._pad_capturing:
            return
        mask = _xinput_buttons()
        if mask is False:
            QMessageBox.warning(self, "手柄不可用", "未找到 XInput 运行库, 无法使用手柄。")
            return
        if mask is None:
            QMessageBox.warning(self, "手柄未连接", "请先连接手柄再录制。")
            return
        self._pad_capturing = True
        self._pad_wait_release = (mask != 0)   # 先等已按下的键松开, 再录新按下
        self._pad_timer_start = time.time()
        self._refresh_pad_btn(capturing=True)

    def _disable_pad(self):
        self._pad = None
        self._pad_capturing = False
        self._refresh_pad_btn()

    def _refresh_hide_btn(self, capturing=False):
        if capturing:
            self.hide_btn.setText("按下手柄按键... (8秒超时)")
        else:
            name = "未启用" if self._hide_btn_id is None else                    dict(GAMEPAD_BUTTONS).get(self._hide_btn_id, str(self._hide_btn_id))
            self.hide_btn.setText("【%s】" % name)

    def _start_hide_capture(self):
        if self._hide_capturing:
            return
        mask = _xinput_buttons()
        if mask is False or mask is None:
            QMessageBox.warning(self, "手柄不可用", "XInput 不可用或手柄未连接。")
            return
        self._hide_capturing = True
        self._hide_wait_release = (mask != 0)
        self._hide_timer_start = time.time()
        self._refresh_hide_btn(capturing=True)

    def _disable_hide(self):
        self._hide_btn_id = None
        self._hide_capturing = False
        self._refresh_hide_btn()

    def _poll(self):
        """50ms 轮询: 消费键盘录制结果 + 手柄按钮录制(显隐/隐藏地图)"""
        self._consume_capture()
        if self._pad_capturing:   # 显隐键录制
            if time.time() - self._pad_timer_start > 8:
                self._pad_capturing = False
                self._refresh_pad_btn()
            else:
                mask = _xinput_buttons()
                if mask == 0:
                    self._pad_wait_release = False
                elif mask and not self._pad_wait_release:
                    for idx, bit in enumerate(_XINPUT_BITS):
                        if mask & bit:
                            self._pad = idx
                            break
                    self._pad_capturing = False
                    self._refresh_pad_btn()
        if self._hide_capturing:  # 隐藏地图键录制
            if time.time() - self._hide_timer_start > 8:
                self._hide_capturing = False
                self._refresh_hide_btn()
            else:
                mask = _xinput_buttons()
                if mask == 0:
                    self._hide_wait_release = False
                elif mask and not self._hide_wait_release:
                    for idx, bit in enumerate(_XINPUT_BITS):
                        if mask & bit:
                            self._hide_btn_id = idx
                            break
                    self._hide_capturing = False
                    self._refresh_hide_btn()

    def _key_button(self, text):
        b = QPushButton()
        b.setMinimumWidth(220)
        b.setText("【%s】" % text)
        return b

    def _color_button(self, rgb):
        b = QPushButton()
        b.setFixedWidth(46)
        b.clicked.connect(lambda: self._pick_color(b))
        self._apply_btn_color(b, rgb)
        return b

    def _apply_btn_color(self, btn, rgb):
        btn._rgb = list(rgb)
        btn.setStyleSheet("background-color: rgb(%d,%d,%d);" % tuple(rgb))

    def _pick_color(self, btn):
        c = QColorDialog.getColor(QColor(*btn._rgb), self, "选择颜色")
        if c.isValid():
            self._apply_btn_color(btn, [c.red(), c.green(), c.blue()])

    def _btn_rgb(self, btn):
        return btn._rgb

    def _refresh_key_buttons(self):
        self.detect_btn.setText("【%s】" % self._detect)
        self.toggle_btn.setText("【%s】" % self._toggle)

    def _start_capture(self, target, btn):
        if self._hook:
            return
        btn.setText("按下新按键...")

        def on_key(event):
            if event.event_type != keyboard.KEY_DOWN:
                return
            if event.name == "esc":
                self._captured = (target, None)
            elif event.name not in self.MODIFIER_NAMES:
                self._captured = (target, event.name)

        self._hook = keyboard.hook(on_key, suppress=False)

    def _consume_capture(self):
        if not self._captured or not self._hook:
            return
        target, key = self._captured
        self._captured = None
        keyboard.unhook(self._hook)
        self._hook = None
        btn = self.detect_btn if target == "detect" else self.toggle_btn
        if key is None:
            self._refresh_key_buttons()
            return
        if key in (SETTINGS_KEY, QUIT_KEY):
            QMessageBox.warning(self, "冲突", "键 %s 已被固定功能占用 (%s/退出), 请换一个键。"
                                % (key, SETTINGS_KEY.upper()))
            self._refresh_key_buttons()
            return
        if target == "detect":
            self._detect = key
        else:
            self._toggle = key
        self._refresh_key_buttons()

    def _apply(self):
        cfg = {"mode": self.mode_combo.currentData(), "hotkeys": {
            "detect": self._detect,
            "toggle": self._toggle,
            "gamepad_button": self._pad,
            "hide_button": self._hide_btn_id,
        }, "display": {
            "sidebar": self.sidebar_cb.isChecked(),
            "show_icons": self.icons_cb.isChecked(),
            "stats": self.stats_cb.isChecked(),
            "stats_font": self.stats_font_sb.value(),
            **{k: cb.isChecked() for k, cb in self.opt_cbs.items()},
        }, "style": {
            "hl_weak": self._btn_rgb(self.weak_btn),
            "hl_resist": self._btn_rgb(self.resist_btn),
            **{key: {
                "color": self._btn_rgb(e["color_btn"]),
                "outline": self._btn_rgb(e["outline_btn"]),
                "size": e["size_sb"].value(),
                "offset": [e["dx"].value(), e["dy"].value()],
            } for key, e in self._sty_edits.items()},
            "label_offset": [self.gox.value(), self.goy.value()],
        }}
        self._on_apply(cfg)


def worker(info, assets, events: queue.Queue, ui: queue.Queue, hidden_evt: threading.Event):
    """识别线程: 空闲时等待热键; 识别期间忽略新热键, 结束后清空积压。"""
    busy = False
    while True:
        events.get()
        events.task_done()
        if busy:
            continue
        busy = True
        cfg = reload_config()   # 每次识别前重读 config.json (修改无需重启)
        M.SAVE_DEBUG = bool(cfg.get("save_debug", False))
        # 先隐藏悬浮层并等 UI 线程确认, 再截屏 —— 否则会把自己的标注识别进去
        ui.put(("hide", None))
        hidden_evt.wait()
        hidden_evt.clear()
        ui.put(("detecting", None))
        try:
            t0 = time.time()
            best_id, results = M.detect_image(info, assets, grab_map_image(), tag="_live")
            if results:
                # 控制台输出 top 结果(同 main.py), 便于核对
                print("\n[F9] 识别结果 top%d:" % M.TOPK)
                M.print_results(info, results)
                ui.put(("result", (results, "%.1fs" % (time.time() - t0), cfg, info)))
            else:
                ui.put(("fail", "未识别出地图(请确认当前是完整地图画面)"))
        except Exception as e:
            ui.put(("fail", "识别出错: %s" % e))
        finally:
            try:
                while True:
                    events.get_nowait()
                    events.task_done()
            except queue.Empty:
                pass
            busy = False


if __name__ == "__main__":
    # 单实例保护: 退出卡死残留的旧进程会继续抢占热键、显示旧效果
    import socket
    _lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _lock.bind(("127.0.0.1", 52991))
    except OSError:
        print("已有一个 overlay 实例在运行, 请先退出它(必要时检查任务管理器)。")
        sys.exit(1)

    print("加载数据...")
    INFO = M.load_data()
    ASSETS = M.Assets()
    print("数据加载完成。F9 设置, F8(可改) 识别, M(可改/手柄) 显隐, F10 退出。")

    events = queue.Queue()
    ui = queue.Queue()

    app = QApplication(sys.argv)
    overlay = OverlayWindow()

    last_result = {"p": None, "note": None}

    def render_result(p, note, cfg, info):
        """按配置渲染种子标注并显示 (识别结果 / 设置保存后的即时重渲染共用)"""
        try:
            letters = info.get("anchor_letters") if cfg.get("show_anchor_labels", True) else None
            img = R.render(p, info["names"], note=note, anchor_letters=letters,
                           name_ids=info.get("name_ids"), display=cfg.get("display"),
                           style=cfg.get("style"),
                           castles=info.get("castles"), header=[
                "夜王 %s | 地形 %s" % (info["bosses"].get(p.nightlord, ("?", "?"))[1],
                                       info["terrains"].get(p.earth_shifting, ("?", "?"))[1]),
            ])
            if (cfg.get("display") or {}).get("sidebar"):  # 侧边模式: 左侧拼 Boss信息栏
                panel = R.boss_list_image(p, info["names"], info.get("name_ids"),
                                          info.get("anchor_letters"),
                                          display=cfg.get("display"))
                canvas = __import__("PIL.Image", fromlist=["Image"]).new(
                    "RGBA", (panel.width + img.width, img.height), (0, 0, 0, 0))
                canvas.paste(panel, (0, 0))
                canvas.paste(img, (panel.width, 0))
                img = canvas
            if (cfg.get("display") or {}).get("stats"):  # 屏幕左侧强敌数据面板
                disp = cfg.get("display") or {}
                scale = MAP_W / 1000.0
                stats_y = int(disp.get("stats_y", 313))
                win_y = min(stats_y, MAP_Y)                  # 窗口纵向上界取面板/地图较小者
                win_h = MAP_Y + MAP_H - win_y
                panel_w = int((MAP_X - 20) / scale) if scale > 0 else 600
                canvas_h = int(win_h / scale)
                spanel = R.stats_panel_image(p, info["names"], info.get("boss_stats"),
                                             info.get("castles"), bosses=info.get("bosses"),
                                             width=panel_w, height=canvas_h, style=cfg.get("style"))
                canvas = __import__("PIL.Image", fromlist=["Image"]).new(
                    "RGBA", (spanel.width + img.width, canvas_h), (0, 0, 0, 0))
                canvas.paste(spanel, (0, int((stats_y - win_y) / scale)))
                canvas.paste(img, (spanel.width, int((MAP_Y - win_y) / scale)))
                img = canvas
                overlay.set_image(img, screen_x=20, screen_y=win_y)
            else:
                overlay.set_image(img)
            if cfg.get("mode") == "debug":  # 调试模式: result/{seed}.txt, 每行 位置_长ID_短ID
                try:
                    lts = info.get("anchor_letters") or {}
                    ni = info.get("name_ids") or {}
                    os.makedirs("result", exist_ok=True)
                    with open(os.path.join("result", "%d.txt" % p.id), "w",
                              encoding="utf-8") as fp:
                        for c in sorted(p.constructs,
                                        key=lambda c: lts.get(c.pos_index, "~")):
                            fp.write("%s_%s_%d\n" % (
                                lts.get(c.pos_index, "?"),
                                ni.get(c.type, "0"), c.type))
                except Exception as e:
                    print("写 result/%d.txt 失败: %s" % (p.id, e))
            overlay.show()
        except Exception as e:
            print("渲染信息图失败: %s" % e)
            overlay.set_image(R._simple_text_image("种子 %d (渲染失败)" % p.id))
            overlay.show()

    def apply_hotkeys(hotkey_cfg):
        """设置界面保存回调: 写回 config.json 并热更新热键/手柄, 立即重绘。"""
        try:
            file_cfg = {}
            if os.path.exists(CONFIG_PATH):
                file_cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
            if "mode" in hotkey_cfg:
                file_cfg["mode"] = hotkey_cfg["mode"]
            file_cfg.setdefault("hotkeys", {}).update(hotkey_cfg["hotkeys"])
            if "display" in hotkey_cfg:
                file_cfg.setdefault("display", {}).update(hotkey_cfg["display"])
            if "style" in hotkey_cfg:
                file_sty = file_cfg.setdefault("style", {})
                file_sty.update(hotkey_cfg["style"])
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(file_cfg, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print("保存 config.json 失败: %s" % e)
        cfg = reload_config()
        register_hotkeys(events, ui, cfg)
        GAMEPAD_CFG["button"] = (cfg.get("hotkeys") or {}).get("gamepad_button")
        GAMEPAD_CFG["hide"] = (cfg.get("hotkeys") or {}).get("hide_button")
        print("快捷键已更新: 识别=%s 显隐=%s 手柄按钮=%s"
              % (cfg["hotkeys"]["detect"], cfg["hotkeys"]["toggle"], GAMEPAD_CFG["button"]))
        if last_result["p"] is not None:   # 用新配置立即重渲染当前种子 (主线程直调)
            render_result(last_result["p"], last_result["note"], reload_config(), INFO)

    settings_dialog = SettingsDialog(reload_config(), apply_hotkeys)
    GAMEPAD_CFG["button"] = (reload_config().get("hotkeys") or {}).get("gamepad_button")
    GAMEPAD_CFG["hide"] = (reload_config().get("hotkeys") or {}).get("hide_button")
    threading.Thread(target=gamepad_loop, args=(ui,), daemon=True).start()

    hidden_evt = threading.Event()
    threading.Thread(target=worker, args=(INFO, ASSETS, events, ui, hidden_evt), daemon=True).start()
    register_hotkeys(events, ui, reload_config())

    # 简易"识别中"文字 (透明窗上直接用渲染层生成)
    detecting_img = R._simple_text_image("识别中...")

    def poll():
        try:
            while True:
                try:
                    kind, payload = ui.get_nowait()
                except queue.Empty:
                    break
                if kind == "hide":
                    overlay.close()          # 销毁当前标注, 防止被下一次截屏识别进去
                    hidden_evt.set()
                elif kind == "detecting":
                    overlay.set_image(detecting_img)
                    overlay.show()
                elif kind == "result":
                    results, note, cfg, info = payload
                    error, neg_score, p = results[0]
                    last_result["p"], last_result["note"] = p, note
                    render_result(p, note, cfg, info)
                elif kind == "fail":
                    overlay.set_image(R._simple_text_image(payload))
                    overlay.show()
                elif kind == "rerender":   # 设置保存后用新配置立即重渲染当前种子
                    if last_result["p"] is not None:
                        render_result(last_result["p"], last_result["note"],
                                      reload_config(), INFO)
                        overlay.show()
                elif kind == "toggle":
                    overlay.hide() if overlay.isVisible() else overlay.show()
                elif kind == "hide_only":   # 隐藏地图: 只隐藏, 显示仍走显隐键
                    overlay.hide()
                elif kind == "settings":
                    settings_dialog.hide() if settings_dialog.isVisible() else settings_dialog.show()
                    settings_dialog.raise_()
                    settings_dialog.activateWindow()
                elif kind == "quit":
                    app.quit()
                    return
        except Exception as e:
            print("UI 轮询异常: %s" % e)
        QTimer.singleShot(100, poll)

    QTimer.singleShot(100, poll)
    sys.exit(app.exec())
