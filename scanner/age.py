"""How old is it? Apple products, laptops and phones from before 2020 are left out entirely: they resell
poorly (Diede, 9 Oct 2026; Macs only resell well from Apple Silicon, M1, Nov 2020).

release_year() reads the year from the lot title (then the description):
- a year in the title ("MacBook Pro 2017", "Mid 2015"), but not "Office 2016" or "Windows 2019";
- Macs: an M chip is 2020 or later, an Intel Mac without a year is older;
- iPads, iPhones, Apple Watch, Galaxy S/Note/A, Pixel, OnePlus, Huawei P/Mate: by model number;
- laptops: the Intel or Ryzen generation ("i5-8250U", "8th gen", "Ryzen 5 3500U"), HP's "G6", ThinkPad
  "T480"/"X1 Carbon Gen 7", Dell Latitude/XPS/Inspiron/Vostro numbers, Microsoft Surface numbers.
When nothing says the year ("Laptop HP", "MacBook Pro"), the lot stays: release_year() returns None.
"""
from __future__ import annotations

import re

from .identify import _CHIP_RE, _IPAD_GEN_RE, _IPAD_RE, _IPAD_YEARS, _MAC_LINES, ipad_size, gen_norm, matched_keyword
from .models import Lot, WatchItem
from .util import normalize

_APPLE_RE = re.compile(r"(?<![a-z0-9])(macbook|imac|mac (?:mini|studio|pro)|ipad|iphone|apple)(?![a-z0-9])")
_PHONE_RE = re.compile(r"(?<![a-z0-9])(iphone|smartphones?|telefoons?|gsm|mobiele telefoons?|galaxy|pixel|oneplus"
                       r"|huawei|xiaomi|redmi|oppo|motorola|nokia|fairphone)(?![a-z0-9])")
_LAPTOP_RE = re.compile(r"(?<![a-z0-9])(laptops?|notebooks?|ultrabook|chromebook|thinkpad|thinkbook|ideapad|yoga"
                        r"|elitebook|probook|zbook|latitude|xps|inspiron|vostro|precision|surface|vivobook|zenbook"
                        r"|expertbook|travelmate|aspire|swift|spectre|envy|pavilion|macbook)(?![a-z0-9])")


def device_kind(title: str, item_name: str = "") -> str | None:
    """"apple", "phone" or "laptop" when the age filter applies to this lot, else None."""
    t = normalize(title)
    if _APPLE_RE.search(t):
        return "apple"
    if _PHONE_RE.search(t):
        return "phone"
    if _LAPTOP_RE.search(t) or re.search(r"laptop|notebook", normalize(item_name)):
        return "laptop"
    return None


# ---------------------------------------------------------------- years

_YEAR_RE = re.compile(r"(?<![a-z0-9])(20[0-2][0-9])(?![a-z0-9])")
_NOT_DEVICE_YEAR = re.compile(r"(office|windows|server|exchange|sql|visual studio|autocad|project|visio) $")
# in a description a year is only the model year when it's written like one ("Mid 2015", "bouwjaar 2018")
_DESC_YEAR_RE = re.compile(r"(?<![a-z0-9])(?:early|mid|late|begin|medio|eind|model|modeljaar|bouwjaar|bj|uit)"
                           r" (20[0-2][0-9])(?![a-z0-9])")


def _years_written(t: str) -> list[int]:
    return [int(m.group(1)) for m in _YEAR_RE.finditer(t) if not _NOT_DEVICE_YEAR.search(t[:m.start()])]


# Mac model numbers (on the underside) that lot titles sometimes give instead of a year
_MAC_A_NUMBERS = {
    **dict.fromkeys(["a1278", "a1286", "a1297", "a1369", "a1370", "a1398", "a1418", "a1419", "a1425", "a1465",
                     "a1466", "a1502", "a1534", "a1706", "a1707", "a1708", "a1932", "a1989", "a1990", "a2159",
                     "a2115", "a2116", "a1993", "a1347"], 2017),
    **dict.fromkeys(["a2141"], 2019),
    **dict.fromkeys(["a2179", "a2251", "a2289"], 2020),  # last Intel MacBooks
    **dict.fromkeys(["a2337", "a2338", "a2348", "a2438", "a2439"], 2020),  # M1
    **dict.fromkeys(["a2442", "a2485", "a2681", "a2615", "a2686"], 2021),
    **dict.fromkeys(["a2779", "a2780", "a2941", "a2918", "a2992", "a2991", "a3113", "a3114", "a3112"], 2023),
}
_CHIP_YEARS = {1: 2020, 2: 2022, 3: 2023, 4: 2024, 5: 2025}


def _mac_year(t: str) -> tuple[int, str] | None:
    for code, year in _MAC_A_NUMBERS.items():
        if re.search(rf"(?<![a-z0-9]){code}(?![a-z0-9])", t):
            return year, f"model {code.upper()}"
    if not any(re.search(rf"(?<![a-z0-9]){ln}(?![a-z0-9])", t) for ln in _MAC_LINES):
        return None
    chip = _CHIP_RE.search(t)
    if chip:
        return _CHIP_YEARS.get(int(chip.group(1)), 2020), f"M{chip.group(1)} chip"
    if re.search(r"(?<![a-z0-9])(i[3579]|intel|core|xeon)(?![a-z0-9])", t):
        return 2019, "Intel Mac"  # the last Intel Macs (2020) only count when the title says 2020
    return None


# iPad Pro: (size, generation) -> year; 9.7 and 10.5 only had one generation
_IPAD_PRO = {("12.9", 1): 2015, ("12.9", 2): 2017, ("12.9", 3): 2018, ("12.9", 4): 2020, ("12.9", 5): 2021,
             ("12.9", 6): 2022, ("11", 1): 2018, ("11", 2): 2020, ("11", 3): 2021, ("11", 4): 2022,
             ("9.7", None): 2016, ("10.5", None): 2017}


def _ipad_year(title: str, t: str) -> tuple[int, str] | None:
    m = _IPAD_RE.search(t)
    if not m:
        return None
    line = "ipad" + (f" {m.group(1)}" if m.group(1) else "")
    chip = _CHIP_RE.search(t)
    if chip:
        return _CHIP_YEARS.get(int(chip.group(1)), 2021) + (1 if chip.group(1) == "1" else 0), f"M{chip.group(1)}"
    gen_m = _IPAD_GEN_RE.search(t)
    gen = int(gen_m.group(1)) if gen_m else None
    size = ipad_size(title, line)
    if gen is None and line != "ipad pro":
        after = re.match(r" (\d{1,2})(?![0-9])", t[m.end():])
        if after and not (size and size.split(".")[0] == after.group(1)):
            gen = int(after.group(1))
    if line == "ipad pro":
        year = _IPAD_PRO.get((size, gen)) or _IPAD_PRO.get((size, None))
    else:
        year = _IPAD_YEARS.get(line, {}).get(gen)
    if year:
        return year, f"{line} {size + ' ' if size and line == 'ipad pro' else ''}{gen or ''}".strip()
    return None


_IPHONE_OLD = {"3g": 2008, "3gs": 2009, "4": 2010, "4s": 2011, "5": 2012, "5s": 2013, "5c": 2013, "6": 2014,
               "6s": 2015, "7": 2016, "8": 2017, "x": 2017, "xs": 2018, "xr": 2018, "11": 2019}


def _iphone_year(t: str) -> tuple[int, str] | None:
    m = re.search(r"(?<![a-z0-9])iphone ?(3gs|3g|4s|5s|5c|xs|xr|x|se|\d{1,2})(?![0-9])", t)
    if not m:
        return None
    model = m.group(1)
    if model == "se":
        rest = t[m.end():m.end() + 25]
        if re.match(r" ?(?:\(?2(?:e|nd|de)?|second|tweede)(?: ?(?:gen|generatie|generation))?(?![0-9])", rest):
            return 2020, "iPhone SE 2nd gen"
        if re.match(r" ?(?:\(?3(?:e|rd|de)?|third|derde)(?: ?(?:gen|generatie|generation))?(?![0-9])", rest):
            return 2022, "iPhone SE 3rd gen"
        return None  # the first SE (2016) and later ones look the same in a title
    if model in _IPHONE_OLD:
        return _IPHONE_OLD[model], f"iPhone {model.upper() if model[0] == 'x' else model}"
    n = int(model)
    return (2008 + n, f"iPhone {n}") if 12 <= n <= 30 else None


def _watch_year(t: str) -> tuple[int, str] | None:
    m = re.search(r"(?<![a-z0-9])apple watch (?:series |s)?(\d{1,2})(?![0-9])", t)
    if m and 1 <= int(m.group(1)) <= 15:
        n = int(m.group(1))
        return (2016 if n <= 2 else 2014 + n), f"Apple Watch Series {n}"
    return None


def _android_year(t: str) -> tuple[int, str] | None:
    samsung = re.search(r"(?<![a-z0-9])(galaxy|samsung)(?![a-z0-9])", t)
    if samsung:
        m = re.search(r"(?<![a-z0-9])s ?(\d{1,2})(?:e|fe|plus|ultra)?(?![a-z0-9])", t[samsung.start():])
        if m and not re.search(r"(?<![a-z0-9])tab(?![a-z0-9])", t):  # Galaxy Tab S7 is a tablet
            n = int(m.group(1))
            year = 2009 + n if n <= 10 else 2000 + n if 20 <= n <= 40 else None
            if year:
                return year, f"Galaxy S{n}"
        m = re.search(r"(?<![a-z0-9])note ?(\d{1,2})(?![0-9])", t)
        if m:
            n = int(m.group(1))
            year = min(2011 + n, 2019) if n <= 10 else 2000 + n if n >= 20 else None
            if year:
                return year, f"Galaxy Note {n}"
        m = re.search(r"(?<![a-z0-9])a ?(\d{1,2})s?(?![0-9])", t[samsung.start():])
        if m:
            digits = m.group(1)
            if len(digits) == 1:
                return 2017, f"Galaxy A{digits}"  # A3/A5/A7/A8: 2015-2018
            return 2019 + int(digits[1]), f"Galaxy A{digits}"  # A50 2019, A51 2020, A52 2021, ...
        if re.search(r"(?<![a-z0-9])j ?\d(?![0-9])", t[samsung.start():]):
            return 2017, "Galaxy J"
        if re.search(r"(?<![a-z0-9])fold(?![a-z0-9])", t) and not re.search(r"(?<![a-z0-9])z (?:fold|flip)|fold ?\d", t):
            return 2019, "Galaxy Fold"
    m = re.search(r"(?<![a-z0-9])pixel ?(\d{1,2})(a)?(?![a-z0-9])", t)
    if m:
        n = int(m.group(1))
        return 2015 + n + (1 if m.group(2) else 0), f"Pixel {n}{m.group(2) or ''}"
    m = re.search(r"(?<![a-z0-9])oneplus ?(\d{1,2})t?(?![a-z0-9])", t)
    if m and int(m.group(1)) <= 15:
        n = int(m.group(1))
        return (2016 if n < 5 else 2012 + n), f"OnePlus {n}"
    m = re.search(r"(?<![a-z0-9])huawei (?:p|mate) ?(\d0)(?![0-9])", t)
    if m:
        return 2016 + int(m.group(1)) // 10, f"Huawei {m.group(1)}"
    return None


def _cpu_year(t: str) -> tuple[int, str] | None:
    if re.search(r"(?<![a-z0-9])core ultra(?![a-z0-9])", t):
        return 2023, "Core Ultra"
    m = re.search(r"(?<![a-z0-9])i[3579] ?(\d{4,5})(?:[a-z]{0,2}|g\d)(?![a-z0-9])", t)
    if m:
        digits = m.group(1)  # i5-8250U: 8th gen; i7-10510U, i5-1135G7, i5-1235U: 10th, 11th, 12th gen
        gen = int(digits[:2]) if len(digits) == 5 or re.match(r"1[0-4]", digits) else int(digits[0])
        if 2 <= gen <= 15:
            return 2010 + gen, f"Intel {gen}th gen"
    m = re.search(r"(?<![0-9])(\d{1,2}) ?(?:e|de|ste|th|st|nd|rd)? ?(?:gen|generatie|generation)\b", t)
    if m and re.search(r"(?<![a-z0-9])(i[3579]|intel|core)(?![a-z0-9])", t) and 2 <= int(m.group(1)) <= 15:
        gen = int(m.group(1))
        return 2010 + gen, f"Intel {gen}th gen"
    m = re.search(r"(?<![a-z0-9])ryzen [3579] (?:pro )?([2-9])\d{3}[a-z]{0,2}(?![a-z0-9])", t)
    if m:
        return 2016 + int(m.group(1)), f"Ryzen {m.group(1)}000"
    return None


def _laptop_model_year(t: str) -> tuple[int, str] | None:
    g = gen_norm(t)
    m = re.search(r"(?<![a-z0-9])(elitebook|probook|zbook)(?: [a-z0-9]+){0,3}? g(\d{1,2})(?![0-9])", g)
    if m:
        return 2013 + int(m.group(2)), f"HP {m.group(1).title()} G{m.group(2)}"
    m = re.search(r"(?<![a-z0-9])hp (?:2[45]\d|3[45]\d) g(\d{1,2})(?![0-9])", g)
    if m:
        return 2012 + int(m.group(1)), f"HP G{m.group(1)}"
    if re.search(r"thinkpad|lenovo", t):
        m = re.search(r"(?<![a-z0-9])x1 (carbon|yoga)(?: [a-z]+)? g(\d{1,2})(?![0-9])", g)
        if m:
            return (2012 if m.group(1) == "carbon" else 2015) + int(m.group(2)), f"X1 {m.group(1).title()} Gen {m.group(2)}"
        m = re.search(r"(?<![a-z0-9])([tlexa])(\d)(\d)[05][a-z]?(?![a-z0-9])", t)
        if m:
            return 2010 + int(m.group(3)), f"ThinkPad {m.group(0).upper()}"
        m = re.search(r"(?<![a-z0-9])p5([0-3])s?(?![a-z0-9])", t)
        if m:
            return 2016 + int(m.group(1)), f"ThinkPad P5{m.group(1)}"
    m = re.search(r"(?<![a-z0-9])latitude (e ?)?(\d)(\d)(\d)(\d)(?![0-9])", t)
    if m:
        if m.group(1):
            return 2016, "Dell Latitude E"
        c = int(m.group(4))
        return (2009 + c if c >= 6 else 2019 + c), f"Dell Latitude {m.group(2)}{m.group(3)}{c}{m.group(5)}"
    m = re.search(r"(?<![a-z0-9])(xps|inspiron|vostro)(?: 1[3-7])? (\d)(\d)(\d)(\d)(?![0-9])", t)
    if m and m.group(3) + m.group(4) + m.group(5) != "000":
        c = int(m.group(4))
        return (min(2009 + c, 2019) if c >= 6 else 2020 + (c if m.group(1) == "xps" else max(c - 1, 0))), \
            f"Dell {m.group(1).upper() if m.group(1) == 'xps' else m.group(1).title()} {''.join(m.groups()[1:])}"
    m = re.search(r"(?<![a-z0-9])surface (pro|laptop|book|go) ?(\d{1,2})(?![0-9])", t)
    if m:
        kind, n = m.group(1), int(m.group(2))
        first = {"pro": 2011, "laptop": 2016, "book": 2013, "go": 2016}[kind]
        plus = kind == "pro" and n == 7 and re.search(r"pro ?7 ?(?:plus|\+)", t)
        year = 2021 if plus else {"pro": {8: 2021, 9: 2022, 10: 2024, 11: 2024},
                                  "laptop": {4: 2021, 5: 2022, 6: 2024, 7: 2024},
                                  "book": {3: 2020}, "go": {2: 2020, 3: 2021, 4: 2023}}[kind].get(n, first + n)
        return year, f"Surface {kind.title()} {n}{'+' if plus else ''}"
    return None


def _inferred(title: str, t: str) -> list[tuple[int, str]]:
    found = [f(t) for f in (_mac_year, _iphone_year, _watch_year, _android_year, _cpu_year, _laptop_model_year)]
    found.append(_ipad_year(title, t))
    return [f for f in found if f]


def release_year(title: str, description: str = "") -> tuple[int, str] | None:
    """(year, why) for the newest thing the title says; then the description; None when nothing says it."""
    t = normalize(title.replace("+", " plus "))
    written = _years_written(t)
    if written:
        return max(written), str(max(written))
    found = _inferred(title, t)
    if not found and description:
        d = normalize(description[:400].replace("+", " plus "))
        found = _inferred(description[:400], d)
        found += [(int(y), y) for y in _DESC_YEAR_RE.findall(d)]
    return max(found) if found else None


def too_old(item: WatchItem, lot: Lot, min_year: int) -> tuple[int, str] | None:
    """(year, why) when this is an Apple product, laptop or phone from before min_year; else None.
    It's about what the lot matched on: "Laptop Dell Latitude 7400 + monitor Samsung 24inch" found by the
    Monitor item is priced as a monitor, so the laptop's age doesn't count."""
    if not device_kind(matched_keyword(item, lot.title), item.name):
        return None
    year = release_year(lot.title, lot.description)
    return year if year and year[0] < min_year else None


def age_filter(config: dict) -> int | None:
    """min_year from config.yml (age_filter), or None when it's off."""
    cfg = config.get("age_filter") or {}
    if cfg.get("enabled", True) is False:
        return None
    return int(cfg.get("min_year", 2020))
