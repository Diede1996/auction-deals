"""Lots with a defect are left out entirely (Diede, 9 Oct 2026: "Als er iets van een defect staat, laat het dan
niet zien"), e.g. a Samsung Galaxy whose description says "64 GB, Scherm beschadigd".

defect() reads the lot's condition field (ProVeiling's "Conditie: Defect"), title and its own part of the
description (not the auction house's standard text after it) for damage wording in Dutch, Flemish, English and
French: defect, kapot, beschadigd, schade, barst, gebroken, werkt niet, gaat niet aan, voor onderdelen, iCloud
locked, écran cassé, does not work, ... It is not a defect when the words are:
- negated: "geen schade", "niet beschadigd", "zonder krassen, deuken of schade", "Schade: geen";
- general: "eventuele defecten", "verborgen gebreken", "niet aansprakelijk voor schade";
- about the box: "doos beschadigd", "kapotte doos", "verpakkingsschade";
- light wear on the casing: "lichte schade aan de behuizing" (but "kleine barst in het scherm" is a defect);
- something else: "gebroken wit" (a colour), "stuk voor stuk getest", "iCloud lock: nee", "Activatieslot uit".
Scratches ("krassen"), signs of use and "niet getest" are not a defect.
"""
from __future__ import annotations

import re

from .util import normalize

# Standard texts auction houses put after the lot's own description: everything from here on is not about the lot.
_BOILERPLATE = re.compile(
    r"(?:voor meer informatie|algemene informatie|tijdens de veiling kunnen wij|lees a\.?u\.?b|lees aub"
    r"|dit betreft een tijdens transport|type veiling\s*:|ten gevolge van het faillissement|veilingvoorwaarden"
    r"|algemene voorwaarden|opgeld|kijkdag|ophaaldag|afhaaldag)", re.I)
# ProVeiling's "Conditie: Defect" can come after that standard text, or after the 300 characters that are kept
_CONDITION_FIELD = re.compile(r"(?:conditie|staat|toestand|condition|etat|état)\s*:\s*([^.,;:\n|]{1,40})", re.I)

# normalised text (lowercase, accents stripped, punctuation -> spaces)
_DEFECT = re.compile(r"""(?<![a-z0-9])(?:
    [a-z]*defect(?:e|en)? | defekt | defectueu(?:x|se)s? | faulty | mankement(?:en)? | gebreken
  | kapot(?:te)? | stuk\ (?:gegaan|gevallen)
  | (?:is|zijn|scherm|glas|display|lcd|accu|batterij|toetsenbord|scharnier|lader|knop)\ stuk(?!\ voor\ stuk)
  | beschadig(?:d|de|t|ing|ingen)? | damaged? | endommag(?:e|ee|es|ees)
  | [a-z]*schade | gebarsten | gebarst | barst(?:je|jes|en)? | (?:scherm|glas|display|ruit|lcd)\ gesprongen
  | gescheurd | scheur(?:tje|en)? | cracked | crack | smashed | shattered | fissure(?:e|es)? | brise(?:e|es)?
  | gebroken(?!\ (?:wit|white|grijs)) | broken | [a-z]*breuk | casse(?:e|es)? | abime(?:e|es)? | fele(?:e)?
  | werk(?:t|en)\ niet | functioneert\ niet
  | niet\ (?:meer\ |volledig\ |goed\ |naar\ behoren\ )?(?:werkend|werkende|werkt|functionerend|functionerende|functioneert)
  | (?:deels|gedeeltelijk|half)\ werkend | werkt\ (?:maar\ )?half | doet\ het\ niet | doet\ (?:niets|niks)
  | start\ niet | gaat\ niet\ (?:meer\ )?aan | laa?dt?\ niet\ (?:meer\ )?op | reageert\ niet
  | (?:geeft\ )?geen\ beeld | zwart\ beeld | dode\ pixels? | pixelfout(?:en)? | (?:strepen|lijnen)\ in\ (?:het\ )?(?:beeld|scherm)
  | lekkage | storing | foutmelding | foutcode | (?:bolle|opgezwollen)\ (?:accu|batterij) | (?:accu|batterij)\ (?:bol|opgezwollen)
  | not\ working | not\ functioning | does\ not\ work | doesn\ t\ work | won\ t\ (?:turn\ on|power\ on|charge|boot|start)
  | doesn\ t\ (?:turn\ on|power\ on|charge|boot)
  | ne\ fonctionne\ (?:pas|plus) | ne\ (?:s\ )?allume\ (?:pas|plus) | ne\ marche\ (?:pas|plus) | ne\ charge\ (?:pas|plus)
  | en\ panne | hors\ service | degats?
  | (?:voor|als|for|pour)\ (?:de\ )?(?:onderdelen|parts|pieces|spare\ parts|reparatie|repair)(?!\ (?:van|of|de|du|des))
  | spares\ or\ repair | for\ spares | parts\ only | te\ repareren | a\ reparer | reparatie\ nodig | needs?\ repair
  | icloud\ (?:lock(?:ed)?|vergrendeld|slot|geblokkeerd|bloque(?:e)?|verrouille(?:e)?) | bloque(?:e)?\ icloud
  | activa(?:tie|tion)\ ?(?:slot|lock)
  | (?:mdm|frp|google|account|bios)\ (?:lock(?:ed)?|vergrendeld|slot|geblokkeerd|wachtwoord|password)
  | zoek\ mijn\ (?:iphone|ipad|mac)(?:\ staat)?\ aan | find\ my\ (?:iphone|ipad|mac)(?:\ is)?\ on
  | (?:pin)?code\ onbekend | wachtwoord\ onbekend | password\ unknown | locked\ to | verrouille(?:e)?
)(?![a-z0-9])""", re.X)

# right before the damage word: "niet beschadigd", "is niet kapot", "pas endommagé"
_NEGATION = {"niet", "not", "nooit", "never", "pas", "nicht"}
# a little further back, over fillers, scratches and list words: "geen zichtbare schade", "zonder enige vorm van
# schade", "zonder krassen, deuken of schade", "geen accu, lader of schade", "vrij van krassen en schade"
_NEGATION_WIDE = {"geen", "zonder", "vrij", "free", "no", "without", "sans", "aucun", "aucune", "ni", "noch"}
_FILLER = {"enige", "enkele", "zichtbare", "zichtbaar", "verdere", "andere", "vorm", "van", "de", "het", "een", "any",
           "visible", "other", "kind", "of", "noemenswaardige", "grote", "ernstige", "duidelijke", "echte", "soort",
           "sort", "trace", "traces", "d", "la", "le", "les"}
_COSMETIC = {"krassen", "kras", "krasjes", "krasje", "deuken", "deuk", "deukjes", "deukje", "gebruikssporen",
             "slijtage", "vlekken", "vlek", "scratches", "scratch", "dents", "dent", "rayures", "rayure"}
_CHAIN_OR = {"of", "noch", "or", "nor", "ou", "ni"}
_CHAIN_AND = {"en", "and", "et"}
# right before a plural or general damage word: "eventuele defecten", "mogelijke schade", "verborgen gebreken"
# (not "mogelijk defect": that's about this lot)
_GENERAL = {"eventuele", "mogelijke", "enige", "verborgen", "possible", "possibles", "eventuels", "eventuelles"}
# "garantie op defecten", "niet aansprakelijk voor schade", "risico op beschadiging", "verzekerd tegen schade"
_ABOUT_RISK = {"garantie", "warranty", "verzekerd", "verzekering", "risico", "kans", "bescherming", "beschermd",
               "protection", "transportrisico", "aansprakelijk", "aansprakelijkheid", "liability", "liable",
               "responsible", "verantwoordelijk", "responsable", "responsabilite", "vergoeding", "claim", "claims"}
_RISK_LINK = {"op", "voor", "tegen", "on", "for", "against", "pour", "contre", "van"}
# "doos beschadigd", "beschadigde verpakking", "schade aan de doos": the box, not the item
_PACKAGING = re.compile(r"^(?:[a-z]*doos|dozen|[a-z]*verpakking(?:en)?|karton|carton|box|packaging|emballage|boite"
                        r"|label|etiket|sticker|seal|zegel)$")
_SOFT = {"is", "zijn", "was", "licht", "lichtjes", "iets", "wat", "enigszins", "beetje", "een", "de", "het", "erg",
         "zwaar", "flink", "aan", "op", "van", "the", "of", "to", "la", "le", "du", "est"}
# light wear on the casing is not a defect: "lichte schade aan de behuizing", "kleine beschadiging op de hoek"
_LIGHT = {"licht", "lichte", "lichtjes", "kleine", "klein", "minimale", "minimaal", "oppervlakkige", "cosmetische",
          "cosmetisch", "minieme", "slight", "minor", "small", "light", "legere", "legeres", "petite", "petites"}
_LIGHT_OK = re.compile(r"^(?:schade|beschadig(?:d|de|t|ing|ingen)?|damage|damaged|endommag[a-z]*|deuk[a-z]*)$")
_SCREEN = {"scherm", "display", "glas", "lcd", "beeld", "touchscreen", "screen", "ecran", "lens", "camera"}
# "Schade: geen", "iCloud lock: nee", "Activatieslot uit", "iCloud vrij"
_NO = {"nee", "neen", "geen", "no", "none", "nvt", "uit", "off", "nein", "non", "aucun", "aucune", "0", "n"}
_OFF = {"uit", "off", "uitgeschakeld", "verwijderd", "removed", "disabled", "vrij", "free", "nee", "no", "gereset",
        "ontgrendeld", "unlocked", "desactive"}
_LOCK = re.compile(r"^(?:icloud|activa|mdm|frp|google|account|bios|zoek|find|verrouille|bloque)")
_WORKING_KEY = re.compile(r"(?:werkend|werkt|werking|functioneel|functioneert|working|works|fonctionne|fonctionnel)$")
# a part of a sentence ends at these; a dash or slash between spaces too ("Niet getest / defect")
_SENTENCE = re.compile(r"[.;!?()\[\]|\n•]+|\s[-–—/]+\s|[–—]")


def lot_text(description: str, limit: int = 400) -> str:
    """The lot's own part of a description: up to the auction house's standard text, at most `limit` characters."""
    text = re.sub(r"[ \t\r\f\v]+", " ", description or "").strip()
    m = _BOILERPLATE.search(text)
    return (text[:m.start()] if m else text)[:limit]


def _negated(before: list[str]) -> bool:
    if before and before[-1] in _NEGATION:
        return True
    if len(before) >= 2 and before[-2] in _NEGATION and before[-1] in ("meer", "erg", "echt", "zo", "heel",
                                                                       "zichtbaar", "ernstig", "really", "very"):
        return True
    saw_or = False
    for steps, w in enumerate(reversed(before), 1):  # back over fillers, scratches, lists to a "geen" / "zonder"
        if w in _NEGATION_WIDE:
            return True
        if steps > 8:
            return False
        if w in _CHAIN_OR:
            saw_or = True
        elif w in _CHAIN_AND or w == "," or w in _FILLER or w in _COSMETIC or _DEFECT.fullmatch(w):
            continue
        elif not saw_or:  # "geen accu, lader of schade" is a list under the "geen"; "zonder lader scherm kapot" isn't
            return False
    return False


def _about_packaging(found: str, before: list[str], after: list[str]) -> bool:
    if re.match(r"^(?:[a-z]*verpakking|[a-z]*doos|karton|emballage)s?[a-z]*schade$", found):
        return True  # "verpakkingsschade", "doosschade"
    for back, w in enumerate(reversed(before[-4:]), 1):  # "de doos is licht beschadigd", "carton endommagé"
        if w == ",":
            break
        if _PACKAGING.match(w):
            i = len(before) - back
            return not (i > 0 and before[i - 1] in _NEGATION_WIDE)  # "zonder doos, scherm beschadigd" is the screen
        if w not in _SOFT:
            break
    if re.match(r"^(?:beschadig|schade|[a-z]*schade|kapot|damage|endommag|casse|abime)", found):
        for w in after[:4]:  # "beschadigde verpakking", "kapotte doos", "schade aan de buitenverpakking"
            if w == ",":
                break
            if _PACKAGING.match(w):
                return True
            if w not in _SOFT:
                break
    return False


def _is_light(found: str, before: list[str], part: list[str]) -> bool:
    near = [w for w in before[-2:] if w not in ("zeer", "heel", "erg", "very")]
    return bool(near) and near[-1] in _LIGHT and bool(_LIGHT_OK.match(found)) and not any(w in _SCREEN for w in part)


def _about_risk(before: list[str]) -> bool:
    window = before[-5:]
    return any(w in _ABOUT_RISK for w in window) and any(w in _RISK_LINK for w in window)


def _words(text: str) -> list[str]:
    """Normalised words, with "," kept as a word: "Geen krassen, deuken of schade"."""
    return " , ".join(normalize(p) for p in (text or "").split(",")).split()


def _found_in(text: str, value: str | None = None, key: str | None = None) -> str | None:
    """A damage word in this part of a sentence that's about the lot. `value` is what follows it after a colon
    ("Schade: geen"), `key` what came before it ("Verpakking: beschadigd")."""
    words = _words(text)
    joined = " ".join(words)
    key_words = [w for w in _words(key) if w != ","] if key else []
    value_words = [w for w in _words(value) if w != ","] if value is not None else []
    for m in _DEFECT.finditer(joined):
        found = m.group(0)
        before = joined[:m.start()].split()
        after = joined[m.end():].split()
        start = len(before) - before[::-1].index(",") if "," in before else 0
        part = before[start:] + found.split() + (after[:after.index(",")] if "," in after else after)
        if _negated(before) or (before and before[-1] in _GENERAL) or _about_risk(before):
            continue
        if _about_packaging(found, before, after) or (not before and key_words and _PACKAGING.match(key_words[-1])):
            continue  # "doos beschadigd", "Verpakking: beschadigd"
        if _is_light(found, before, part):
            continue
        if not after and value_words and value_words[0] in _NO:
            continue  # "Schade: geen", "Defect: Nee", "iCloud lock: nee"
        if _LOCK.match(found) and (any(w in _OFF for w in (after[:2] if after else value_words[:2]))
                                   or after[:2] == ["niet", "actief"]):
            continue  # "Activatieslot uit", "iCloud lock verwijderd", "iCloud vrij"
        return found
    if value_words and value_words[0] in _NO and _WORKING_KEY.search(" ".join(w for w in words if w != ",")):
        return "werkt niet"  # "Werkend: Nee"
    return None


def _scan(text: str) -> str | None:
    for sentence in _SENTENCE.split(text or ""):
        pieces = sentence.split(":")
        for i, piece in enumerate(pieces):
            found = _found_in(piece, value=pieces[i + 1] if i + 1 < len(pieces) else None,
                              key=pieces[i - 1] if i > 0 else None)
            if found:
                return found
    return None


def _condition_defect(value: str) -> str | None:
    """"Defect", "Voor onderdelen", "Beschadigd" in a condition field; not "Geen schade" or "Niet getest"."""
    v = normalize(value)
    m = _DEFECT.search(v)
    return m.group(0) if m and not _negated(v[:m.start()].split()) else None


def defect(title: str, description: str = "", condition: str = "") -> str | None:
    """The damage word when the lot's condition field, title or own description says it has a defect, else None."""
    for value in [condition] + [m.group(1) for m in _CONDITION_FIELD.finditer(description or "")]:
        found = value and _condition_defect(value)
        if found:
            return found  # "Conditie: Defect", "Staat: voor onderdelen"
    return _scan(title) or _scan(lot_text(description))


def defect_filter(config: dict) -> bool:
    """hide_defects in config.yml (default on)."""
    return (config.get("condition") or {}).get("hide_defects", True) is not False
