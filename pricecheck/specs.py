"""Spec rules for the "cheapest part with the required specs, any brand" filters (F1–F3).

Each check returns (matches: bool, reason: str | None). The reason is shown in the report
line listing skipped search hits.
"""
from __future__ import annotations

import re

# ----------------------------------------------------------------------------- RAM

RAM_USED = re.compile(r"gebraucht|b-ware|refurbished|generalüberholt|\bused\b", re.I)
RAM_COMPLETE_PC_ANY = re.compile(r"gaming-pc|pc-system|komplett|\bsysteme?\b", re.I)
RAM_COMPLETE_PC_NAME = re.compile(r"\bryzen\b|\bcore\s?i\d?\b|\bgeforce\b", re.I)
RAM_SODIMM = re.compile(r"so-?dimm|notebook|laptop", re.I)

RAM_CAPACITY_ANY = re.compile(r"\d+\s?GB", re.I)
RAM_32GB = re.compile(r"32\s?GB", re.I)
RAM_WRONG_CAPACITY = re.compile(r"64\s?GB|16\s?GB\s+Kit|48\s?GB|96\s?GB|128\s?GB", re.I)

RAM_SINGLE_MODULE = re.compile(r"anzahl\s+module\s*:?\s*1\b", re.I)
# Kingston single 32GB sticks like KF560C30BBE-32 / KF560C30BWE-32 (kits carry "K2", e.g. KF560C30BBEK2-32)
RAM_SINGLE_PARTNO = re.compile(r"\bKF\d{3}C\d{2}[A-Z]{2,4}-32\b")
RAM_TWO_MODULES = re.compile(r"\bkit\b|2\s?x\s?16|anzahl\s+module\s*:?\s*2\b", re.I)

RAM_ANY_SPEED = re.compile(r"ddr5-\d{4}|\d[.]?\d{3}\s?(?:mt/s|mhz)", re.I)
RAM_6000 = re.compile(r"ddr5-6000(?!\d)|6000\s?mt/s|6\.000\s?mt/s|6000\s?mhz|pc5-48\.?000", re.I)

RAM_CL_ANY = re.compile(r"\bCL\s?(\d{2})(?!\d)", re.I)


def check_ram(name: str, specs: str = "") -> tuple[bool, str | None]:
    """F1/F2: 32 GB kit (2 modules), DDR5-6000, CL30, desktop DIMM, new."""
    text = f"{name} {specs}"
    if RAM_USED.search(text):
        return False, "used/B-Ware"
    if RAM_COMPLETE_PC_ANY.search(text) or RAM_COMPLETE_PC_NAME.search(name):
        return False, "complete PC"
    if RAM_SODIMM.search(text):
        return False, "SO-DIMM"
    cls = RAM_CL_ANY.findall(text)
    if not RAM_CAPACITY_ANY.search(text) and not RAM_ANY_SPEED.search(text) and not cls:
        return False, "specs not listed"
    if not RAM_32GB.search(text) or RAM_WRONG_CAPACITY.search(text):
        return False, "not 32GB"
    if RAM_SINGLE_MODULE.search(text) or RAM_SINGLE_PARTNO.search(text):
        return False, "single module"
    if not RAM_TWO_MODULES.search(text):
        return False, "single module"
    if not RAM_6000.search(text):
        m = re.search(r"ddr5-(\d{4})", text, re.I)
        return False, f"DDR5-{m.group(1)}" if m else "speed not DDR5-6000"
    if not cls:
        return False, "CL not listed"
    if "30" not in cls:
        return False, f"CL{cls[0]}"
    return True, None


# ----------------------------------------------------------------------------- GPU

GPU_MODEL = re.compile(r"RTX\s?(50\d0)(?:\s?(Ti|SUPER))?", re.I)
GPU_5080 = re.compile(r"RTX\s?5080", re.I)
GPU_5080_VARIANT = re.compile(r"5080\s?(Ti|SUPER)\b", re.I)
GPU_OTHER_MODEL = re.compile(r"(?<!\d)50[679]0(?!\d)")
GPU_NOT_CARD = re.compile(r"notebook|laptop|gaming-pc|komplettsystem|\bsysteme?\b|pc-system", re.I)
GPU_MEMORY = re.compile(r"(?<!\d)(\d{1,2})\s?GB?\b", re.I)


def check_gpu(text: str, is_variant_group: bool = False) -> tuple[bool, str | None]:
    """F3: a single RTX 5080 graphics card (not Ti/SUPER, not a notebook or PC), 16 GB."""
    if is_variant_group or re.match(r"\s*VARIANTEN\b", text or ""):
        return False, "variant group"
    if GPU_NOT_CARD.search(text):
        return False, "notebook/complete system"
    if not GPU_5080.search(text):
        m = GPU_MODEL.search(text)
        if m:
            return False, "RTX " + m.group(1) + (f" {m.group(2)}" if m.group(2) else "")
        return False, "not RTX 5080"
    v = GPU_5080_VARIANT.search(text)
    if v:
        return False, f"RTX 5080 {v.group(1)}"
    other = GPU_OTHER_MODEL.search(text)
    if other:
        return False, f"mentions {other.group(0)}"
    mems = {int(x) for x in GPU_MEMORY.findall(text)}
    if mems and 16 not in mems:
        return False, f"{sorted(mems)[0]}GB (not 16GB)"
    return True, None
