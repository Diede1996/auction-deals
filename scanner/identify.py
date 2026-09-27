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
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Lot, WatchItem
from .util import normalize, phrase_in

# Words that say nothing about which product it is.
NOISE = set("""
met zonder en de het een van voor in op tot aan bij of als uit incl inclusief excl exclusief
with and the for of to from new nieuw nieuwe gebruikt used zgan zga ongebruikt nieuwstaat
partij restpartij voorraad diverse div divers kavel lot set stuks stuk st stk pcs x cm mm m kg gr g l ltr liter
v w watt gb tb inch type model merk bj bwjr bouwjaar ca circa oa etc zie foto fotos afbeelding afbeeldingen
maat size nr no art artikel serie series
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
  | \d+x\d+(?:x\d+)?
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

    def matches(self, title_norm: str) -> bool:
        if self.model and not model_in(self.model, title_norm):
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
    tokens = normalize(title).split()
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

    keyword = normalize(matched_keyword(item, lot.title))
    generic = category(keyword) is not None
    found = model_code(lot.title)
    if found:
        written, pos = found
        brand = None if generic else keyword
        parts = written.split()
        if brand and len(parts) == 2 and parts[0] in brand.split():
            written = parts[1]  # keyword "surface pro" + "Pro 1796" -> model "1796"
        squashed = written.replace(" ", "")
        brand = brand or brand_in(lot.title, before=pos)
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
                          note=f"exact model {(brand or '').title()} {written.upper()}".replace("  ", " ").strip())

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
    rules, labels = [], []
    for n in range(len(extras), lowest - 1, -1):
        words = extras[:n]
        groups = fixed + [category(w) or (w,) for w in words]
        label = " ".join([fixed[0][0]] + ([brand] if brand else []) + words)
        rules.append(Rule(groups, label=label))
        labels.append(label)
    if not rules:
        return None
    searches = [labels[0]] + ([labels[-1]] if len(labels) > 1 else [])
    return SearchPlan("general", searches, rules, brand=brand,
                      note="no type number in the lot title, so this is a rough price for similar items")
