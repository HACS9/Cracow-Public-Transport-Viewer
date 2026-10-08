"""C2a: ekspozycja look-through — pozycje bezpośrednie + składniki ETF (wartość pozycji ETF × waga składnika).

Gotówka poza ekspozycją (jak struktura „bez gotówki”). Część ETF bez znanego składu (pokrycie < 100%) → „reszta /
nieznane”, żeby suma = wartość pozycji. Składy ręczne ETF z GPW (ETFBW20TR, ETFBM40TR): kraj Polska, waluta PLN,
sektor nieznany. VWCE może mieć skład przybliżony pełnym składem iShares MSCI ACWI (sklad.ZRODLO_ACWI).
"""
from __future__ import annotations

from . import db, sklad

NIEZNANE = "nieznane"
TOP = 15

KRAJE = {"United States": "USA", "Japan": "Japonia", "United Kingdom": "Wielka Brytania", "Canada": "Kanada",
         "France": "Francja", "Germany": "Niemcy", "Switzerland": "Szwajcaria", "China": "Chiny", "Taiwan": "Tajwan",
         "India": "Indie", "Australia": "Australia", "Netherlands": "Holandia", "Korea (South)": "Korea Płd.",
         "Korea": "Korea Płd.", "Sweden": "Szwecja", "Denmark": "Dania", "Italy": "Włochy", "Spain": "Hiszpania",
         "Brazil": "Brazylia", "Hong Kong": "Hongkong", "Ireland": "Irlandia", "Belgium": "Belgia", "Finland": "Finlandia",
         "Norway": "Norwegia", "Singapore": "Singapur", "Saudi Arabia": "Arabia Saudyjska", "South Africa": "RPA",
         "Mexico": "Meksyk", "Israel": "Izrael", "Poland": "Polska", "Austria": "Austria", "Portugal": "Portugalia",
         "Indonesia": "Indonezja", "Thailand": "Tajlandia", "Malaysia": "Malezja", "United Arab Emirates": "ZEA",
         "New Zealand": "Nowa Zelandia", "Chile": "Chile", "Turkey": "Turcja", "Philippines": "Filipiny",
         # AL3: pełne składy (ACWI / SSGA) — kraje spoza pierwszej mapy
         "Greece": "Grecja", "Hungary": "Węgry", "Czech Republic": "Czechy", "Czechia": "Czechy", "Turkiye": "Turcja",
         "Türkiye": "Turcja", "South Korea": "Korea Płd.", "Korea, Republic of": "Korea Płd.",
         "Colombia": "Kolumbia", "Peru": "Peru", "Argentina": "Argentyna", "Egypt": "Egipt", "Kuwait": "Kuwejt",
         "Qatar": "Katar", "Luxembourg": "Luksemburg", "Macau": "Makau", "Macao": "Makau", "Bermuda": "Bermudy",
         "Cayman Islands": "Kajmany", "Jersey": "Jersey", "Guernsey": "Guernsey", "Isle of Man": "Wyspa Man",
         "Curacao": "Curaçao", "Panama": "Panama", "Uruguay": "Urugwaj", "Russian Federation": "Rosja", "Russia": "Rosja",
         "Pakistan": "Pakistan", "Vietnam": "Wietnam", "Viet Nam": "Wietnam", "Monaco": "Monako", "Malta": "Malta",
         "Cyprus": "Cypr", "Iceland": "Islandia", "Kazakhstan": "Kazachstan", "Slovenia": "Słowenia", "Slovakia": "Słowacja",
         "Romania": "Rumunia", "Estonia": "Estonia", "Latvia": "Łotwa", "Lithuania": "Litwa", "Croatia": "Chorwacja",
         "Bulgaria": "Bułgaria", "Morocco": "Maroko", "Nigeria": "Nigeria", "Kenya": "Kenia", "Bahrain": "Bahrajn",
         "Oman": "Oman", "Jordan": "Jordania", "Bangladesh": "Bangladesz", "Sri Lanka": "Sri Lanka",
         "Puerto Rico": "Portoryko", "British Virgin Islands": "Brytyjskie Wyspy Dziewicze", "Liberia": "Liberia",
         "Gibraltar": "Gibraltar", "Faroe Islands": "Wyspy Owcze", "Zambia": "Zambia", "Ghana": "Ghana",
         "European Union": "Unia Europejska"}
SEKTORY = {"Information Technology": "Technologie informatyczne", "Financials": "Finanse", "Health Care": "Ochrona zdrowia",
           "Consumer Discretionary": "Dobra cykliczne", "Communication": "Komunikacja",
           "Communication Services": "Komunikacja", "Industrials": "Przemysł", "Consumer Staples": "Dobra podstawowe",
           "Energy": "Energia", "Materials": "Materiały", "Utilities": "Użyteczność publiczna",
           "Real Estate": "Nieruchomości"}
SUFIKS_KRAJ = {"WA": "Polska", "DE": "Niemcy", "PA": "Francja", "AS": "Holandia", "MI": "Włochy", "L": "Wielka Brytania",
               "ST": "Szwecja", "CO": "Dania", "SW": "Szwajcaria", "T": "Japonia", "TO": "Kanada", "HK": "Hongkong",
               "MC": "Hiszpania", "AX": "Australia", "": "USA"}


def _kraj(v):
    return KRAJE.get(v, v) if v else None


def _sektor(v):
    return SEKTORY.get(v, v) if v else None


def _waluta(v):
    v = (v or "").strip()
    return "GBP" if v in ("GBp", "GBX", "gbx") else (v.upper() or None)


def skladniki(con, ins: dict) -> dict:
    """Skład ETF z bazy (bez sieci): {'rows': [...], 'zrodlo', 'data', 'reczny', 'przyblizenie'}."""
    etf = ins["ticker"]
    provider = (ins.get("sklad_provider") or "manual").lower()
    reczny = db.wiersze(con, "SELECT * FROM sklad_etf WHERE etf=? AND reczny=1 ORDER BY waga DESC", (etf,))
    pobrany = db.wiersze(con, "SELECT * FROM sklad_etf WHERE etf=? AND reczny=0 ORDER BY waga DESC", (etf,))
    acwi = bool(sklad.url_przyblizenia(con, ins)) and any(r["zrodlo"] == sklad.ZRODLO_ACWI for r in pobrany[:1])
    # AL2: przybliżenie ACWI (VWCE) ma pierwszeństwo przed składem ręcznym; ręczny = zapas
    uzyj_recznego = not acwi and ((provider == "manual" and reczny) or not (len(pobrany) > 1 or (pobrany and not reczny)))
    rows = reczny if uzyj_recznego else pobrany
    gpw = etf.upper().endswith(".WA")
    out = []
    for r in rows:
        kraj, waluta = _kraj(r.get("kraj")), _waluta(r.get("waluta"))
        if uzyj_recznego and gpw:          # skład ręczny ETF z GPW: Polska / PLN, sektor nieznany (decyzja 07.10)
            kraj, waluta = kraj or "Polska", waluta or "PLN"
        out.append({"ticker": r["ticker"], "nazwa": r.get("nazwa") or "", "waga": float(r["waga"] or 0),
                    "kraj": kraj, "sektor": _sektor(r.get("sektor")), "waluta": waluta})
    z = rows[0]["zrodlo"] if rows else "brak"
    return {"rows": out, "zrodlo": "manual" if uzyj_recznego and rows else z, "data": rows[0]["data"] if rows else None,
            "reczny": bool(uzyj_recznego and rows), "przyblizenie": z == sklad.ZRODLO_ACWI and not uzyj_recznego}


def _dodaj(d: dict, k, v: float) -> None:
    d[k or NIEZNANE] = d.get(k or NIEZNANE, 0.0) + v


def ekspozycja(con, w: dict, zakres: str = "calosc", top: int = TOP) -> dict:
    """Ekspozycja konta (A / B / calosc) z wyceny `w` (ksiega.wycena)."""
    instr = {r["ticker"]: r for r in db.wiersze(con, "SELECT * FROM instrumenty")}
    pozycje = [p for p in w[zakres]["pozycje"] if p.get("wartosc")]
    suma = round(sum(p["wartosc"] for p in pozycje), 2)
    spolki: dict[str, dict] = {}
    kraje: dict = {}
    waluty: dict = {}
    sektory: dict = {}
    pokrycie: list[dict] = []
    nieznane = 0.0
    sklady: dict[str, dict] = {}
    for p in pozycje:
        ins = instr.get(p["ticker"]) or {"ticker": p["ticker"], "typ": "stock"}
        if ins.get("typ") == "etf":
            sklady[p["ticker"]] = skladniki(con, ins)
    meta: dict[str, dict] = {}                     # ticker spółki → kraj / sektor / nazwa z dowolnego składu
    for sk in sklady.values():
        for r in sk["rows"]:
            m = meta.setdefault(r["ticker"], {})
            for k in ("kraj", "sektor", "nazwa"):
                if r.get(k) and not m.get(k):
                    m[k] = r[k]
    for p in pozycje:
        v = float(p["wartosc"])
        ins = instr.get(p["ticker"]) or {"ticker": p["ticker"], "typ": "stock"}
        sym = p.get("symbol") or p["ticker"]
        if ins.get("typ") == "etf":
            sk = sklady[p["ticker"]]
            s_wag = sum(r["waga"] for r in sk["rows"])
            skala = min(1.0, 100.0 / s_wag) if s_wag > 0 else 0.0
            znane = 0.0
            for r in sk["rows"]:
                x = v * r["waga"] * skala / 100.0
                znane += x
                e = spolki.setdefault(r["ticker"], {"ticker": r["ticker"], "nazwa": r["nazwa"], "lacznie": 0.0,
                                                    "bezposrednio": 0.0, "etf": {}})
                e["lacznie"] += x
                e["etf"][sym] = e["etf"].get(sym, 0.0) + x
                _dodaj(kraje, r["kraj"], x)
                _dodaj(waluty, r["waluta"], x)
                _dodaj(sektory, r["sektor"], x)
            reszta = v - znane
            nieznane += reszta
            for d in (kraje, waluty, sektory):
                _dodaj(d, None, reszta)
            pokrycie.append({"symbol": sym, "ticker": p["ticker"], "konto": p.get("konto"), "wartosc": round(v, 2),
                             "pokrycie": round(znane / v * 100, 1) if v else 0.0, "zrodlo": sk["zrodlo"], "data": sk["data"],
                             "reczny": sk["reczny"], "przyblizenie": sk["przyblizenie"], "pozycji": len(sk["rows"]),
                             "z_krajem": round(sum(r["waga"] for r in sk["rows"] if r["kraj"]) * skala, 1)})
        else:
            m = meta.get(p["ticker"], {})
            suf = p["ticker"].rsplit(".", 1)[-1].upper() if "." in p["ticker"] else ""
            e = spolki.setdefault(p["ticker"], {"ticker": p["ticker"], "nazwa": m.get("nazwa") or p.get("nazwa") or "",
                                                "lacznie": 0.0, "bezposrednio": 0.0, "etf": {}})
            e["lacznie"] += v
            e["bezposrednio"] += v
            _dodaj(kraje, m.get("kraj") or SUFIKS_KRAJ.get(suf), v)
            _dodaj(waluty, _waluta(p.get("waluta")), v)
            _dodaj(sektory, m.get("sektor"), v)
    lista = sorted(spolki.values(), key=lambda e: -e["lacznie"])
    for e in lista:
        # AL3: składnik z kluczem ISIN / nazwą (SSGA bez tickera) — wyświetlany nazwą, ISIN w podpowiedzi
        isin = sklad.jest_isin(e["ticker"])
        e["isin"] = e["ticker"] if isin else None
        e["etykieta"] = (e["nazwa"] or e["ticker"]) if isin else e["ticker"]
        # tickery liczbowe z ACWI (2330, 7203…): nazwa + ticker Yahoo, jeśli z sufiksem (2330.TW), inaczej sama nazwa
        rdzen, _, suf = e["ticker"].partition(".")
        if not isin and rdzen.isdigit() and e["nazwa"]:
            e["etykieta"], e["isin"] = e["nazwa"], None
            e["ticker_yahoo"] = e["ticker"] if suf else None
        e["lacznie"] = round(e["lacznie"], 2)
        e["bezposrednio"] = round(e["bezposrednio"], 2)
        e["pct"] = round(e["lacznie"] / suma * 100, 2) if suma else 0.0
        # AL3: „przez ETF” bez udziałów < 1 zł (np. MIL.WA · VWCE 0)
        e["etf"] = sorted(((k, round(x, 2)) for k, x in e["etf"].items() if x >= 1.0), key=lambda t: -t[1])
    reszta_znane = lista[top:]

    def podzial(d: dict) -> list[dict]:
        out = [{"nazwa": k, "wartosc": round(x, 2), "pct": round(x / suma * 100, 2) if suma else 0.0}
               for k, x in d.items() if abs(x) >= 0.005]
        return sorted(out, key=lambda r: (r["nazwa"] == NIEZNANE, -r["wartosc"]))

    return {"zakres": zakres, "suma": suma, "top": lista[:top], "spolki_n": len(lista),
            "pozostale": {"n": len(reszta_znane), "wartosc": round(sum(e["lacznie"] for e in reszta_znane), 2)},
            "nieznane": round(nieznane, 2), "kraje": podzial(kraje), "waluty": podzial(waluty), "sektory": podzial(sektory),
            "pokrycie": sorted(pokrycie, key=lambda r: -r["wartosc"]), "wszystkie": lista}
