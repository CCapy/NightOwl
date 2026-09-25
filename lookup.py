# -*- coding: utf-8 -*-
"""种子/锚点查询小工具 (测试用)

输入 "种子/位置" (如 240/EP), 输出该锚点的 长ID/短ID/名字 及真身推导。
用法: python lookup.py   (Tkinter UI)
"""
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main  # noqa: E402  复用识别管线的 load_data


class App:
    def __init__(self, root):
        self.root = root
        root.title("种子/锚点查询")
        root.geometry("560x420")
        tk.Label(root, text="输入 种子/位置 (如 240/EP，可连输多行):").pack(anchor="w", padx=8, pady=(8, 0))
        self.entry = tk.Text(root, height=4)
        self.entry.pack(fill="x", padx=8)
        self.entry.bind("<Return>", lambda e: (self.query(), "break")[1])
        tk.Button(root, text="查询 (Enter)", command=self.query).pack(anchor="w", padx=8, pady=4)
        self.out = tk.Text(root, height=16, wrap="none")
        self.out.pack(fill="both", expand=True, padx=8, pady=8)

        self.info = main.load_data()
        self.by_seed = {p.id: p for p in self.info["patterns"]}
        self.names = self.info["names"]
        self.ni = self.info["name_ids"]
        self.letters = self.info.get("anchor_letters") or {}
        self.inv_letters = {v: k for k, v in self.letters.items()}  # 字母 -> 锚点ID
        self.out.insert("end", "数据就绪: %d 种子, %d 锚点字母\n示例: 240/EP\n" % (len(self.by_seed), len(self.inv_letters)))

    def query(self):
        self.out.delete("1.0", "end")
        for raw in self.entry.get("1.0", "end").strip().splitlines():
            raw = raw.strip().replace("：", "/").replace(",", "/").replace(" ", "/")
            if not raw:
                continue
            parts = [p for p in raw.split("/") if p]
            if len(parts) != 2 or not parts[0].isdigit():
                self.out.insert("end", "%s  -> 格式错误, 应为 种子/位置\n" % raw)
                continue
            seed, code = int(parts[0]), parts[1].upper()
            p = self.by_seed.get(seed)
            if p is None:
                self.out.insert("end", "%s  -> 种子不存在 (共 %d 个)\n" % (raw, len(self.by_seed)))
                continue
            aid = self.inv_letters.get(code)
            if aid is None:
                self.out.insert("end", "%s  -> 位置 %s 不存在\n" % (raw, code))
                continue
            c = next((c for c in p.constructs if c.pos_index == aid), None)
            if c is None:
                self.out.insert("end", "%d/%s -> 该种子在此锚点无建筑\n" % (seed, code))
                continue
            name = self.info.get("castles", {}).get(c.type) or self.names.get(c.type, "")
            line = "%d/%s -> %s/%d/%s" % (seed, code, self.ni.get(c.type, "0"), c.type, name)
            if c.is_basement:
                line += "  [地下室]"
            if c.is_rooftop:
                line += "  [楼顶]"
            self.out.insert("end", line + "\n")


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
