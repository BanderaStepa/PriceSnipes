import pytest

from pricecheck.specs import check_gpu, check_ram

RAM_MATCH = [
    ("goodram IRDM BLACK SILVER UDIMM 32GB Kit, DDR5-6000, CL30-36-36-76, 1RX8", ""),
    ("Patriot Viper Venom RGB 32GB Kit DDR5-6000 CL30 (PVVR532G600C30K)", ""),
    ("Team T-Force DELTA RGB 32GB Kit DDR5-6000 CL30 (FF4D532G6000HC30DC01)", ""),
    # idealo style: name + spec line
    ("Lexar ARES OC", "DDR5-RAM, 32 GB, Anzahl Module 2, Kapazität pro Modul 16 GB, 6.000 MT/s, PC5-48.000, "
                      "CL 30-40-40-76, 1,35 V, UDIMM"),
]

RAM_SKIP = [
    ("Kingston 32GB DDR5-5600 CL46 (KCP556SD8-32)", "SODIMM", {"SO-DIMM", "DDR5-5600"}),
    ("Crucial Pro Overclocking 32GB Kit DDR5-6000 CL36", "", {"CL36"}),
    ("XPG Lancer Blade 32GB Kit DDR5-6000 CL48", "", {"CL48"}),
    ("GoodRAM IRDM RGB DDR5", "", {"specs not listed"}),
    ("Kingston FURY Impact 32GB Kit DDR5-6000 CL38 (KF560S38IBK2-32)", "SODIMM", {"SO-DIMM", "CL38"}),
    ("Patriot Viper Venom RGB 32GB Kit DDR5-6000 CL28", "", {"CL28"}),
    ("Kingston FURY Beast 32GB DDR5-6000 CL30 (KF560C30BWE-32)", "Anzahl Module 1", {"single module"}),
    ("Kingston FURY Beast 32GB DDR5-6000 CL30 (KF560C30BBE-32)", "", {"single module"}),
    ("G.Skill Trident Z5 64GB Kit DDR5-6000 CL30", "", {"not 32GB"}),
    ("Corsair Vengeance 32GB Kit DDR5-6000 CL30", "gebraucht", {"used/B-Ware"}),
    ("Gaming-PC Ryzen 7 32GB DDR5-6000 CL30 Kit", "", {"complete PC"}),
]


@pytest.mark.parametrize("name,specs", RAM_MATCH)
def test_ram_match(name, specs):
    assert check_ram(name, specs) == (True, None)


@pytest.mark.parametrize("name,specs,reasons", RAM_SKIP)
def test_ram_skip(name, specs, reasons):
    ok, reason = check_ram(name, specs)
    assert not ok
    assert reason in reasons


@pytest.mark.parametrize("text", [
    "Gainward GeForce RTX 5080 Phoenix, 16GB GDDR7, HDMI, 3x DP",
    "Palit GeForce RTX 5080 Infinity 3, 16GB GDDR7",
    "GIGABYTE GeForce RTX 5080 Aero OC SFF 16G",
    "Zotac Gaming GeForce RTX 5080 Solid",   # memory not mentioned -> allowed
])
def test_gpu_match(text):
    assert check_gpu(text) == (True, None)


@pytest.mark.parametrize("text,variant,reasons", [
    ("VARIANTEN ASUS GeForce RTX 5070 Ti", False, {"variant group", "RTX 5070 Ti"}),
    ("ASUS GeForce RTX 5080", True, {"variant group"}),
    ("MSI GeForce RTX 5070 Ti", False, {"RTX 5070 Ti"}),
    ("MSI GeForce RTX 5090 Suprim, 32GB GDDR7", False, {"RTX 5090"}),
    ("ASUS GeForce RTX 5080 SUPER, 24GB", False, {"RTX 5080 SUPER"}),
    ("Lenovo Legion Notebook RTX 5080 16GB", False, {"notebook/complete system"}),
    ("Gaming-PC Ryzen 7 RTX 5080 16GB", False, {"notebook/complete system"}),
])
def test_gpu_skip(text, variant, reasons):
    ok, reason = check_gpu(text, variant)
    assert not ok
    assert reason in reasons
