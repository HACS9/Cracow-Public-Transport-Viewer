"""Skład ETF (do śledzenia spółek wewnątrz funduszy): pobieranie od dostawcy + cache w `sklad_etf`.

Kolejność jak w trackerze: provider=manual ze składem ręcznym → tylko ręczny; dane pobrane < 7 dni → z bazy;
inaczej iShares (CSV) / SSGA (XLSX) → yfinance (źródło danych) → ręczny.
Od C2a zapisywany jest pełny skład (look-through, kraj/sektor/waluta); sklad() zwraca SKLAD_MAX największych (alerty).
VWCE: przybliżenie pełnym składem iShares MSCI ACWI (ustawienie sklad_vwce_acwi).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import math
import re
import sys

from . import db
from .zrodla import pobierz

UA = {"User-Agent": "Mozilla/5.0 (compatible; wallet/1.0)"}
WAZNOSC_DNI = 7
SKLAD_MAX = 30


# AL3: giełda z pliku iShares → sufiks Yahoo (kolejność ma znaczenie: „bolsa mexicana” / „santiago” przed „bolsa” = Madryt)
_GIELDY = ((("mexic",), ".MX"), (("santiago",), ".SN"), (("warsaw", "gpw"), ".WA"), (("london",), ".L"),
           (("xetra", "deutsche", "frankfurt"), ".DE"), (("amsterdam",), ".AS"), (("paris",), ".PA"), (("tokyo",), ".T"),
           (("toronto",), ".TO"), (("swiss", "zurich"), ".SW"), (("hong kong",), ".HK"), (("australian", "asx"), ".AX"),
           (("copenhagen",), ".CO"), (("stockholm",), ".ST"), (("madrid", "bolsa"), ".MC"), (("borsa italiana", "milan"), ".MI"),
           (("taiwan stock",), ".TW"), (("gretai", "taipei exchange"), ".TWO"), (("kosdaq",), ".KQ"), (("korea",), ".KS"),
           (("shanghai",), ".SS"), (("shenzhen",), ".SZ"), (("national stock exchange of india",), ".NS"),
           (("bse ltd", "bombay"), ".BO"), (("johannesburg",), ".JO"), (("saudi",), ".SR"), (("singapore",), ".SI"),
           (("sao paulo", "bovespa", "b3 s.a"), ".SA"), (("oslo",), ".OL"), (("helsinki",), ".HE"), (("brussels",), ".BR"),
           (("lisbon",), ".LS"), (("wiener", "vienna"), ".VI"), (("irish",), ".IR"), (("tel aviv",), ".TA"),
           (("indonesia",), ".JK"), (("bursa malaysia",), ".KL"), (("thailand",), ".BK"), (("philippine",), ".PS"),
           (("istanbul",), ".IS"), (("new zealand",), ".NZ"), (("athens",), ".AT"), (("budapest",), ".BD"),
           (("prague",), ".PR"), (("qatar",), ".QA"), (("kuwait",), ".KW"))


def _norm_ticker(raw: str, exchange: str = "") -> str:
    t = (raw or "").strip().upper().replace(" ", "-")
    if not t:
        return ""
    ex = (exchange or "").lower()
    for klucze, suf in _GIELDY:
        if any(k in ex for k in klucze):
            if suf == ".L":
                t = t.rstrip(".")                          # iShares: „BP.” → BP.L
            if suf == ".HK" and t.isdigit():
                t = t.zfill(4)                             # Yahoo: 0700.HK
            return t + suf
    if ("nyse" in ex or "new york" in ex or "nasdaq" in ex) and "omx" not in ex:
        t = t.replace(".", "-").replace("/", "-")          # BRK.B → BRK-B
    return t


class SkladBlad(Exception):
    """AL2: przyczyna nieudanego pobrania składu (podgląd `worker sklad --dry-run`, log)."""


def _pobierz_url(url: str):
    import requests
    if not url:
        raise SkladBlad("brak URL (sklad_url)")
    try:
        r = requests.get(url, headers=UA, timeout=60)
    except Exception as e:  # noqa
        raise SkladBlad(f"błąd sieci: {type(e).__name__}") from None
    if r.status_code != 200:
        raise SkladBlad(f"HTTP {r.status_code}")
    return r


def fetch_ishares(url: str) -> list[dict]:
    r = _pobierz_url(url)
    typ = (r.headers.get("content-type") or "").lower() if hasattr(r, "headers") and r.headers else ""
    tekst = r.text
    if "html" in typ or tekst.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        raise SkladBlad(f"HTML zamiast CSV ({len(tekst) // 1024} KB) — nieaktualny adres iShares (stary .ajax?)")
    lines = tekst.lstrip("\ufeff").splitlines()
    start = next((i for i, l in enumerate(lines) if l.lstrip('\ufeff"').startswith("Ticker,")), None)
    if start is None:
        raise SkladBlad("brak kolumn: nie znaleziono nagłówka „Ticker,…” (pierwsza linia: " + (lines[0][:80] if lines else "—") + ")")
    out = []
    for r in csv.DictReader(lines[start:]):
        try:
            w = float(str(r.get("Weight (%)", "")).replace(",", ""))
        except ValueError:
            continue
        if (r.get("Asset Class") or "").strip().lower() not in ("equity", ""):
            continue
        tk = _norm_ticker(r.get("Ticker", ""), r.get("Exchange", ""))
        if tk:
            out.append({"ticker": tk, "weight": w, "name": (r.get("Name") or "").strip(),
                        "kraj": _pole(r.get("Location")), "sektor": _pole(r.get("Sector")),
                        "waluta": _pole(r.get("Market Currency") or r.get("Currency"))})
    if not out:
        raise SkladBlad("0 wierszy po parsowaniu (CSV iShares)")
    return out


def _pole(v) -> str | None:
    """C2a: kraj / sektor / waluta z pliku dostawcy; puste, „-”, „nan” → None („nieznane”)."""
    v = str(v or "").strip()
    return None if v in ("", "-", "—") or v.lower() in ("nan", "none", "n/a", "cash and/or derivatives") else v


_RE_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def jest_isin(t: str) -> bool:
    """AL2: składnik bez tickera Yahoo (klucz = ISIN, np. SSGA) — tylko ekspozycja, nie alerty ani tickery."""
    return bool(_RE_ISIN.match(t or ""))


def fetch_ssga(url: str) -> list[dict]:
    """XLSX SSGA. AL2: nagłówek szukany po „ISIN” + „Percent of Fund” (albo Ticker + Weight); waga w % (0.668 = 0,668%);
    kraj = Trade Country Name, sektor = Sector Classification, waluta = Currency; klucz = Ticker, a bez niego ISIN / nazwa."""
    import warnings
    import pandas as pd
    raw = _pobierz_url(url).content
    if raw[:2] != b"PK":
        raise SkladBlad(f"to nie jest plik XLSX ({len(raw) // 1024} KB, początek {raw[:15]!r})")
    df, naglowki = None, []
    for skip in range(0, 15):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")      # plik SSGA bez stylu domyślnego — ostrzeżenie openpyxl bez znaczenia
                cand = pd.read_excel(io.BytesIO(raw), skiprows=skip)
        except Exception:  # noqa
            continue
        cols = [str(c).strip().lower() for c in cand.columns]
        if skip == 0:
            naglowki = cols
        klucz = any("ticker" in c for c in cols) or "isin" in cols
        waga = any("weight" in c or "percent of fund" in c for c in cols)
        if klucz and waga:
            cand.columns = [str(c).strip() for c in cand.columns]
            df = cand
            break
    if df is None:
        raise SkladBlad("brak kolumn (ISIN/Ticker + Percent of Fund/Weight); nagłówki z 1. wiersza: " + ", ".join(naglowki[:12]))
    col = lambda *wz: next((c for c in df.columns if any(w in c.lower() for w in wz)), None)
    tcol = col("ticker")
    icol = next((c for c in df.columns if c.strip().lower() == "isin"), None)
    wcol = col("percent of fund", "weight")
    ncol = next((c for c in df.columns if "name" in c.lower() and "country" not in c.lower()), None)
    kcol = col("trade country", "country", "location")
    scol = col("sector")
    wacol = next((c for c in df.columns if c.strip().lower() in ("currency", "trade currency", "local currency")), None) or col("currency")
    out = []
    for _, r in df.iterrows():
        try:
            w = float(str(r[wcol]).replace("%", "").replace(",", ""))
        except (ValueError, TypeError):
            continue
        if not math.isfinite(w):
            continue
        tk = _norm_ticker(str(r[tcol])) if tcol else ""
        if not tk or tk in ("NAN", "-", "--"):
            tk = (str(r[icol]).strip().upper() if icol else "") or (str(r[ncol]).strip() if ncol else "")
        if not tk or tk.upper() == "NAN":
            continue
        out.append({"ticker": tk, "weight": w, "name": str(r[ncol]).strip() if ncol else "",
                    "kraj": _pole(r[kcol]) if kcol else None, "sektor": _pole(r[scol]) if scol else None,
                    "waluta": _pole(r[wacol]) if wacol else None})
    if not out:
        raise SkladBlad("0 wierszy po parsowaniu (XLSX SSGA)")
    return out


def _zapisz(con, etf: str, holdings: list[dict], zrodlo: str) -> None:
    """C2a: zapisywany PEŁNY skład (look-through); alerty i tickery dalej używają top SKLAD_MAX."""
    con.execute("DELETE FROM sklad_etf WHERE etf=? AND reczny=0", (etf,))
    dzis = dt.date.today().isoformat()
    for h in sorted(holdings, key=lambda h: -h["weight"]):
        con.execute("INSERT INTO sklad_etf(etf,ticker,waga,nazwa,zrodlo,reczny,data,kraj,sektor,waluta) "
                    "VALUES(?,?,?,?,?,0,?,?,?,?)",
                    (etf, h["ticker"], h["weight"], h.get("name"), zrodlo, dzis, h.get("kraj"), h.get("sektor"),
                     h.get("waluta")))


ZRODLO_ACWI = "ishares-acwi"


def url_przyblizenia(con, ins: dict) -> str | None:
    """C2a: VWCE → pełny skład iShares MSCI ACWI (przybliżenie), gdy ustawienie sklad_vwce_acwi = 1."""
    if (ins.get("symbol") or ins["ticker"].split(".")[0]).upper() != "VWCE":
        return None
    ust = db.ustawienia(con)
    if str(ust.get("sklad_vwce_acwi", "1")).strip() != "1":
        return None
    return (ust.get("sklad_acwi_url") or "").strip() or None


def pobierz_sklad(con, ins: dict, przyczyna: list | None = None, acwi_url: str | None = None) -> tuple[list[dict], str]:
    """Pobranie składu od dostawcy bez zapisu (także do podglądu --dry-run). AL2: przyczyna błędu dostawcy
    (brak URL / HTTP / HTML zamiast CSV / brak kolumn / 0 wierszy) → `przyczyna`, potem zapas yfinance."""
    etf = ins["ticker"]
    provider = (ins.get("sklad_provider") or "manual").lower()
    holdings, uzyte = [], ""
    acwi = url_przyblizenia(con, ins)
    if acwi and acwi_url:
        acwi = acwi_url                        # podgląd: adres po migracji (bez zapisu ustawienia)
    glowne = ZRODLO_ACWI if acwi else (provider if provider in ("ishares", "ssga") else "")
    try:
        if acwi:
            holdings, uzyte = fetch_ishares(acwi), ZRODLO_ACWI
        elif provider == "ishares":
            holdings, uzyte = fetch_ishares(ins.get("sklad_url") or ""), "ishares"
        elif provider == "ssga":
            holdings, uzyte = fetch_ssga(ins.get("sklad_url") or ""), "ssga"
    except Exception as e:  # noqa
        opis = str(e) if isinstance(e, SkladBlad) else f"{type(e).__name__}: {e}"
        print(f"[{etf}] skład ({glowne}): {opis}", file=sys.stderr)
        if przyczyna is not None:
            przyczyna.append(f"{glowne}: {opis}")
        holdings = []
    if not holdings:
        try:
            holdings = pobierz((ins.get("zrodlo_danych") or db.ustawienia(con).get("zrodlo_danych") or "yahoo")).sklad(etf, 0.0)
            uzyte = "yfinance"
        except Exception as e:  # noqa
            print(f"[{etf}] błąd składu ze źródła danych: {e}", file=sys.stderr)
    return holdings, uzyte


def sklad(con, ins: dict, odswiez: bool = True, wymus: bool = False) -> tuple[list[dict], str]:
    """Skład funduszu [{ticker, weight, name}] malejąco po wadze + źródło."""
    etf = ins["ticker"]
    provider = (ins.get("sklad_provider") or "manual").lower()
    reczny = [{"ticker": r["ticker"], "weight": r["waga"], "name": r["nazwa"] or ""} for r in
              db.wiersze(con, "SELECT * FROM sklad_etf WHERE etf=? AND reczny=1 ORDER BY waga DESC", (etf,))]
    acwi = bool(url_przyblizenia(con, ins))
    if provider == "manual" and reczny and not acwi:     # AL2: przybliżenie ACWI ma pierwszeństwo przed ręcznym
        return reczny, "manual"
    pobrany = db.wiersze(con, "SELECT * FROM sklad_etf WHERE etf=? AND reczny=0 ORDER BY waga DESC", (etf,))
    swiezy = pobrany and (dt.date.today() - dt.date.fromisoformat(pobrany[0]["data"])).days < WAZNOSC_DNI
    if pobrany and (pobrany[0]["zrodlo"] == ZRODLO_ACWI) != bool(url_przyblizenia(con, ins)):
        swiezy = False          # C2a: zmiana ustawienia przybliżenia VWCE → pobierz ponownie przy najbliższym odświeżeniu
    z_bazy = [{"ticker": r["ticker"], "weight": r["waga"], "name": r["nazwa"] or ""} for r in pobrany
              if not jest_isin(r["ticker"])]               # AL2: składniki bez tickera Yahoo — tylko ekspozycja
    # zapis z 1 pozycją = nieudane pobranie w starym trackerze → traktuj jak brak
    uzywalny = len(pobrany) > 1
    if not odswiez and not wymus:     # bez sieci: pobrany z bazy → ręczny
        if uzywalny or not reczny:
            return z_bazy[:SKLAD_MAX], (pobrany[0]["zrodlo"] if pobrany else "brak")
        return reczny, "manual"
    if uzywalny and swiezy and not wymus:
        return z_bazy[:SKLAD_MAX], pobrany[0]["zrodlo"]
    przyczyna: list[str] = []
    holdings, uzyte = pobierz_sklad(con, ins, przyczyna)
    if przyczyna and uzywalny and pobrany[0]["zrodlo"] not in ("yfinance", "manual"):
        return z_bazy[:SKLAD_MAX], pobrany[0]["zrodlo"]   # AL2: dobry skład w bazie nie jest nadpisywany zapasem yfinance
    if acwi and uzyte != ZRODLO_ACWI and reczny:
        return reczny, "manual"                           # AL2: ACWI nieudane → skład ręczny VWCE jako zapas
    if holdings:
        _zapisz(con, etf, holdings, uzyte)
        con.commit()
        return [h for h in sorted(holdings, key=lambda h: -h["weight"]) if not jest_isin(h["ticker"])][:SKLAD_MAX], uzyte
    if uzywalny or (pobrany and not reczny):
        return z_bazy[:SKLAD_MAX], pobrany[0]["zrodlo"]
    return reczny, "manual"


API_ISHARES = ("https://www.blackrock.com/varnish-api/uk-retail01-product-data/product-data/api/v1/get-fund-document"
               "?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=ishares-uk&locale=en_GB&portfolioId={id}"
               "&userType=individual&component=holdings")


def nowy_url_ishares(url: str | None) -> str | None:
    """AL2: stary adres iShares (…/products/{ID}/….ajax?fileType=csv…, dziś zwraca stronę HTML) → nowe API
    (portfolioId = ID produktu). Inny adres → None (bez zmiany)."""
    m = re.search(r"/products/(\d+)/", url or "")
    if not url or ".ajax" not in url or not m:
        return None
    return API_ISHARES.format(id=m.group(1))


def przeglad(con, zapis: bool = False) -> int:
    """C2a/AL2: `worker sklad [--dry-run]` — pobranie składów trzymanych ETF-ów; podgląd przed/po (liczba pozycji,
    pokrycie %, % wagi z krajem / sektorem / walutą), przyczyna błędu dostawcy. Bez --dry-run: zapis składów
    (tylko z dostawcy podstawowego — zapas yfinance nie jest zapisywany) i migracja starych adresów iShares (z logiem)."""
    from . import ledger
    held = set()
    for k in ("A", "B"):
        held |= {t for t, q in ledger.positions(db.transakcje(con, k)).items() if q > 1e-9}
    etfy = [i for i in db.wiersze(con, "SELECT * FROM instrumenty WHERE typ='etf' ORDER BY ticker") if i["ticker"] in held]
    print(f"### SKŁADY ETF ({'ZAPIS' if zapis else 'dry-run, bez zapisu'}) — {len(etfy)} ETF ###")

    # migracja adresów iShares (stary .ajax → nowe API)
    zmiany_url: list[tuple[str, str, str, str]] = []          # (gdzie, klucz, stary, nowy)
    ust = db.ustawienia(con)
    n = nowy_url_ishares(ust.get("sklad_acwi_url"))
    if n:
        zmiany_url.append(("ustawienie", "sklad_acwi_url", ust.get("sklad_acwi_url"), n))
    for i in db.wiersze(con, "SELECT ticker, sklad_provider, sklad_url FROM instrumenty WHERE sklad_provider='ishares'"):
        n = nowy_url_ishares(i["sklad_url"])
        if n:
            zmiany_url.append(("instrument", i["ticker"], i["sklad_url"], n))
    for gdzie, k, stary, nowy in zmiany_url:
        pid = re.search(r"portfolioId=(\d+)", nowy).group(1)
        print(f"adres iShares {gdzie} {k}: stary .ajax → nowe API (portfolioId {pid})" + ("" if zapis else " — [dry-run] bez zapisu"))
    nowe_url = {k: nowy for gdzie, k, _s, nowy in zmiany_url if gdzie == "instrument"}
    acwi_nowy = next((nowy for gdzie, k, _s, nowy in zmiany_url if gdzie == "ustawienie"), None)

    def opis(rows, w_key="waga"):
        s_ = sum(float(r.get(w_key) or 0) for r in rows)
        f = lambda k: sum(float(r.get(w_key) or 0) for r in rows if r.get(k))
        return (f"{len(rows):5d} poz. · pokrycie {s_:6.1f}% · kraj {f('kraj'):5.1f}% · sektor {f('sektor'):5.1f}% · "
                f"waluta {f('waluta'):5.1f}%")
    bledy = 0
    nowe_all = {}
    for ins in etfy:
        etf = ins["ticker"]
        provider = (ins.get("sklad_provider") or "manual").lower()
        if etf in nowe_url:
            ins = dict(ins, sklad_url=nowe_url[etf])
        przed = db.wiersze(con, "SELECT * FROM sklad_etf WHERE etf=? AND reczny=0", (etf,))
        reczny = db.wiersze(con, "SELECT * FROM sklad_etf WHERE etf=? AND reczny=1", (etf,))
        acwi = url_przyblizenia(con, ins)
        print(f"\n{ins.get('symbol') or etf} ({etf}) · dostawca {provider}" + (" · przybliżenie ACWI" if acwi else "")
              + (" · skład ręczny w bazie" if reczny else ""))
        print(f"  przed: {opis(przed)} · źródło {przed[0]['zrodlo'] if przed else '—'} · {przed[0]['data'] if przed else ''}")
        if provider == "manual" and reczny and not acwi:
            gpw = etf.upper().endswith(".WA")
            print(f"  ręczny: {opis(reczny)} — bez pobierania" + (" (ekspozycja: kraj Polska / PLN — ETF z GPW)" if gpw else ""))
            continue
        przyczyna: list[str] = []
        nowe, uzyte = pobierz_sklad(con, ins, przyczyna, acwi_url=acwi_nowy)
        for p_ in przyczyna:
            print(f"  BŁĄD dostawcy — {p_}")
        rows = [{"waga": h["weight"], "kraj": h.get("kraj"), "sektor": h.get("sektor"), "waluta": h.get("waluta")} for h in nowe]
        if not nowe:
            print("  po:    BRAK — pobranie nieudane, skład w bazie bez zmian")
            bledy += 1
            continue
        zapas = bool(przyczyna) or (uzyte == "yfinance" and provider in ("ishares", "ssga"))
        print(f"  po:    {opis(rows)} · źródło {uzyte}" + (" (przybliżenie ACWI)" if uzyte == ZRODLO_ACWI else "")
              + (" — ZAPAS yfinance, nie zostanie zapisany" if zapas else ""))
        bez_tick = sum(1 for h in nowe if jest_isin(h["ticker"]))
        if bez_tick:
            print(f"  {bez_tick} składników bez tickera Yahoo (klucz ISIN) — tylko ekspozycja, nie alerty")
        top = sorted(nowe, key=lambda h: -h["weight"])[:5]
        print("  top 5: " + ", ".join(f"{h['ticker']} {h['weight']:.2f}% [{h.get('kraj') or '?'} / {h.get('sektor') or '?'}]"
                                     for h in top))
        if zapas:
            bledy += 1
        else:
            nowe_all[etf] = (nowe, uzyte)
    if zapis and (nowe_all or zmiany_url):
        from . import store
        store.backup_db("sklad")
        for gdzie, k, stary, nowy in zmiany_url:
            if gdzie == "ustawienie":
                con.execute("UPDATE ustawienia SET wartosc=? WHERE klucz=?", (nowy, k))
            else:
                con.execute("UPDATE instrumenty SET sklad_url=? WHERE ticker=?", (nowy, k))
            print(f"ZMIANA {gdzie} {k}: sklad_url {stary[:60]}… → {nowy[:60]}…")
        for etf, (nowe, uzyte) in nowe_all.items():
            _zapisz(con, etf, nowe, uzyte)
        con.commit()
        print(f"\nZapisano składy: {', '.join(nowe_all) or '—'} (kopia bazy w data/backup/edycje)")
    elif not zapis:
        print("\n[dry-run] bez zapisu — zapis: python -m app.worker sklad")
    return 1 if bledy else 0
