"""Work out *what exactly* a lot is, so Marktplaats is searched for that item and nothing else.

"Beeldscherm 27 inch SAMSUNG S27C366EAU" is a Samsung S27C366EAU, not "a 27 inch monitor": comparing it
with every monitor on Marktplaats gives prices of other monitors. So:

- model_code()   finds the type number in a title ("S27C366EAU", "JR 3030T", "DX 460", "T440S"),
                 skipping specs such as 8GB, 18V, 27inch, i5 or 1080p;
- brand_in()     finds the brand ("samsung", "hp compaq" -> "compaq");
- plan_for()     turns that into Marktplaats searches plus the rules a listing must meet to count:
                 exact    the listing must name the same model (and brand, if the model code is short),
                 general  no type number in the title: category + brand + a few words (a rough price),
                 custom   the watchlist item has its own marktplaats_query;
- quantity()     reads "2 x", "Twee ..." or "- 3 stuks", so a lot of two monitors is worth two monitors.
- mac_plan()     Macs are told apart by chip and screen size ("MacBook Pro 16 M1 Max"), not a type number.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Lot, WatchItem
from .util import normalize, phrase_in, token_in

# Words that say nothing about which product it is.
NOISE = set("""
met zonder en de het een van voor in op tot aan bij of als uit incl inclusief excl exclusief
with and the for of to from new nieuw nieuwe gebruikt used zgan zga ongebruikt nieuwstaat
partij restpartij voorraad diverse div divers kavel lot set stuks stuk st stk pcs x cm mm m kg gr g l ltr liter
v w watt gb tb inch type model merk bj bwjr bouwjaar ca circa oa etc zie foto fotos afbeelding afbeeldingen
maat size nr no art artikel serie series gen generatie generation
zwart wit grijs zilver blauw rood groen bruin beige black white grey gray silver blue red
twee drie vier vijf zes zeven acht negen tien
ontbreekt ontbreken krasje kras krassen geen beschadigd beschadiging schade werkend werkt getest ongetest
compleet toebehoren accessoires voedingskabel stroomkabel kabel kabels adapter lader oplader afstandsbediening
heren dames man mannen vrouw vrouwen kinder kinderen ouderwetse vintage verschillende kleuren kleur
doos originele verpakking koffer opslag ram geheugen werkgeheugen processor hdd ssd ghz mhz hz
""".split())

# Category words: they say what kind of thing it is, not which one. Listings may use any word of a group.
CATEGORIES = [
    ("monitor", "monitoren", "beeldscherm", "beeldschermen"),
    ("laptop", "laptops", "notebook", "notebooks"),
    ("koffiemachine", "koffiemachines", "koffieapparaat", "koffiezetapparaat", "espressomachine",
     "espressoapparaat", "volautomaat", "espresso machine"),
    ("colbert", "colberts", "blazer", "blazers", "jasje", "jasjes"),
    ("kostuum", "kostuums", "maatpak", "maatpakken"),
    ("smoking", "smokings"),
    ("kunstplant", "kunstplanten", "nepplant", "kunstboom", "kunstbomen"),
    ("kunstbloemen", "kunstbloem"),
    ("olijfboom", "olijfbomen"),
    ("airfryer", "heteluchtfriteuse"),
    ("computer", "pc", "desktop"),
    ("printer", "printers"),
    ("tablet", "tablets"),
]
_CATEGORY_OF = {w: group for group in CATEGORIES for w in group}
# In a lot like "HP desktop met beeldscherm" these words name other things in the bundle.
_BUNDLE_WORDS = set(CATEGORIES[0] + CATEGORIES[1] + CATEGORIES[10] + CATEGORIES[11] + CATEGORIES[12])

BRANDS = """
acer aoc apple asus benq compaq dell eizo fujitsu gigabyte hp huawei iiyama lenovo lg medion microsoft msi nec
philips razer samsung sharp sony terra toshiba viewsonic xiaomi alienware hyundai panasonic canon epson brother
aeg bosch dewalt einhell festool fein flex hikoki hilti hitachi makita metabo milwaukee ryobi stanley worx
mafell kress skil kärcher karcher husqvarna stihl
braun breville delonghi gaggia jura kenwood kitchenaid krups lelit magimix melitta miele moulinex nespresso
rancilio saeco sage siemens smeg tefal thermomix vitamix dyson ninja cosori princess senseo bialetti rocket
profitec
suitsupply boss boggi dsquared2 zara cavallaro oger
""".split()
MULTI_BRANDS = ["hugo boss", "de longhi", "la marzocco", "loro piana", "ralph lauren", "tommy hilfiger",
                "black decker", "van gils", "scotch soda", "g star"]
_BRANDS = set(BRANDS)

# Tokens that look like model codes but are specifications, versions or ordinals.
_SPEC_RE = re.compile(r"""^(?:
    \d+(?:gb|tb|mb|kb|gig)
  | \d{1,3}(?:v|vac|vdc|ah|hz|bar|psi|pk|u|h|m|l|cl|dl|t)
  | \d{1,2}(?:a|k|e|de|ste|th|st|nd|rd)
  | \d{1,5}(?:w|kw|wh|kwh|mah|ml|mg|g|gr|kg|mm|cm|dm|nm|rpm|lm|db|ms|fps|mp|mpx|mbps|gbps|mhz|ghz|ltr|inch
             |in|st|stk|stuks|pcs|delig|dlg|dlig|x|jr|jaar|min|sec|km|mtr|liter|persoons|pers)
  | \d{3,4}(?:p|i)
  | x\d{1,3}
  | \d+x\d+(?:x\d+)?(?:mm|cm|dm|m|mtr|inch|in)?        # sizes: 180x90cm, 60x60x5
  | (?:usb|hdmi|ddr|lpddr|gddr|wifi|cat|sata|pcie|bt|dp|mp|gen|rev|ver|nr|no|type|art|ean|sku|ip|pd|qc)\d{1,3}[a-z]?
  | i[3579](?:\d{3,5}[a-z]{0,2})?
  | r[3579](?:\d{3,4}[a-z]{0,2})?
  | a\d{4}
  | \d+(?:th|st|nd|rd)gen
  | (?:19|20)\d{2}s?
)$""", re.X)

# After one of these words the next few numbers describe a processor or graphics card, not the product.
_CPU_WORDS = {"i3", "i5", "i7", "i9", "ryzen", "core", "celeron", "pentium", "athlon", "xeon", "snapdragon",
              "exynos", "atom", "amd", "intel", "gtx", "rtx", "geforce", "radeon", "quadro", "apple"}


@dataclass
class Rule:
    """A Marktplaats listing counts as comparable when its title matches one alternative of every group
    (and contains the model code, if there is one)."""
    groups: list[tuple[str, ...]]
    model: str | None = None  # squashed model code, e.g. "jr3030t"
    label: str = ""  # human readable, e.g. "makita jr 3030t"

    size: str | None = None  # screen size in inches the listing must mention ("16" or "10.5", not "16 GB")
    without: tuple[str, ...] = ()  # words the listing must not have ("pro" for a plain iPad)
    without_parts: tuple[str, ...] = ()  # nor anywhere inside a word ("machine" in "accuboormachine")
    gen_norm: bool = False  # read "Gen 2" and "15G2" as "g2" / "15 g2" first
    ram: str | None = None  # memory the listing must mention: "32" -> "32GB" / "32 GB"

    def matches(self, title_norm: str) -> bool:
        if self.gen_norm:
            title_norm = gen_norm(title_norm)
        if self.ram and not re.search(rf"(?<![0-9]){self.ram} ?gb(?![a-z0-9])", title_norm):
            return False
        if self.model and not model_in(self.model, title_norm):
            return False
        if any(part in title_norm for part in self.without_parts):
            return False
        if self.size and not size_in(self.size, title_norm):
            return False
        if any(token_in(w, title_norm) for w in self.without):
            return False
        return all(any(phrase_in(alt, title_norm) for alt in group) for group in self.groups)


@dataclass
class SearchPlan:
    kind: str  # "exact", "general" or "custom"
    searches: list[str]  # Marktplaats searches, most specific first (at most two are sent)
    rules: list[Rule]  # tried in order on the combined search results
    model: str | None = None  # display form, e.g. "S27C366EAU"
    brand: str | None = None
    note: str = ""  # why the plan is what it is, shown on the dashboard
    rough: bool = False  # always a rough price, even when the rule has a number in it (Intel MacBook "16")

    @property
    def exact(self) -> bool:
        return self.kind in ("exact", "custom")


# ---------------------------------------------------------------- pieces


def is_brand(word: str) -> bool:
    w = normalize(word)
    return w in _BRANDS or w in MULTI_BRANDS


def is_spec(token: str) -> bool:
    return bool(_SPEC_RE.match(token))


def _mixed(token: str) -> bool:
    return any(c.isdigit() for c in token) and any(c.isalpha() for c in token)


def model_in(code: str, text_norm: str) -> bool:
    """True if the (squashed) model code starts a word of the text, also when the text writes it with
    spaces or dashes: "jr3030t" matches "JR3030T", "JR 3030T" and "JR-3030T"; "dx460" matches "DX 460"
    but not "DX 4600"."""
    code = code.replace(" ", "")
    tokens = text_norm.split()
    for i in range(len(tokens)):
        joined = ""
        for j in range(i, min(len(tokens), i + 4)):
            joined += tokens[j]
            if joined.startswith(code):
                rest = joined[len(code):]
                return not (rest and code[-1].isdigit() and rest[0].isdigit())
            if not code.startswith(joined):
                break
    return False


_NOT_INCHES = {"gb", "tb", "g", "gig", "ram", "core", "cores", "gpu", "cpu", "x", "stuks", "mp", "mm", "cm", "w"}
_INCH_RE = re.compile(r"(?<![\d.,])(\d{2})(?:[.,]\d)?\s*(?:\"|”|“|″|''|-?\s?inch\b|-?\s?in\b|-?\s?zoll\b)", re.I)


def size_in(size: str, text_norm: str) -> bool:
    """The listing mentions this screen size: "16 inch" or "16" yes, "16 GB" no. "10.5" is written
    "10.5", "10,5" or "10.5-inch", which normalise to "10 5"."""
    tokens = text_norm.split()
    whole, _, frac = size.partition(".")
    for i, tok in enumerate(tokens):
        if frac:
            if tok == whole and i + 1 < len(tokens) and tokens[i + 1] in (frac, f"{frac}inch"):
                nxt = tokens[i + 2] if i + 2 < len(tokens) else ""
                if nxt not in _NOT_INCHES:
                    return True
            continue
        if tok in (size, f"{size}inch"):
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            if nxt not in _NOT_INCHES:
                return True
    return False


def inches(title: str) -> str | None:
    """'MacBook Pro 16”' -> '16', '15.6 inch' -> '15'."""
    m = _INCH_RE.search(title or "")
    return m.group(1) if m else None


_MAC_LINES = ["macbook air", "macbook pro", "mac mini", "mac studio", "mac pro", "imac", "macbook"]
_CHIP_RE = re.compile(r"(?<![a-z0-9])m([1-5])(?: (pro|max|ultra))?(?![a-z0-9])")
_INTEL_RE = re.compile(r"(?<![a-z0-9])(i[3579]|intel|core|xeon)(?![a-z0-9])")


_MAC_CPU_RE = re.compile(r"(?<![a-z0-9])(i[3579])(?![a-z0-9])")


def mac_ram(text_norm: str) -> str | None:
    """"32 GB RAM, 1 TB" -> "32"; "16GB 512GB" -> "16" (memory comes before storage; Macs have at least 128 GB
    storage)."""
    m = re.search(r"(?<![0-9])(8|16|18|24|32|36|48|64|96) ?gb(?: (?:ram|geheugen|werkgeheugen|memory|unified))?(?![a-z0-9])",
                  text_norm)
    return m.group(1) if m else None


def mac_plan(title: str) -> SearchPlan | None:
    """Macs have no type number in lot titles; what sets the price is the line, the chip or processor, the screen
    size and the memory.
    "MacBook Pro 16, M1 Max, 32 GB" -> listings that say macbook pro + 16 inch + m1 max (+ 32 GB when there are
    enough of those): exact;
    "MacBook Pro 16 Core i7 9th Gen, 32 GB RAM" -> MacBook Pro 16 listings with an i7 (+ 32 GB), not i9: a rough price,
    as storage and years vary."""
    t = normalize(title)
    line = next((ln for ln in _MAC_LINES if re.search(rf"(?<![a-z0-9]){ln}(?![a-z0-9])", t)), None)
    if not line:
        return None
    size = inches(title)
    ram = mac_ram(t)
    base = [(w,) for w in line.split()]
    shown = line.replace("macbook", "MacBook").replace("imac", "iMac").replace("mac ", "Mac ")
    shown = " ".join(w if w[0].isupper() else w.title() for w in shown.split())
    chip = _CHIP_RE.search(t)
    if chip:
        name = "m" + chip.group(1) + (f" {chip.group(2)}" if chip.group(2) else "")
        groups = base + [(w,) for w in name.split()]
        rules = []
        if size and ram:
            rules.append(Rule(groups, size=size, ram=ram, label=f"{line} {size} {name} {ram}gb"))
        if size:
            rules.append(Rule(groups, size=size, label=f"{line} {size} {name}"))
        rules.append(Rule(groups, label=f"{line} {name}"))
        searches = list(dict.fromkeys([f"{line} {size} {name}" if size else f"{line} {name}", f"{line} {name}"]))
        return SearchPlan("exact", searches, rules, model=f"{shown}{' ' + size if size else ''} {name.upper()}",
                          brand="apple", note=f"exact: {line} {size + ' inch ' if size else ''}{name}"
                          + (f" ({ram} GB if enough listings say so)" if ram else ""))
    intel = _INTEL_RE.search(t)
    year = re.search(r"(?<![0-9])(20[12][0-9])(?![0-9])", t)
    if not (intel or year):
        return None
    cpu = _MAC_CPU_RE.search(t)
    if cpu:  # "Core i7": an i7, not an i9 or an i5
        which, what = (cpu.group(1),), cpu.group(1)
    elif year:
        which, what = (year.group(1),), year.group(1)
    else:
        which, what = ("intel", "i5", "i7", "i9", "2016", "2017", "2018", "2019", "2020"), "intel"
    rules = []
    if ram:
        rules.append(Rule(base + [which], size=size, ram=ram, label=" ".join(x for x in (line, size, what, f"{ram}gb") if x)))
    rules.append(Rule(base + [which], size=size, label=" ".join(x for x in (line, size, what) if x)))
    # listings rarely say "intel": search the line and size, the rule keeps the Intel ones
    searches = [" ".join(x for x in (line, size, what if what != "intel" else None) if x)]
    searches.append(f"{line} {size}" if size and what != "intel" else f"{line} intel")
    searches = list(dict.fromkeys(searches))
    return SearchPlan("general", searches, rules, brand="apple", rough=not year,
                      note=(f"Intel {shown}{' ' + size if size else ''} {what if what != 'intel' else ''}"
                            f"{' ' + ram + ' GB' if ram else ''}: a rough price, storage and years vary")
                      .replace("  ", " ") if not year else f"{line} {year.group(1)}")


# iPads: line, generation, chip and screen size set the price; lot titles rarely have a type number
_IPAD_RE = re.compile(r"(?<![a-z0-9])ipad(?: (pro|air|mini))?(?![a-z0-9])")
_IPAD_SIZE_RE = re.compile(r"(?<![\d.,])(7[.,]9|8[.,]3|9[.,]7|10[.,][259]|11|12[.,]9|13)(?:[.,]0)?\s*"
                           r"(\"|”|“|″|''|-?\s?inch\b|-?\s?in\b|-?\s?zoll\b)?", re.I)
_IPAD_GEN_RE = re.compile(r"(?<![0-9])(\d{1,2}) ?(?:e|ste|de|th|st|nd|rd)? ?(?:gen|generatie|generation)\b")
# generation -> year it came out, for listings that give the year instead
_IPAD_YEARS = {"ipad": {5: 2017, 6: 2018, 7: 2019, 8: 2020, 9: 2021, 10: 2022, 11: 2025},
               "ipad air": {2: 2014, 3: 2019, 4: 2020, 5: 2022},
               "ipad mini": {4: 2015, 5: 2019, 6: 2021, 7: 2024}}
# sizes that pin one model on their own
_IPAD_UNIQUE = {("ipad pro", "10.5"), ("ipad pro", "9.7"), ("ipad air", "10.5")}


def ipad_size(title: str, line: str) -> str | None:
    """"iPad Pro 10,5 inch" -> "10.5"; "iPad Pro 11" -> "11" (for a plain iPad, 11 is the generation)."""
    for m in _IPAD_SIZE_RE.finditer(title or ""):
        size = m.group(1).replace(",", ".")
        if "." in size or m.group(2) or line in ("ipad pro", "ipad air"):
            return size
    return None


def ipad_plan(title: str) -> SearchPlan | None:
    """"iPad Pro 10,5 inch" -> listings that say ipad pro + 10.5; "iPad 6th Gen." -> plain iPads (not Pro, Air
    or mini) of the 6th generation or 2018; "Apple iPad Air 5 64GB" -> iPad Air 5. A line without generation,
    chip, year or a telling size ("iPad Pro") is a rough price."""
    t = normalize(title)
    m = _IPAD_RE.search(t)
    if not m:
        return None
    line = "ipad" + (f" {m.group(1)}" if m.group(1) else "")
    base: list[tuple[str, ...]] = [(w,) for w in line.split()]
    without = () if m.group(1) else ("pro", "air", "mini")
    size = ipad_size(title, line)
    chip = _CHIP_RE.search(t)
    gen_m = _IPAD_GEN_RE.search(t)
    gen = int(gen_m.group(1)) if gen_m else None
    if gen is None and not chip:  # "iPad Air 5", "iPad 9": the number right after the line
        after = re.match(r" (\d{1,2})(?![0-9])", t[m.end():])
        if after and not (size and size.split(".")[0] == after.group(1)):
            gen = int(after.group(1))
    year_m = re.search(r"(?<![0-9])(20[12][0-9])(?![0-9])", t)
    year = int(year_m.group(1)) if year_m else _IPAD_YEARS.get(line, {}).get(gen)
    shown = line.replace("ipad", "iPad").replace(" pro", " Pro").replace(" air", " Air").replace(" mini", " mini")

    which: tuple[str, ...] | None = None
    if chip:
        name = "m" + chip.group(1)
        which, what = (name,), name.upper()
    elif gen:
        which = (f"{gen}e gen", f"{gen}th gen", f"{gen}e generatie", f"{gen}de generatie", f"{gen} generatie",
                 f"{gen}th generation", f"{line} {gen}") + ((str(year),) if year else ())
        what = f"{gen}th gen"
    elif year:
        which, what = (str(year),), str(year)
    if which:
        rules = ([Rule(base + [which], size=size, without=without, label=f"{line} {size} {which[0]}")] if size else []) \
            + [Rule(base + [which], without=without, label=f"{line} {which[-1] if gen and not chip else which[0]}")]
        searches = [f"{line} {gen}" if gen and not chip else f"{line} {which[0]}"]
        if year and gen and not chip:
            searches.append(f"{line} {year}")
        return SearchPlan("exact", searches, rules, model=f"{shown}{' ' + size if size else ''} {what}", brand="apple",
                          note=f"exact: {shown}{' ' + size + ' inch' if size else ''} {what}")
    if size:
        rule = Rule(base, size=size, without=without, label=f"{line} {size}")
        exact = (line, size) in _IPAD_UNIQUE
        return SearchPlan("exact" if exact else "general", [f"{line} {size}"], [rule],
                          model=f"{shown} {size}" if exact else None, brand="apple", rough=not exact,
                          note=f"exact: {shown} {size} inch" if exact else f"{shown} {size} inch: a rough price, generations vary")
    return None


# HP and Lenovo name a laptop or PC by line, model and generation: "ZBook Firefly 14 G10", "EliteBook 840 G5",
# "ThinkBook 15 G2 ITL", "ThinkPad T14 Gen 2". The generation alone ("G10") says nothing.
_GEN_TOKEN = re.compile(r"g(\d{1,2})")
_ORDINAL = re.compile(r"\d+(?:th|st|nd|rd|e|de|ste)")


def gen_norm(text_norm: str) -> str:
    """Write generations one way: "gen 2" / "gen2" -> "g2", "15g2" -> "15 g2" (but "13th gen" is a processor)."""
    toks, out, i = text_norm.split(), [], 0
    while i < len(toks):
        t = toks[i]
        m = re.fullmatch(r"(1[0-8])g(\d{1,2})", t)
        if m:
            out += [m.group(1), "g" + m.group(2)]
        elif t in ("gen", "generation") and i + 1 < len(toks) and re.fullmatch(r"\d{1,2}", toks[i + 1]) \
                and not (out and _ORDINAL.fullmatch(out[-1])):
            out.append("g" + toks[i + 1])
            i += 1
        elif re.fullmatch(r"gen(\d{1,2})", t):
            out.append("g" + t[3:])
        else:
            out.append(t)
        i += 1
    return " ".join(out)


def _gen_tokens(text: str) -> list[str]:
    return gen_norm(normalize(_INCH_RE.sub(lambda m: f" {m.group(1)}inch ", text or ""))).split()


def _generation(title: str) -> SearchPlan | None:
    tokens = _gen_tokens(title)
    gi = next((i for i, t in enumerate(tokens) if i and _GEN_TOKEN.fullmatch(t)), None)
    if gi is None:
        return None
    size = inches(title)
    words: list[str] = []
    brand = None
    j = gi - 1
    while j >= 0 and len(words) < 3:
        tok = tokens[j]
        if tok in _BRANDS:
            brand = tok
            break
        if tok in NOISE or category(tok) or tok in _CPU_WORDS:
            break
        if re.fullmatch(r"1[0-8](?:inch)?", tok):  # "ZBook Studio 16 G10", "ThinkBook 15 G2": the screen size
            size = size or tok.replace("inch", "")
        elif (tok.isdigit() and len(tok) == 3) or (tok.isalpha() and len(tok) >= 3) or re.fullmatch(r"[a-z]{1,2}\d{1,3}", tok):
            words.insert(0, tok)  # "elitebook 840", "zbook firefly", "thinkpad t14", "x1 carbon"
        else:
            break
        j -= 1
    if not words:
        return None
    gen = tokens[gi]
    groups: list[tuple[str, ...]] = [(w,) for w in words] + [(gen,)]
    if all(w.isdigit() for w in words):  # "HP 250 G8": the brand is part of the name
        if not brand:
            return None
        groups.insert(0, (brand,))
        words = [brand] + words
    name = " ".join(words)
    # without the size in the listing is fine, with another size is not ("ThinkBook 14 G2" for a 15 G2)
    other_sizes = tuple(f"{o} {gen}" for o in range(10, 19) if str(o) != size) if size else ()
    rules = ([Rule(groups, size=size, label=f"{name} {size} {gen}", gen_norm=True)] if size else []) + \
        [Rule(groups, label=f"{name} {gen}", gen_norm=True, without_parts=other_sizes)]
    shown = " ".join(("HP" if w == "hp" else w.title()) if w.isalpha() else w.upper() for w in words) + \
        (f" {size}" if size else "") + f" {gen.upper()}"
    for a, b in (("Zbook", "ZBook"), ("Elitebook", "EliteBook"), ("Probook", "ProBook"), ("Elitedesk", "EliteDesk"),
                 ("Prodesk", "ProDesk"), ("Thinkbook", "ThinkBook"), ("Thinkpad", "ThinkPad"), ("Ideapad", "IdeaPad")):
        shown = shown.replace(a, b)
    return SearchPlan("exact", list(dict.fromkeys(r.label for r in rules)), rules, model=shown, brand=brand,
                      note=f"exact: {shown}")


def generation_plan(title: str, description: str = "") -> SearchPlan | None:
    """"HP ZBook Firefly G10 14”" -> listings that say zbook + firefly + g10 + 14 inch (exact), not a ZBook Fury
    16 G10; "HP EliteBook 840 G5" -> elitebook + 840 + g5. When only the description has it ("Laptop Lenovo
    ThinkBook" + "type: 15 g2 itl"), the line comes from the title and size and generation from the description."""
    plan = _generation(title)
    if plan or not description:
        return plan
    d = _gen_tokens(description[:200])
    gi = next((i for i, t in enumerate(d) if _GEN_TOKEN.fullmatch(t)), None)
    if gi is None:
        return None
    t = normalize(title).split()
    line = [w for w in t if w.isalpha() and len(w) >= 3 and w not in NOISE and w not in _BRANDS and not category(w)
            and w not in _CPU_WORDS][:2]
    if not line:
        return None
    before = [w for w in d[max(0, gi - 2):gi] if w not in NOISE and w not in line]
    brand = brand_in(title)
    plan = _generation(" ".join(([brand] if brand else []) + line + before + [d[gi]]))
    if plan:
        plan.note += " (size and generation from the lot description)"
    return plan


# A battery (with or without charger) is not a drill that comes with one.
_BATTERY_WORDS = ("accu", "batterij", "battery", "akku")
TOOL_PARTS = ("machine", "boor", "zaag", "slijp", "schroef", "hamer", "tacker", "frees", "schuur", "lamp", "radio",
              "stofzuig", "blazer", "trimmer", "maaier", "combiset", "combo")
_VOLT_RE = re.compile(r"(?<![\d.,])(\d{1,2}(?:[.,]\d)?)\s?(?:v|volt)\b", re.I)


def is_battery_lot(title: str) -> bool:
    t = normalize(title)
    return any(token_in(w, t) for w in _BATTERY_WORDS) and not any(part in t for part in TOOL_PARTS)


def voltage(title: str) -> tuple[str, ...] | None:
    """"Accu Makita 12V 1.9Ah" -> ways a listing writes 12 V: ("12v", "12 volt", "12 v")."""
    m = _VOLT_RE.search(title or "")
    if not m:
        return None
    value = m.group(1).replace(",", ".")
    try:
        if not 3 <= float(value) <= 60:  # tool batteries, not "230V"
            return None
    except ValueError:
        return None
    v = value.replace(".", " ")
    return (f"{v}v", f"{v} volt", f"{v} v")


def brand_in(title: str, before: int | None = None) -> str | None:
    """The brand named in the title; with `before`, the one closest before that token position
    ("HP COMPAQ LA2306x" -> "compaq")."""
    tokens = normalize(title).split()
    found: list[tuple[int, str]] = []
    for i, tok in enumerate(tokens):
        if tok in _BRANDS:
            found.append((i, tok))
    text = " ".join(tokens)
    for b in MULTI_BRANDS:
        m = re.search(rf"(?<![a-z0-9]){re.escape(b)}(?![a-z0-9])", text)
        if m:
            found.append((text[:m.start()].count(" "), b))
    if not found:
        return None
    if before is not None:
        earlier = [f for f in found if f[0] < before]
        if earlier:
            return max(earlier)[1]
    return min(found)[1]


def _cpu_skip(tokens: list[str]) -> set[int]:
    """Positions of processor/graphics specs: "i5 8250u", "ryzen 3 7320u", "gtx 1650"."""
    out, skip = set(), 0
    for i, tok in enumerate(tokens):
        if tok in _CPU_WORDS:
            out.add(i)
            skip = 3
        elif skip and (tok.isdigit() or _mixed(tok)):
            out.add(i)
            skip -= 1
        else:
            skip = 0
    return out


def model_code(title: str) -> tuple[str, int] | None:
    """(model as written, token position), e.g. ("jr 3030t", 1) for "Makita JR 3030T reciprozaag"."""
    tokens = normalize(_INCH_RE.sub(lambda m: f" {m.group(1)}inch ", title or "")).split()  # 16” is a size
    cpu = _cpu_skip(tokens)
    for i, tok in enumerate(tokens):
        if i in cpu or tok in _BRANDS or tok in NOISE or is_spec(tok):
            continue
        prev = tokens[i - 1] if i else ""
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        short_prefix = (prev.isalpha() and 2 <= len(prev) <= 3 and prev not in NOISE and prev not in _BRANDS
                        and prev not in _CPU_WORDS and prev not in _CATEGORY_OF)
        if _mixed(tok) and len(tok) >= 3:
            if short_prefix and tok[0].isdigit():  # "JR 3030T"
                return f"{prev} {tok}", i - 1
            return tok, i
        if tok.isdigit() and len(tok) >= 2 and short_prefix and nxt not in _UNIT_WORDS:
            return f"{prev} {tok}", i - 1  # "DX 460", "PUA 25"
    return None


def distinctive(code: str) -> bool:
    """Long enough to identify the product without the brand ("s27c366eau" yes, "v277" no)."""
    squashed = code.replace(" ", "")
    return len(squashed) >= 5 and sum(c.isdigit() for c in squashed) >= 3 and any(c.isalpha() for c in squashed)


_UNIT_WORDS = {"inch", "cm", "mm", "m", "meter", "kg", "gr", "g", "gb", "tb", "mb", "v", "w", "watt", "x", "stuks",
               "st", "stk", "liter", "ltr", "l", "ml", "hz", "bar", "ah", "mah", "pk", "jaar", "dagen", "uur"}

_NUM_WORDS = {"twee": 2, "drie": 3, "vier": 4, "vijf": 5, "zes": 6, "zeven": 7, "acht": 8, "negen": 9, "tien": 10}


def quantity(title: str) -> int:
    """How many items the lot title says it holds: "40x Colbert" -> 40, "2 x Kunstplant" -> 2,
    "Twee beeldschermen" -> 2, "Makita koffers - 2 stuks" -> 2, "Colberts (40x)" or "Colberts x40" -> 40.
    Sizes such as "Tafel 180 x 90 cm" don't count. 1 when the title doesn't say."""
    t = normalize(title)
    m = re.match(r"(?:ca |circa )?(\d{1,4}) ?x\b", t) or re.match(r"(?:ca |circa )?(\d{1,4}) (?:stuks|st|stk)\b", t)
    if m:
        return max(1, int(m.group(1)))
    first = t.split(" ", 1)[0] if t else ""
    if first in _NUM_WORDS:
        return _NUM_WORDS[first]
    raw = title or ""
    m = (re.search(r"\((?:ca\.?\s*)?(\d{1,4})\s*(?:x|stuks|st\.?|stk)\s*\)", raw, re.I)  # "(40x)", "(40 stuks)"
         or re.search(r"(?:^|\s)x(\d{1,4})\s*$", raw, re.I)  # "Colberts x40"
         or re.search(r"(?:^|\s)(\d{1,4})x\s*$", raw, re.I)  # "Colberts 40x"
         or re.search(r"\b(\d{1,4}) ?(?:stuks|stk)\b", t))
    if m:
        return max(1, int(m.group(1)))
    return 1


def category(word: str) -> tuple[str, ...] | None:
    return _CATEGORY_OF.get(normalize(word))


def useful_tokens(title: str) -> list[tuple[int, str]]:
    """Distinctive words of a lot title with their position (for general searches)."""
    raw = normalize(title).split()
    out, seen = [], set()
    for i, tok in enumerate(raw):
        if tok in NOISE or tok in seen or re.fullmatch(r"\d+x|x\d+", tok):  # "3x" = quantity
            continue
        if tok.isdigit():
            prev = raw[i - 1] if i else ""
            nxt = raw[i + 1] if i + 1 < len(raw) else ""
            # keep "iphone 13" / "ts 55", drop quantities and sizes like "2 x", "60 cm", "15,3"
            if nxt in NOISE or (len(tok) < 3 and not (prev.isalpha() and prev not in NOISE)):
                continue
        elif len(tok) < 2:
            continue
        seen.add(tok)
        out.append((i, tok))
    return out


# ---------------------------------------------------------------- the plan


def matched_keyword(item: WatchItem, title: str) -> str:
    text = normalize(title)
    return next((k for k in item.keywords if phrase_in(k, text)), item.keywords[0])


def plan_for(item: WatchItem, lot: Lot, extra_words: int = 3) -> SearchPlan | None:
    """How to look this lot up on Marktplaats. None when the title gives nothing to go on."""
    if item.marktplaats_query:
        q = normalize(item.marktplaats_query)
        rule = Rule([(w,) for w in q.split()], label=q)
        return SearchPlan("custom", [q], [rule], note="your own Marktplaats search (watchlist)")

    plan = _plan_for(item, lot, extra_words)
    if plan and plan.kind != "custom" and is_battery_lot(lot.title):
        for rule in plan.rules:
            rule.without_parts = tuple(dict.fromkeys(rule.without_parts + TOOL_PARTS))
    return plan


def _plan_for(item: WatchItem, lot: Lot, extra_words: int = 3) -> SearchPlan | None:
    mac = mac_plan(lot.title) or ipad_plan(lot.title) or generation_plan(lot.title, lot.description)
    if mac:
        return mac
    keyword = normalize(matched_keyword(item, lot.title))
    generic = category(keyword) is not None
    found = model_code(lot.title)
    source, from_description = lot.title, False
    if not found and lot.description:
        # "2 x Dell 24 inch monitor" + description "... monitor type U2419 HC": use the type in the description,
        # if it clearly belongs to this lot (a long type number, or the title's brand is named with it)
        desc = lot.description[:200]
        in_desc = model_code(desc)
        title_brand = brand_in(lot.title)
        if in_desc and (distinctive(in_desc[0]) or (title_brand and title_brand in normalize(desc).split())):
            found, source, from_description = in_desc, desc, True
    if found:
        written, pos = found
        brand = None if generic else keyword
        parts = written.split()
        if brand and len(parts) == 2 and parts[0] in brand.split():
            written = parts[1]  # keyword "surface pro" + "Pro 1796" -> model "1796"
        squashed = written.replace(" ", "")
        brand = brand or brand_in(source, before=pos) or brand_in(lot.title)
        groups: list[tuple[str, ...]] = []
        prefix = brand
        if brand and not distinctive(written):
            groups.append((brand,))
        elif not brand and not distinctive(written) and generic:
            groups.append(category(keyword))  # "Beeldscherm V277": at least a monitor called V277
            prefix = category(keyword)[0]
        search = " ".join(x for x in (prefix, written) if x)
        searches = [search]
        if " " in written:
            searches.append(" ".join(x for x in (prefix, squashed) if x))  # "jr 3030t" -> "jr3030t"
        elif brand and distinctive(written):
            searches.append(squashed)  # also listings that leave out the brand
        label = " ".join(x for x in (brand, written) if x)
        return SearchPlan("exact", searches, [Rule(groups, model=squashed, label=label)],
                          model=written.upper(), brand=brand,
                          note=(f"exact model {(brand or '').title()} {written.upper()}".replace("  ", " ").strip()
                                + (" (type number from the lot description)" if from_description else "")))

    # general: no type number in the title, so compare with similar things (a rough price)
    if generic:
        brand = brand_in(lot.title)
        fixed: list[tuple[str, ...]] = [category(keyword)] + ([(brand,)] if brand else [])
        lowest = 0 if brand else 1  # "monitor lenovo" is fine, "monitor" alone is not
    else:
        brand = None
        fixed = [(keyword,)]
        # a brand or product line alone ("hilti", "ipad") is not a product, unless it names a model ("ps5")
        lowest = 0 if any(ch.isdigit() for ch in keyword) else 1
    raw = normalize(lot.title).split()
    kw_tokens = keyword.split()
    pos = next((i for i, t in enumerate(raw) if kw_tokens and t.startswith(kw_tokens[0])), -1)
    used = set(kw_tokens) | set((brand or "").split())
    cpu = _cpu_skip(raw)
    tokens = []
    for i, t in useful_tokens(lot.title):
        year = re.fullmatch(r"(?:19|20)\d{2}", t) is not None  # "macbook pro 2020" is useful here
        if i in cpu or t in used or (is_spec(t) and not year) or (t in _BRANDS and generic):
            continue
        if category(t) and (category(t) == category(keyword) or (generic and t in _BUNDLE_WORDS)):
            continue  # "desktop" in a monitor lot says nothing about the monitor
        tokens.append((i, t))
    after = [t for i, t in tokens if i > pos]
    before = [t for i, t in tokens if i <= pos]
    # The first word of a title is often the product type ("Bouwradio MAKITA zonder accu"): keep it longest.
    first = tokens[0] if tokens and tokens[0][0] < pos and tokens[0][1].isalpha() and len(tokens[0][1]) >= 5 else None
    ordered = ([first[1]] if first else []) + [t for t in after + before if not first or t != first[1]]
    extras = ordered[:extra_words]
    volt = voltage(lot.title)  # "Accu Makita 12V": a 12 V battery, not an 18 V one
    rules, labels = [], []
    for n in range(len(extras), lowest - 1, -1):
        words = extras[:n]
        groups = fixed + [category(w) or (w,) for w in words] + ([volt] if volt else [])
        label = " ".join([fixed[0][0]] + ([brand] if brand else []) + words + ([volt[0]] if volt else []))
        rules.append(Rule(groups, label=label))
        labels.append(label)
    if not rules:
        return None
    searches = [labels[0]] + ([labels[-1]] if len(labels) > 1 else [])
    return SearchPlan("general", searches, rules, brand=brand,
                      note="no type number in the lot title, so this is a rough price for similar items")
