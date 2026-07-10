#!/usr/bin/env python3
"""
Resource-load dashboard generator.

Usage:
  python resource_dashboard_generator.py --output dashboard.png --pdf dashboard.pdf
  python resource_dashboard_generator.py --data dashboard_data.json --output dashboard.png
  python resource_dashboard_generator.py --write-template dashboard_data_template.json

Data interface:
  The script accepts a JSON object with these top-level fields:
  project, metrics, labor, milestones, risk_windows, machines, notes.
  Keep this shape stable, and upstream Excel/API/multi-agent outputs only need
  to be converted into this JSON before drawing.
"""

from __future__ import annotations

import argparse
import json
import math
import urllib.request
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


DEFAULT_DATA: dict[str, Any] = {
    "project": {
        "title": "名创优品华南智能配送中心｜资源负荷图谱",
        "subtitle": "工程科研风格图谱 · 多智能体进度系统迁移参数集 · 基准计划与扰动测试窗口联动",
        "start": "2026-03-01",
        "end": "2026-10-27",
    },
    "metrics": [
        {"label": "总工期", "value": "240日历天", "color": "#2563EB"},
        {"label": "建筑面积", "value": "24500㎡", "color": "#0D9488"},
        {"label": "钢结构", "value": "580t", "color": "#7C3AED"},
        {"label": "地坪面积", "value": "21000㎡", "color": "#F59E0B"},
        {"label": "峰值配置", "value": "145人", "color": "#EF4444"},
    ],
    "labor": [
        {
            "name": "钢结构安装工",
            "peak": 35,
            "color": "#2563EB",
            "windows": [{"start": "2026-05-10", "end": "2026-06-20", "peak": 35, "ramp": 0.14}],
        },
        {
            "name": "木工",
            "peak": 25,
            "color": "#16A34A",
            "windows": [{"start": "2026-03-18", "end": "2026-06-08", "peak": 25, "ramp": 0.18}],
        },
        {
            "name": "钢筋工",
            "peak": 20,
            "color": "#7C3AED",
            "windows": [{"start": "2026-03-12", "end": "2026-06-02", "peak": 20, "ramp": 0.18}],
        },
        {
            "name": "混凝土工",
            "peak": 15,
            "color": "#F59E0B",
            "windows": [{"start": "2026-03-25", "end": "2026-07-05", "peak": 15, "ramp": 0.18}],
        },
        {
            "name": "地坪施工工",
            "peak": 30,
            "color": "#06B6D4",
            "windows": [{"start": "2026-07-01", "end": "2026-07-30", "peak": 30, "ramp": 0.18}],
        },
        {
            "name": "机电安装工",
            "peak": 20,
            "color": "#EF4444",
            "windows": [{"start": "2026-08-01", "end": "2026-10-10", "peak": 20, "ramp": 0.18}],
        },
    ],
    "milestones": [
        {"label": "桩基完工", "date": "2026-04-20"},
        {"label": "钢构封顶", "date": "2026-06-20"},
        {"label": "地坪完工", "date": "2026-07-30"},
        {"label": "消防验收", "date": "2026-10-10"},
        {"label": "竣工", "date": "2026-10-27"},
    ],
    "risk_windows": [
        {"label": "用例1 钢构供应延误", "start": "2026-05-10", "end": "2026-06-20", "color": "#3B82F6"},
        {"label": "用例2 暴雨+晾晒17天", "start": "2026-07-01", "end": "2026-07-17", "color": "#F97316"},
        {"label": "用例3 消防新增30%", "start": "2026-08-15", "end": "2026-09-20", "color": "#DC2626"},
    ],
    "machines": [
        {"name": "静力压桩机", "spec": "ZYJ-600", "quantity": 2, "start": "2026-03-01", "end": "2026-04-20", "color": "#4F46E5"},
        {"name": "混凝土输送泵", "spec": "HBT-60", "quantity": 1, "start": "2026-04-10", "end": "2026-07-05", "color": "#EA580C"},
        {"name": "汽车起重机", "spec": "25t", "quantity": 2, "start": "2026-04-15", "end": "2026-06-25", "color": "#2563EB"},
        {"name": "塔式起重机", "spec": "TC6012", "quantity": 1, "start": "2026-04-20", "end": "2026-06-30", "color": "#0891B2"},
        {"name": "直流电焊机", "spec": "ZX7-500", "quantity": 8, "start": "2026-05-10", "end": "2026-06-20", "color": "#9333EA"},
        {"name": "激光整平机", "spec": "S-940", "quantity": 1, "start": "2026-07-01", "end": "2026-07-30", "color": "#0D9488"},
    ],
    "notes": [
        "数据依据：工程概况、工程量、总工期、关键节点、机械进场时间、劳动力高峰配置及扰动用例。",
        "曲线为工程进度可视化建模结果，可替换为多智能体系统输出的真实资源负荷数据。",
    ],
}


W, H = 3600, 2200
BG = "#F6F8FB"
INK = "#172033"
MUTED = "#64748B"
GRID = "#D8E0EA"
PANEL = "#FFFFFF"
BORDER = "#C9D4E2"


def deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = deepcopy(base)
        for key, value in override.items():
            merged[key] = deep_merge(merged.get(key), value)
        return merged
    return deepcopy(override) if override is not None else deepcopy(base)


def parse_date(text: str) -> date:
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unsupported date format: {text!r}. Use YYYY-MM-DD.")


def date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgba(hex_color: str, alpha: int) -> tuple[int, int, int, int]:
    return (*hex_to_rgb(hex_color), alpha)


def blend(c1: str, c2: str, t: float) -> tuple[int, int, int]:
    a = hex_to_rgb(c1)
    b = hex_to_rgb(c2)
    return tuple(round((1 - t) * a[i] + t * b[i]) for i in range(3))


def font_candidates(kind: str) -> list[str]:
    if kind == "bold":
        return [
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        ]
    if kind == "mono":
        return [
            "C:/Windows/Fonts/consola.ttf",
            "/System/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/Monaco.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ]
    return [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]


def load_font(kind: str, size: int) -> ImageFont.ImageFont:
    for path in font_candidates(kind):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


F = {
    "title": load_font("bold", 68),
    "title_small": load_font("bold", 54),
    "subtitle": load_font("regular", 31),
    "panel": load_font("bold", 38),
    "label": load_font("regular", 27),
    "small": load_font("regular", 23),
    "tiny": load_font("regular", 20),
    "mono": load_font("mono", 24),
    "mono_big": load_font("mono", 42),
    "badge": load_font("bold", 24),
}


def text_wh(draw: ImageDraw.ImageDraw, text: str, ft: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=ft)
    return box[2] - box[0], box[3] - box[1]


def fit_text(draw: ImageDraw.ImageDraw, text: str, ft: ImageFont.ImageFont, max_width: int) -> str:
    if text_wh(draw, text, ft)[0] <= max_width:
        return text
    text = text.strip()
    while text and text_wh(draw, text + "…", ft)[0] > max_width:
        text = text[:-1]
    return text + "…" if text else "…"


def wrap_text(draw: ImageDraw.ImageDraw, text: str, ft: ImageFont.ImageFont, max_width: int, max_lines: int) -> list[str]:
    chars = list(text.strip())
    lines: list[str] = []
    current = ""
    for char in chars:
        candidate = current + char
        if current and text_wh(draw, candidate, ft)[0] > max_width:
            lines.append(current)
            current = char
            if len(lines) == max_lines - 1:
                break
        else:
            current = candidate
    remaining = "".join(chars[len("".join(lines) + current):])
    if remaining:
        current += remaining
    if current:
        if len(lines) >= max_lines:
            lines[-1] = fit_text(draw, lines[-1] + current, ft, max_width)
        else:
            lines.append(fit_text(draw, current, ft, max_width))
    return lines[:max_lines]


def rounded(draw: ImageDraw.ImageDraw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def smooth_window(day: date, start: date, end: date, peak: float, ramp: float) -> float:
    if day < start or day > end:
        return 0.0
    span = max((end - start).days + 1, 1)
    x = (day - start).days / max(span - 1, 1)
    r = min(max(ramp, 0.05), 0.45)
    if x < r:
        y = 0.5 - 0.5 * math.cos(math.pi * x / r)
    elif x > 1 - r:
        y = 0.5 - 0.5 * math.cos(math.pi * (1 - x) / r)
    else:
        y = 1.0
    return peak * y


def compute_labor_values(data: dict[str, Any], days: list[date]) -> None:
    for labor in data["labor"]:
        values: list[float] = []
        for day in days:
            total = 0.0
            for window in labor.get("windows", []):
                peak = float(window.get("peak", labor.get("peak", 0)))
                ramp = float(window.get("ramp", 0.16))
                total += smooth_window(day, parse_date(window["start"]), parse_date(window["end"]), peak, ramp)
            values.append(total)
        labor["_values"] = values


def sum_lists(items: list[list[float]]) -> list[float]:
    if not items:
        return []
    total = [0.0] * len(items[0])
    for values in items:
        for i, value in enumerate(values):
            total[i] += value
    return total


def date_x(day: date, start: date, end: date, left: int, width: int) -> float:
    return left + ((day - start).days / max((end - start).days, 1)) * width


def y_map(value: float, bottom: int, height: int, ymax: float) -> float:
    return bottom - (value / ymax) * height


def draw_pill(draw, x, y, label, value, accent):
    w_label, _ = text_wh(draw, label, F["tiny"])
    w_value, _ = text_wh(draw, value, F["badge"])
    w = max(230, w_label + w_value + 70)
    rounded(draw, (x, y, x + w, y + 86), 22, PANEL, "#D6DEE9", 2)
    draw.rectangle((x, y, x + 10, y + 86), fill=accent)
    draw.text((x + 28, y + 13), label, font=F["tiny"], fill=MUTED)
    draw.text((x + 28, y + 43), value, font=F["badge"], fill=INK)
    return w


def draw_panel_title(draw, x, y, letter, title, subtitle):
    rounded(draw, (x, y, x + 62, y + 52), 14, INK)
    draw.text((x + 19, y + 7), letter, font=F["panel"], fill="#FFFFFF")
    draw.text((x + 82, y + 1), title, font=F["panel"], fill=INK)
    draw.text((x + 82, y + 48), subtitle, font=F["small"], fill=MUTED)


def month_starts(start: date, end: date) -> list[date]:
    out = []
    cur = date(start.year, start.month, 1)
    while cur <= end:
        out.append(cur)
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return out


def draw_header(draw, data):
    title_lines = wrap_text(draw, data["project"]["title"], F["title_small"], 1470, 2)
    for index, line in enumerate(title_lines):
        draw.text((118, 58 + index * 62), line, font=F["title_small"], fill=INK)
    subtitle_y = 58 + len(title_lines) * 62 + 6
    draw.text((122, subtitle_y), data["project"]["subtitle"], font=F["subtitle"], fill=MUTED)
    x = 1650
    for metric in data.get("metrics", []):
        x += draw_pill(draw, x, 80, metric["label"], metric["value"], metric.get("color", "#2563EB")) + 20


def draw_labor_panel(img, draw, data, days, start, end):
    px, py, pw, ph = 92, 250, 3416, 920
    rounded(draw, (px, py, px + pw, py + ph), 30, PANEL, BORDER, 2)
    draw_panel_title(
        draw,
        px + 48,
        py + 38,
        "A",
        "劳动力资源负荷曲线（按日建模 / 按周阅读）",
        "峰值人数来自数据接口；曲线由工序窗口自动展开，可替换真实每日资源负荷",
    )

    left, top, width, height = px + 190, py + 220, pw - 330, 500
    bottom = top + height
    capacity = sum(float(item.get("peak", 0)) for item in data["labor"])
    labor_total = sum_lists([item["_values"] for item in data["labor"]])
    max_total = max(labor_total) if labor_total else 0
    ymax = max(25, math.ceil(max(capacity, max_total) / 25) * 25 + 10)

    overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
    od = ImageDraw.Draw(overlay)
    for risk in data.get("risk_windows", []):
        x0 = int(date_x(parse_date(risk["start"]), start, end, left, width))
        x1 = int(date_x(parse_date(risk["end"]), start, end, left, width))
        color = risk.get("color", "#DC2626")
        od.rectangle((x0, top, x1, bottom), fill=rgba(color, 30))
        od.line((x0, top, x0, bottom), fill=rgba(color, 110), width=2)
        od.line((x1, top, x1, bottom), fill=rgba(color, 80), width=1)
        tw, _ = text_wh(draw, risk["label"], F["tiny"])
        draw.rounded_rectangle((x0 + 8, top + 13, x0 + tw + 30, top + 48), radius=10, fill=PANEL, outline=color, width=1)
        draw.text((x0 + 19, top + 18), risk["label"], font=F["tiny"], fill=color)
    img.alpha_composite(overlay)

    step = 25 if ymax <= 175 else 50
    for v in range(0, int(ymax) + 1, step):
        y = int(y_map(v, bottom, height, ymax))
        draw.line((left, y, left + width, y), fill=GRID, width=2 if v == 0 else 1)
        draw.text((left - 70, y - 15), str(v), font=F["tiny"], fill=MUTED)
    draw.text((left - 112, top - 8), "人数", font=F["small"], fill=MUTED)

    for mday in month_starts(start, end):
        x = int(date_x(mday, start, end, left, width))
        draw.line((x, top, x, bottom), fill="#E8EEF5", width=1)
        draw.text((x + 6, bottom + 24), f"{mday.month}月", font=F["small"], fill=MUTED)
    draw.line((left, bottom, left + width, bottom), fill="#334155", width=3)
    draw.line((left, top, left, bottom), fill="#334155", width=3)

    cumulative = [0.0] * len(days)
    x_points = [date_x(day, start, end, left, width) for day in days]
    for labor in data["labor"]:
        vals = labor["_values"]
        upper = [cumulative[i] + vals[i] for i in range(len(days))]
        poly = [(int(x_points[i]), int(y_map(upper[i], bottom, height, ymax))) for i in range(len(days))]
        poly.extend(
            (int(x_points[i]), int(y_map(cumulative[i], bottom, height, ymax)))
            for i in range(len(days) - 1, -1, -1)
        )
        area = Image.new("RGBA", img.size, (255, 255, 255, 0))
        ad = ImageDraw.Draw(area)
        ad.polygon(poly, fill=rgba(labor.get("color", "#2563EB"), 205))
        img.alpha_composite(area)
        cumulative = upper

    total_points = [(int(x_points[i]), int(y_map(v, bottom, height, ymax))) for i, v in enumerate(labor_total)]
    if total_points:
        draw.line(total_points, fill="#0F172A", width=7, joint="curve")

    ycap = int(y_map(capacity, bottom, height, ymax))
    for x in range(left, left + width, 34):
        draw.line((x, ycap, x + 17, ycap), fill="#EF4444", width=3)
    draw.text((left + width - 300, ycap - 36), f"峰值配置总量 {int(capacity)}人", font=F["small"], fill="#EF4444")

    for i, milestone in enumerate(data.get("milestones", [])):
        mday = parse_date(milestone["date"])
        x = int(date_x(mday, start, end, left, width))
        draw.line((x, top - 8, x, bottom + 6), fill="#475569", width=2)
        draw.ellipse((x - 7, bottom - 7, x + 7, bottom + 7), fill=INK)
        y_text = bottom + 70 + (i % 2) * 38
        draw.text((x - 50, y_text), milestone["label"], font=F["tiny"], fill=INK)
        draw.text((x - 64, y_text + 26), mday.strftime("%m.%d"), font=F["tiny"], fill=MUTED)

    lx, ly = px + 132, py + 135
    count = len(data["labor"])
    gap = max(320, min(440, (pw - 280) // max(count, 1)))
    for i, labor in enumerate(data["labor"]):
        x = lx + i * gap
        draw.rounded_rectangle((x, ly, x + 34, ly + 34), radius=8, fill=labor.get("color", "#2563EB"))
        draw.text((x + 48, ly + 2), f"{labor['name']} {int(labor.get('peak', 0))}人", font=F["small"], fill=INK)

    if labor_total:
        peak_idx = max(range(len(labor_total)), key=lambda i: labor_total[i])
        peak_day = days[peak_idx]
        peak_value = int(round(labor_total[peak_idx]))
        px_peak = int(date_x(peak_day, start, end, left, width))
        py_peak = int(y_map(labor_total[peak_idx], bottom, height, ymax))
        draw.ellipse((px_peak - 13, py_peak - 13, px_peak + 13, py_peak + 13), fill=PANEL, outline="#0F172A", width=5)
        draw.line((px_peak + 18, py_peak - 18, px_peak + 180, py_peak - 104), fill="#0F172A", width=3)
        rounded(draw, (px_peak + 182, py_peak - 154, px_peak + 548, py_peak - 72), 18, "#0F172A")
        draw.text((px_peak + 205, py_peak - 143), "模型峰值负荷", font=F["small"], fill="#CBD5E1")
        draw.text((px_peak + 205, py_peak - 112), f"{peak_day.strftime('%Y.%m.%d')} · {peak_value}人", font=F["badge"], fill=PANEL)


def draw_machine_panel(img, draw, data, start, end):
    px, py, pw, ph = 92, 1244, 2115, 760
    rounded(draw, (px, py, px + pw, py + ph), 30, PANEL, BORDER, 2)
    draw_panel_title(
        draw,
        px + 48,
        py + 40,
        "B",
        "主要施工机械进场与占用强度",
        "机械数量与窗口来自数据接口；颜色深度对应数量强度，长条长度对应占用窗口",
    )

    left, top, width, row_h = px + 330, py + 180, pw - 430, 68
    for mday in month_starts(start, end):
        x = int(date_x(mday, start, end, left, width))
        draw.line((x, top - 22, x, top + row_h * len(data.get("machines", [])) + 20), fill="#E7EDF5", width=1)
        draw.text((x + 6, top - 58), f"{mday.month}月", font=F["tiny"], fill=MUTED)
    draw.line((left, top + row_h * len(data.get("machines", [])) + 18, left + width, top + row_h * len(data.get("machines", [])) + 18), fill="#334155", width=2)

    max_qty = max([float(m.get("quantity", 0)) for m in data.get("machines", [])] or [1.0])
    for i, machine in enumerate(data.get("machines", [])):
        y = top + i * row_h
        color = machine.get("color", "#2563EB")
        qty = float(machine.get("quantity", 0))
        draw.text((px + 68, y + 6), fit_text(draw, machine["name"], F["small"], 230), font=F["small"], fill=INK)
        draw.text((px + 68, y + 35), fit_text(draw, machine.get("spec", ""), F["tiny"], 230), font=F["tiny"], fill=MUTED)
        draw.line((left, y + row_h - 7, left + width, y + row_h - 7), fill="#EDF2F7", width=1)

        x0 = int(date_x(parse_date(machine["start"]), start, end, left, width))
        x1 = int(date_x(parse_date(machine["end"]), start, end, left, width))
        intensity = qty / max_qty if max_qty else 0
        fill = blend("#EAF2FF", color, 0.36 + 0.52 * intensity)
        draw.rounded_rectangle((x0, y + 11, x1, y + 50), radius=12, fill=fill, outline=color, width=2)
        draw.text((x0 + 14, y + 18), f"{int(qty)}台", font=F["badge"], fill=PANEL if qty >= 4 else INK)
        draw.text((x1 + 10, y + 18), parse_date(machine["end"]).strftime("%m.%d"), font=F["tiny"], fill=MUTED)

    draw.text((px + 68, py + ph - 100), "接口字段：name/spec/quantity/start/end/color，可由设备计划表自动映射。", font=F["small"], fill=MUTED)


def draw_donut(draw, data):
    px, py, pw, ph = 2250, 1244, 1258, 760
    rounded(draw, (px, py, px + pw, py + ph), 30, PANEL, BORDER, 2)
    draw_panel_title(
        draw,
        px + 48,
        py + 40,
        "C",
        "高峰劳动力配置结构",
        "自动读取 labor[].peak，适合与资源平衡结果对比",
    )

    total = sum(float(item.get("peak", 0)) for item in data.get("labor", []))
    cx, cy, r = px + 350, py + 418, 205
    start_angle = -90
    for labor in data.get("labor", []):
        extent = 360 * float(labor.get("peak", 0)) / total if total else 0
        draw.pieslice((cx - r, cy - r, cx + r, cy + r), start=start_angle, end=start_angle + extent, fill=labor.get("color", "#2563EB"))
        start_angle += extent
    draw.ellipse((cx - 118, cy - 118, cx + 118, cy + 118), fill=PANEL)
    draw.text((cx - 82, cy - 66), "合计", font=F["small"], fill=MUTED)
    draw.text((cx - 90, cy - 24), f"{int(total)}", font=F["mono_big"], fill=INK)
    draw.text((cx + 8, cy - 9), "人", font=F["small"], fill=INK)
    draw.text((cx - 70, cy + 42), "峰值配置", font=F["small"], fill=MUTED)

    lx, ly = px + 650, py + 205
    for i, labor in enumerate(data.get("labor", [])[:7]):
        y = ly + i * 64
        color = labor.get("color", "#2563EB")
        peak = float(labor.get("peak", 0))
        pct = peak / total if total else 0
        draw.rounded_rectangle((lx, y, lx + 30, y + 30), radius=8, fill=color)
        draw.text((lx + 47, y - 2), labor["name"], font=F["small"], fill=INK)
        draw.text((lx + 260, y - 2), f"{int(peak)}人", font=F["small"], fill=INK)
        draw.rounded_rectangle((lx + 350, y + 5, lx + 542, y + 25), radius=10, fill="#E8EEF5")
        draw.rounded_rectangle((lx + 350, y + 5, lx + 350 + int(192 * pct), y + 25), radius=10, fill=color)
        draw.text((lx + 562, y - 2), f"{pct * 100:.1f}%", font=F["tiny"], fill=MUTED)


def draw_footer(draw, data):
    y = 2070
    draw.line((118, y - 24, W - 118, y - 24), fill="#D7E0EA", width=2)
    notes = data.get("notes", [])
    if notes:
        draw.text((118, y), notes[0], font=F["small"], fill=MUTED)
    if len(notes) > 1:
        draw.text((118, y + 42), notes[1], font=F["tiny"], fill="#94A3B8")


def build_dashboard_data_from_rows(
    *,
    project_parameters: list[dict[str, Any]],
    schedule_rows: list[dict[str, Any]],
    resource_rows: list[dict[str, Any]],
    milestone_rows: list[dict[str, Any]] | None = None,
    event_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Convert blackboard rows into the dashboard generator JSON schema."""

    schedule_by_task = {str(row.get("task_id") or "").strip(): row for row in schedule_rows if row.get("task_id")}
    starts = [_safe_date(row.get("planned_start")) for row in schedule_rows]
    finishes = [_safe_date(row.get("planned_finish")) for row in schedule_rows]
    starts = [item for item in starts if item]
    finishes = [item for item in finishes if item]
    start = min(starts) if starts else date.today()
    end = max(finishes) if finishes else start
    project_name = _parameter_value(project_parameters, "P-004") or "施工项目"
    area = _parameter_value(project_parameters, "P-011")
    total_days = (end - start).days + 1

    labor_rows = [row for row in resource_rows if _is_labor(row)]
    machine_rows = [row for row in resource_rows if _is_machine(row)]
    labor = _build_labor_items(labor_rows, schedule_by_task)
    machines = _build_machine_items(machine_rows, schedule_by_task)
    peak_labor = int(sum(float(item.get("peak", 0)) for item in labor))

    return {
        "project": {
            "title": f"{project_name}｜资源负荷图谱",
            "subtitle": "由多智能体进度系统输出的排程、资源计划、里程碑与扰动记录自动生成",
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        "metrics": [
            {"label": "总工期", "value": f"{total_days}日历天", "color": "#2563EB"},
            {"label": "建筑面积", "value": f"{area or '-'}㎡", "color": "#0D9488"},
            {"label": "任务数量", "value": f"{len(schedule_rows)}项", "color": "#7C3AED"},
            {"label": "资源配置", "value": f"{len(resource_rows)}条", "color": "#F59E0B"},
            {"label": "峰值配置", "value": f"{peak_labor}人", "color": "#EF4444"},
        ],
        "labor": labor,
        "milestones": _build_milestone_items(milestone_rows or [], schedule_rows),
        "risk_windows": _build_risk_items(event_rows or [], schedule_by_task),
        "machines": machines,
        "notes": [
            "数据依据：项目参数、WBS 排程、资源需求、里程碑与事件记录。",
            "曲线为资源计划按工序窗口自动展开的图谱，可用于与扰动调整方案的资源结果对比。",
        ],
    }


def render_dashboard_from_rows(
    *,
    project_parameters: list[dict[str, Any]],
    schedule_rows: list[dict[str, Any]],
    resource_rows: list[dict[str, Any]],
    milestone_rows: list[dict[str, Any]] | None,
    event_rows: list[dict[str, Any]] | None,
    output: Path,
    pdf: Path | None = None,
) -> None:
    data = build_dashboard_data_from_rows(
        project_parameters=project_parameters,
        schedule_rows=schedule_rows,
        resource_rows=resource_rows,
        milestone_rows=milestone_rows,
        event_rows=event_rows,
    )
    render_dashboard(data, output, pdf)


def _build_labor_items(resource_rows: list[dict[str, Any]], schedule_by_task: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    palette = ["#2563EB", "#16A34A", "#7C3AED", "#F59E0B", "#06B6D4", "#EF4444", "#0D9488", "#9333EA"]
    grouped: dict[str, dict[str, Any]] = {}
    for row in resource_rows:
        task_id = str(row.get("task_id") or "").strip()
        schedule = schedule_by_task.get(task_id)
        if not schedule:
            continue
        start = _safe_date(schedule.get("planned_start"))
        end = _safe_date(schedule.get("planned_finish"))
        if not start or not end:
            continue
        name = str(row.get("resource_name") or "劳动力").strip()
        demand = _safe_float(row.get("demand"))
        item = grouped.setdefault(name, {"name": name, "peak": 0.0, "windows": [], "_weight": 0.0})
        item["peak"] = max(float(item["peak"]), demand)
        item["_weight"] += demand * max(1, (end - start).days + 1)
        item["windows"].append({"start": start.isoformat(), "end": end.isoformat(), "peak": demand, "ramp": 0.12})
    items = sorted(grouped.values(), key=lambda item: float(item.get("_weight", 0)), reverse=True)[:8]
    for index, item in enumerate(items):
        item["color"] = palette[index % len(palette)]
        item["peak"] = round(float(item.get("peak", 0)), 2)
        item.pop("_weight", None)
    return items or [{"name": "劳动力", "peak": 0, "color": palette[0], "windows": []}]


def _build_machine_items(resource_rows: list[dict[str, Any]], schedule_by_task: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    palette = ["#4F46E5", "#EA580C", "#2563EB", "#0891B2", "#9333EA", "#0D9488", "#64748B", "#DC2626"]
    items = []
    for row in resource_rows:
        task_id = str(row.get("task_id") or "").strip()
        schedule = schedule_by_task.get(task_id)
        if not schedule:
            continue
        start = _safe_date(schedule.get("planned_start"))
        end = _safe_date(schedule.get("planned_finish"))
        if not start or not end:
            continue
        items.append(
            {
                "name": str(row.get("resource_name") or "机械设备"),
                "spec": str(row.get("unit") or row.get("period") or ""),
                "quantity": max(1, int(round(_safe_float(row.get("demand")) or 1))),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "_duration": (end - start).days + 1,
            }
        )
    items = sorted(items, key=lambda item: (item["quantity"], item["_duration"]), reverse=True)[:8]
    for index, item in enumerate(items):
        item["color"] = palette[index % len(palette)]
        item.pop("_duration", None)
    return items


def _build_milestone_items(milestone_rows: list[dict[str, Any]], schedule_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    items = []
    for row in milestone_rows:
        day = _safe_date(row.get("actual_date") or row.get("target_date"))
        if day:
            items.append({"label": str(row.get("milestone_name") or row.get("milestone_id") or "里程碑"), "date": day.isoformat()})
    if items:
        return items[:6]
    if not schedule_rows:
        return []
    first = min(schedule_rows, key=lambda row: str(row.get("planned_start") or "9999"))
    last = max(schedule_rows, key=lambda row: str(row.get("planned_finish") or ""))
    return [
        {"label": "开工", "date": str(first.get("planned_start"))[:10]},
        {"label": "竣工", "date": str(last.get("planned_finish"))[:10]},
    ]


def _build_risk_items(event_rows: list[dict[str, Any]], schedule_by_task: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    colors = ["#3B82F6", "#F97316", "#DC2626", "#7C3AED"]
    items = []
    for index, row in enumerate(event_rows[:4]):
        related = str(row.get("related_task") or "").split(",")[0].strip()
        schedule = schedule_by_task.get(related)
        start = _safe_date(schedule.get("planned_start")) if schedule else None
        end = _safe_date(schedule.get("planned_finish")) if schedule else None
        if not start or not end:
            continue
        items.append(
            {
                "label": str(row.get("event_type") or row.get("event_id") or "扰动事件"),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "color": colors[index % len(colors)],
            }
        )
    return items


def _parameter_value(rows: list[dict[str, Any]], parameter_id: str) -> str:
    for row in rows:
        if str(row.get("parameter_id") or "") == parameter_id:
            return str(row.get("value") or "")
    return ""


def _is_labor(row: dict[str, Any]) -> bool:
    text = f"{row.get('resource_type') or ''} {row.get('resource_name') or ''}".lower()
    return "labor" in text or "班组" in text or "工" in text


def _is_machine(row: dict[str, Any]) -> bool:
    resource_type = str(row.get("resource_type") or "").lower()
    if "labor" in resource_type:
        return False
    text = f"{resource_type} {row.get('resource_name') or ''}".lower()
    return "equipment" in text or "机械" in text or "塔吊" in text or "施工电梯" in text or "设备" in text


def _safe_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return parse_date(text[:10])
    except ValueError:
        return None


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def load_dashboard_data(path: str | None = None, api_url: str | None = None) -> dict[str, Any]:
    if path:
        with open(path, "r", encoding="utf-8") as f:
            return deep_merge(DEFAULT_DATA, json.load(f))
    if api_url:
        # Reserved API interface. The endpoint should return the same JSON schema
        # used by --data, so drawing logic remains unchanged.
        with urllib.request.urlopen(api_url, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return deep_merge(DEFAULT_DATA, payload)
    return deepcopy(DEFAULT_DATA)


def render_dashboard(data: dict[str, Any], output: Path, pdf: Path | None = None) -> None:
    start = parse_date(data["project"]["start"])
    end = parse_date(data["project"]["end"])
    days = date_range(start, end)
    compute_labor_values(data, days)

    img = Image.new("RGBA", (W, H), BG)
    draw = ImageDraw.Draw(img)
    for x in range(0, W, 120):
        draw.line((x, 0, x, H), fill="#EEF3F8", width=1)
    for y in range(0, H, 120):
        draw.line((0, y, W, y), fill="#EEF3F8", width=1)

    draw_header(draw, data)
    draw_labor_panel(img, draw, data, days, start, end)
    draw_machine_panel(img, draw, data, start, end)
    draw_donut(draw, data)
    draw_footer(draw, data)

    output.parent.mkdir(parents=True, exist_ok=True)
    rgb = img.convert("RGB")
    rgb.save(output, "PNG", optimize=True)
    if pdf:
        pdf.parent.mkdir(parents=True, exist_ok=True)
        rgb.save(pdf, "PDF", resolution=300.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an engineering-style resource-load dashboard.")
    parser.add_argument("--data", help="Path to dashboard JSON data.")
    parser.add_argument("--api-url", help="Reserved API URL returning dashboard JSON data.")
    parser.add_argument("--output", default="resource_dashboard.png", help="Output PNG path.")
    parser.add_argument("--pdf", help="Optional output PDF path.")
    parser.add_argument("--write-template", help="Write a JSON data template and exit.")
    args = parser.parse_args()

    if args.write_template:
        target = Path(args.write_template)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(DEFAULT_DATA, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote template: {target}")
        return

    data = load_dashboard_data(args.data, args.api_url)
    render_dashboard(data, Path(args.output), Path(args.pdf) if args.pdf else None)
    print(f"Wrote PNG: {args.output}")
    if args.pdf:
        print(f"Wrote PDF: {args.pdf}")


if __name__ == "__main__":
    main()
