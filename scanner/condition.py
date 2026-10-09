"""Lots with a defect are left out entirely (Diede, 9 Oct 2026: "Als er iets van een defect staat, laat het dan
niet zien"), e.g. a Samsung Galaxy whose description says "64 GB, Scherm beschadigd".

defect() looks at the lot title and the lot's own part of the description (not the auction house's standard
text after it) for damage words in Dutch, English and French: defect, kapot, beschadigd, schade, barst,
gebroken, werkt niet, voor onderdelen, iCloud-lock, ... Not when they're negated or hypothetical ("geen schade",
"niet beschadigd", "zonder garantie op eventuele defecten") or about the box ("doos beschadigd").
Scratches and signs of use ("krassen", "gebruikssporen") and "niet getest" are not a defect.
"""
from __future__ import annotations

import re

from .util import normalize

# Standard texts auction houses put after the lot's own description: everything from here on is not about the lot.
_BOILERPLATE = re.compile(
    r"(?:voor meer informatie|algemene informatie|tijdens de veiling kunnen wij|lees a\.?u\.?b|lees aub"
    r"|dit betreft een tijdens transport|type veiling\s*:|ten gevolge van het faillissement|veilingvoorwaarden"
    r"|algemene voorwaarden|let op\s*:|opgeld|kijkdag|ophaaldag|afhaaldag)", re.I)

# normalised text (lowercase, accents stripped, punctuation -> spaces)
_DEFECT = re.compile(r"""(?<![a-z0-9])(?:
    defect(?:e|en)? | defekt | defectueu(?:x|se) | faulty
  | kapot(?:te)? | stuk(?:ken)?\ (?:gegaan|gevallen)
  | (?:is|zijn|scherm|glas|display|lcd|accu|batterij|toetsenbord|scharnier|lader|knop)\ stuk(?![a-z0-9])
  | beschadig(?:d|de|ing|ingen)? | damaged? | endommag(?:e|ee|es|ees)
  | [a-z]*schade | gebarsten | barst(?:je|jes|en)? | cracked | crack
  | gebroken | broken | [a-z]*breuk | casse(?:e|es)?
  | werk(?:t|en)\ niet | niet\ (?:meer\ )?werk(?:end|ende|t) | doet\ het\ niet | start\ niet | gaat\ niet\ aan
  | geeft\ geen\ beeld | not\ working | ne\ fonctionne\ pas | hors\ service
  | (?:voor|als|for|pour)\ (?:de\ )?(?:onderdelen|parts|pieces|spare\ parts|reparatie|repair)
  | spares\ or\ repair | te\ repareren | reparatie\ nodig | needs?\ repair
  | icloud\ (?:lock(?:ed)?|vergrendeld|slot|geblokkeerd) | activa(?:tie|tion)\ ?(?:slot|lock)
  | (?:mdm|frp|google|account)\ (?:lock(?:ed)?|vergrendeld|slot|geblokkeerd)
  | (?:pin)?code\ onbekend | wachtwoord\ onbekend | password\ unknown | locked\ to
)(?![a-z0-9])""", re.X)

# A damage word after one of these (in the same part of the sentence) isn't about the item: "geen schade",
# "niet beschadigd", "zonder krassen of schade", "vrij van schade".
_NEGATION = {"niet", "not", "nooit", "never", "pas"}  # right before: "niet beschadigd", "is niet kapot"
_NEGATION_WIDE = {"geen", "zonder", "vrij", "free", "no", "without", "sans", "aucun", "aucune"}  # "zonder enige vorm van schade"
# ... or right after one of these: "eventuele defecten", "mogelijke schade"
_HYPOTHETICAL = {"eventuele", "eventueel", "mogelijke", "mogelijk", "possible", "possibles", "verborgen"}
# "garantie op defecten", "verzekerd tegen schade", "risico op beschadiging"
_ABOUT_RISK = {"garantie", "warranty", "verzekerd", "verzekering", "risico", "kans", "bescherming", "beschermd",
               "protection", "transportrisico"}
_CHAIN = {"of", "en", "noch", "or", "and", "nor", "ou", "et"}
# "doos beschadigd", "beschadigde verpakking", "schade aan de doos": the box, not the item
_PACKAGING = {"doos", "dozen", "omdoos", "verpakking", "verpakkingen", "karton", "box", "packaging", "emballage",
              "boite", "label", "etiket", "sticker"}
_ABOUT_NEXT = re.compile(r"^(?:beschadigde|beschadiging|beschadigingen|schade|damaged|damage)$")
# commas, full stops etc. end a part of a sentence: "Verkocht zonder garantie, defect" is a defect
_CLAUSE = re.compile(r"[,.;:!?()\[\]|\n•]+")


def lot_text(description: str, limit: int = 400) -> str:
    """The lot's own part of a description: up to the auction house's standard text, at most `limit` characters."""
    text = re.sub(r"\s+", " ", description or "").strip()
    m = _BOILERPLATE.search(text)
    return (text[:m.start()] if m else text)[:limit]


def _negated(before: list[str]) -> bool:
    if any(w in _NEGATION for w in before[-2:]) or any(w in _NEGATION_WIDE for w in before[-4:]):
        return True
    if before and before[-1] in _HYPOTHETICAL:
        return True
    if len(before) >= 2 and before[-1] in ("op", "tegen", "van", "against", "on") and before[-2] in _ABOUT_RISK:
        return True
    if before and before[-1] in _CHAIN:  # "zonder krassen of schade"
        return any(w in _NEGATION or w in _NEGATION_WIDE for w in before[-5:-1])
    return False


def _found_in(clause: str) -> str | None:
    text = normalize(clause)
    for m in _DEFECT.finditer(text):
        found = m.group(0)
        before = text[:m.start()].split()
        after = text[m.end():].split()[:3]
        if _negated(before):
            continue
        if any(w in _PACKAGING for w in before[-2:]):
            continue  # "doos beschadigd", "verpakking is beschadigd"
        if _ABOUT_NEXT.match(found) and any(w in _PACKAGING for w in after):
            continue  # "beschadigde verpakking", "schade aan de doos"
        return found
    return None


def defect(title: str, description: str = "") -> str | None:
    """The damage word when the title or the lot's description says it has a defect, else None."""
    for text in (title or "", lot_text(description)):
        for clause in _CLAUSE.split(text):
            found = _found_in(clause)
            if found:
                return found
    return None


def defect_filter(config: dict) -> bool:
    """hide_defects in config.yml (default on)."""
    return (config.get("condition") or {}).get("hide_defects", True) is not False
