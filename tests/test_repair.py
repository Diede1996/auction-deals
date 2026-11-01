from scanner.condition import repair_brand, repair_settings


def test_repair_settings_off_without_brands():
    assert repair_settings({}) is None
    assert repair_settings({"condition": {"repair": {"enabled": False, "brands": ["makita"]}}}) is None


def test_repair_brand_in_title():
    r = repair_settings({"condition": {"repair": {"brands": ["Makita", "La Marzocco", "Kärcher"], "margin": 1.0}}})
    assert r["margin"] == 1.0
    assert repair_brand("Makita DHP485 accuboormachine defect", r["brands"]) == "makita"
    assert repair_brand("La Marzocco Linea Mini voor onderdelen", r["brands"]) == "la marzocco"
    assert repair_brand("Karcher K5 hogedrukreiniger werkt niet", r["brands"]) == "karcher"
    assert repair_brand("Lenovo laptop defect", r["brands"]) is None
