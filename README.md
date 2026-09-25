# Nightreign Overlay Helper (code/)

黑夜君临（ELDEN RING NIGHTREIGN）实时地图识别悬浮工具。
配合 More Map Variations (MMV) 等 MOD 使用：截屏识别当前远征地图种子，
在地图上方悬浮标注强敌/据点/事件位置，并在屏幕左侧显示强敌抗性数据面板。

## 快速开始

```bash
pip install -r requirements.txt
python overlay.py
```

- **F9** 打开/关闭设置界面（快捷键、显示选项、文字样式、模式）
- **F8**（可改） 识别当前地图
- **M**（可改） 切换悬浮层显隐；**手柄 View** 同功能
- **手柄 B**（可改） 只隐藏悬浮层
- **F10** 退出

## 数据链路

```
Smithbox 解包 (人工操作)              解析工具                    运行时
─────────────────────              ─────────────────          ──────────────
data/          param CSV   ──┐
event/         事件反编译  ──┼──  py extract.py  ──>  out/  ──>  main.load_data()
msb/           MSB 反解    ──┘                       (6张表)      overlay.py 渲染
translations/  翻译文本   ──────>  text_merge.py 合并
fmg/           变体映射表  ──>  event_extract.py (槽位规则 vN->8(N-1)0)
```

## 需要提取什么、放在哪里

用 **Smithbox** 打开游戏（含 MOD）后导出以下内容，放到对应目录，然后运行 `py extract.py` 重新生成 `out/`：

| 内容 | 位置 | 说明 |
|---|---|---|
| param CSV | `data/` | Smithbox -> Param Editor 导出 CSV。至少需要：`LotResultSmallBaseAndSpot.csv`、`LotResultPlayAreaParam.csv`、`LotResultMapPatternFlag.csv`、`SmallBaseAndSpotAttachPoint.csv`、`SmallBaseMapVariationParam.csv`、`NpcParam.csv`、`PlayAreaCreateParam.csv` |
| 事件反编译 | `event/` | EMEVD 反编译的 JS（`m??_??_00_00.emevd.dcx.js`），用于 Boss 真身与长ID 推导 |
| MSB 反解 | `msb/` | MSB 反解的 XML（`m??_??_00_00-msb-dcx/Part/Enemy/*.xml`），用于 实体->NPCParam 抗性数据 |
| 翻译文本 | `translations/` | 游戏内+MOD 导出的中文文本（见下节） |

> `event/`、`msb/` 缺失时 extract 仍可运行，对应增强数据（名称校准/抗性）自动降级。

## 翻译目录（translations/）

所有翻译类文件集中在 `translations/`，合并优先级（低 -> 高）：

1. `origin.txt` / `origin_dlc01.txt` —— 原版中文（格式 `ID;文本`，两者直接合并）
2. `mod1.xml` / `mod1_dlc01.xml` —— MOD 中文（按序号升序覆盖/新增，`%null%` 视为缺失）
3. `translations.csv` —— 人工翻译字典（`ID,中文`）
4. `manual.csv` —— 人工干预（`ID,文本`），最终覆盖

由 `text_merge.py` 的 `load_translations()` 统一合并；MOD 新增条目在 `translations.csv` 末尾追加即可。

## 运行时数据表（out/）

`extract.py` 生成，运行必需：

| 文件 | 内容 |
|---|---|
| `map_patterns.csv` | 520 张种子地图（夜王/地形/Day1/Day2 Boss/事件/宝藏） |
| `constructs.csv` | 每张种子的建筑/强敌/据点（type = 槽位×10+变体） |
| `positions.csv` | 604 个锚点的网格与格内坐标 |
| `labels.csv` | 名称表（en=param 英文名, zh=合并翻译） |
| `boss_stats.csv` | 强敌抗性（物理/属性四维、韧性、异常五维） |

## 名称/分类表（项目根目录）

- `castles.csv` —— 主城 type -> 中文城名（MMV 24 城需人工核对）
- `bosses.csv` —— 夜王中文名（0~9）
- `terrains.csv` —— 特殊地形中文名

## 配置（config.json）

- `mode`：`normal` / `video` / `debug`，只影响识别区域（读 `screen_crop` 同名键；debug 模式额外写 `result/{seed}.txt`）
- `hotkeys`：识别/显隐快捷键 + 手柄按钮（XInput，点击录制）
- `display`：侧边显示、强敌图标、左侧数据面板、位置标识/长ID/短ID/名称勾选、面板字号与垂直位置
- `style`：各类文字颜色/描边/字号/偏移、弱点/抗性高亮色、全局偏移

设置界面（F9）修改后应用即保存并即时生效。

## 工具

- `lookup.py` —— Tkinter 小工具，输入 `种子/位置字母`（如 `240/EP`）查询该锚点信息
- `main.py` —— 离线识别单张图（`input/map.jpg`），调试图存 `output/`
- `compare.py` —— 对比两次解析输出的差异
- `_make_test_input.py` —— 按种子 ID 合成测试地图（`python _make_test_input.py [种子ID]`）
- `fmg_translate.py` —— 旧 FMG 翻译辅助

## 许可证

本项目采用 [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/deed.zh)（知识共享 署名-非商业性使用 4.0 国际）许可协议。

- ✅ **允许**：个人使用、学习研究、修改代码、再分发（须署名并保留许可声明）
- ❌ **禁止**：任何形式的商业使用——包括但不限于售卖、付费服务、内置广告等，**基于本项目修改后的衍生版本同样禁止商用**

完整条款见仓库内 [LICENSE](LICENSE) 文件。
