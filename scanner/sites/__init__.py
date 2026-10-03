from . import hnvi, onlineveilingmeester, openbareverkopen, plaatsjebod, proveiling, troostwijk

SITES = {
    "troostwijk": troostwijk.fetch_lots,
    "proveiling": proveiling.fetch_lots,
    "hnvi": hnvi.fetch_lots,
    "plaatsjebod": plaatsjebod.fetch_lots,
    "onlineveilingmeester": onlineveilingmeester.fetch_lots,
    "openbareverkopen": openbareverkopen.fetch_lots,
}

SITE_NAMES = {
    "troostwijk": "Troostwijk",
    "proveiling": "ProVeiling",
    "hnvi": "HNVI",
    "plaatsjebod": "Plaats Je Bod",
    "onlineveilingmeester": "Onlineveilingmeester",
    "openbareverkopen": "Openbare Verkopen (BE)",
    "marktplaats": "Marktplaats prices",
}
