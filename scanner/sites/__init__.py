from . import (bellauction, hnvi, inventarisveilingen, nedveiling, onlineveilingmeester, openbareverkopen,
               plaatsjebod, proveiling, troostwijk, veilingwinnaar, vlavem)

SITES = {
    "troostwijk": troostwijk.fetch_lots,
    "proveiling": proveiling.fetch_lots,
    "hnvi": hnvi.fetch_lots,
    "plaatsjebod": plaatsjebod.fetch_lots,
    "onlineveilingmeester": onlineveilingmeester.fetch_lots,
    "veilingwinnaar": veilingwinnaar.fetch_lots,
    "inventarisveilingen": inventarisveilingen.fetch_lots,
    "nedveiling": nedveiling.fetch_lots,
    "openbareverkopen": openbareverkopen.fetch_lots,
    "vlavem": vlavem.fetch_lots,
    "bellauction": bellauction.fetch_lots,
}

SITE_NAMES = {
    "troostwijk": "Troostwijk",
    "proveiling": "ProVeiling",
    "hnvi": "HNVI",
    "plaatsjebod": "Plaats Je Bod",
    "onlineveilingmeester": "Onlineveilingmeester",
    "veilingwinnaar": "Veilingwinnaar",
    "inventarisveilingen": "Inventarisveilingen",
    "nedveiling": "Nedveiling",
    "openbareverkopen": "Openbare Verkopen (BE)",
    "vlavem": "Vlavem (BE)",
    "bellauction": "BellAuction (BE)",
    "marktplaats": "Marktplaats prices",
}
