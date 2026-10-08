"""Księga A / B / całość: pozycje, gotówka, kapitał wpłacony, wycena. Logika przepływów = ledger.py (tracker).

Całość = A + B (sumowana, nie liczona z wszystkich wierszy naraz), żeby start śledzenia gotówki
jednego konta nie zmieniał rozliczenia drugiego.
"""
from __future__ import annotations

from . import db, kotwica, ledger, nav_ibkr
from .rynek import Rynek

ZAKRESY = ("A", "B")


def _n(c) -> str:
    c = (c or "").strip()
    return "GBP" if c.upper() in ("GBP", "GBX") else c.upper()


def _waluta_dzis(rynek: Rynek, n, prev: float) -> float:
    """Zmiana dzisiejsza z kursu waluty notowania: wczorajsza wartość × (kurs dziś / kurs wczoraj − 1)."""
    c = (n.waluta or rynek.base).strip()
    c_norm = "GBP" if c.upper() in ("GBP", "GBX") else c.upper()
    if c_norm == rynek.base:
        return 0.0
    spot, fp = rynek.fx_spot(c_norm), rynek.fx_poprzedni(c_norm)
    return prev * (1 - fp / spot) if fp and spot else 0.0


def _rozbicie(rynek: Rynek, n, qty: float, now: float, prev: float, e: dict, fx: float | None = None,
              wal_dzis: float | None = None) -> dict:
    """Wynik pozycji = cena + waluta + prowizje (od zakupu) oraz zmiana dzienna = cena + waluta.
    Kurs zakupu Fb = średni kurs IBKR zakupów (brutto PLN / brutto w walucie), kurs dziś F = bieżący.
    AL3: przy wycenie z kotwicy IBKR fx = kurs IBKR(D) × zmiana FX Yahoo (na jednostkę waluty pozycji w IBKR)."""
    wal_now = n.cena * qty
    F = fx if fx else (now / wal_now if wal_now else 1.0)       # efektywny kurs (z pensami)
    Fb = e["brutto"] / e["brutto_wal"] if e.get("brutto_wal") else F
    waluta = now - now / F * Fb if F else 0.0
    prowizje = -e.get("prowizje", 0.0)
    cena = now - waluta - e.get("brutto", 0.0)
    c = (n.waluta or rynek.base).strip()
    c_norm = "GBP" if c.upper() in ("GBP", "GBX") else c.upper()
    obca = c_norm != rynek.base
    spot = rynek.fx_spot(c_norm) if obca else 1.0
    waluta_dzis = _waluta_dzis(rynek, n, prev) if wal_dzis is None else wal_dzis
    return {"rozbicie": {"cena": round(cena, 2), "waluta": round(waluta, 2), "prowizje": round(prowizje, 2)},
            "dzis": {"cena": round(now - prev, 2), "waluta": round(waluta_dzis, 2)},
            "kurs_zakupu": round(Fb * (100 if c.upper() == "GBX" or c == "GBp" else 1), 4) if obca else None,
            "kurs_dzis": round(spot, 4) if obca else None}


def rozbicie_konta(k: dict) -> dict:
    """Suma rozbicia pozycji + „inne” (zamknięte pozycje, dywidendy, opłaty, gotówka w walutach) = wynik konta."""
    s = {"cena": 0.0, "waluta": 0.0, "prowizje": 0.0, "dzis_cena": 0.0, "dzis_waluta": 0.0, "pozycje": 0.0}
    for p in k["pozycje"]:
        if p.get("rozbicie"):
            for f in ("cena", "waluta", "prowizje"):
                s[f] += p["rozbicie"][f]
            s["dzis_cena"] += p["dzis"]["cena"]
            s["dzis_waluta"] += p["dzis"]["waluta"]
            s["pozycje"] += p["pnl"] or 0.0
    s["przeniesienie"] = -k.get("korekta_przeniesienia", 0.0)
    s["inne"] = k["wynik"] - s["pozycje"] - s["przeniesienie"]
    return {f: round(v, 2) for f, v in s.items()}


def _konto(con, rynek: Rynek, konto: str, override: str, kot: "kotwica.Kotwica | None" = None) -> dict:
    base = rynek.base
    rows = db.transakcje(con, konto)
    held = ledger.positions(rows)

    # koszt pozycji po kursie IBKR z dnia transakcji (C1, 10.2026; wcześniej kurs zamknięcia z dnia):
    # brutto (ilość × cena) i prowizje osobno — do rozbicia wyniku na cenę, walutę i prowizje
    koszt: dict[str, float] = {}
    sk: dict[str, dict[str, float]] = {}
    for r in rows:
        tk = ledger.ticker_of(r)
        if not tk or tk in ledger.CASH_TYPES:
            continue
        a = ledger._amount(r)
        if a is None:
            continue
        gross, fee = a
        d, cur = (r.get("date") or "").strip(), ledger.currency_of(r, base)
        kier = "+" if gross >= 0 else "-"
        e = sk.setdefault(tk, {"brutto_wal": 0.0, "brutto": 0.0, "prowizje": 0.0})
        e["brutto_wal"] += gross
        e["brutto"] += rynek.do_bazowej_tx(gross, cur, d, kier)
        e["prowizje"] += rynek.do_bazowej_tx(fee, cur, d, kier)
        koszt[tk] = e["brutto"] + e["prowizje"]

    pozycje, v_now, v_prev, v_prev_netto = [], 0.0, 0.0, 0.0
    na_d = 0.0                       # AL3: pozycje + gotówka wg cen i kursów IBKR z D (kontrola z NAV)
    for tk, qty in sorted(held.items()):
        n = rynek.notowanie(tk)
        ins = rynek.instr.get(tk) or {}
        p = {"ticker": tk, "symbol": ins.get("symbol") or tk, "nazwa": ins.get("nazwa"), "koszyk": ins.get("koszyk"),
             "ilosc": qty, "koszt": round(koszt.get(tk, 0.0), 2), "kurs": None, "prev_close": None,
             "waluta": None, "wartosc": None, "zmiana_dzien": None, "zmiana_pct": None, "pnl": None, "pnl_pct": None,
             "kurs_ts": rynek.kurs_ts(tk)}
        if n:
            now = rynek.do_bazowej(n.cena * qty, n.waluta)
            prev = rynek.do_bazowej(n.prev_close * qty, n.waluta)
            wal = _waluta_dzis(rynek, n, prev)
            a = kot.pozycja(konto, tk, qty, n.cena) if kot else None
            fx_a = None
            if a:
                # AL3: wartość z kotwicy IBKR; zmiana dzienna = ta sama zmiana % co wg Yahoo (przeskalowana)
                skala = a["wartosc"] / now if now else 1.0
                if a["zamrozona"]:
                    prev, wal = a["wartosc"], (wal * skala if kot.ruch else 0.0)
                else:
                    prev, wal = prev * skala, wal * skala
                now, fx_a = a["wartosc"], a["fx"]
                p.update(kotwica=a["zrodlo"], kurs_ibkr=round(a["kurs"], 4), waluta_ibkr=a["waluta"])
                fi = (kot.st.get("fx_ibkr") or {}).get(a["waluta"], 1.0) if a["waluta"] != rynek.base else 1.0
                na_d += qty * kot.st["pozycje"][tk]["mark"] * fi
            elif kot and kot.aktywna():
                p["kotwica"] = "yahoo"
                na_d = None if na_d is None else na_d + now
            v_now += now
            v_prev += prev
            p.update(kurs=n.cena, prev_close=n.prev_close, waluta=n.waluta, wartosc=round(now, 2),
                     zmiana_dzien=round(now - prev, 2), zmiana_pct=round((now / prev - 1) * 100 if prev else 0.0, 4))
            # netto dziś (C1): ruch notowań + zmiana kursu waluty od wczoraj = faktyczna zmiana wartości w PLN.
            # zmiana_dzien / zmiana_pct (sam ruch notowań) zostają dla alertów ruchu.
            p["zmiana_netto"] = round(now - prev + wal, 2)
            p["zmiana_netto_pct"] = round((now / (prev - wal) - 1) * 100, 4) if prev - wal else 0.0
            v_prev_netto += prev - wal
            if p["koszt"] > 0:
                p["pnl"] = round(now - p["koszt"], 2)
                p["pnl_pct"] = round((now / p["koszt"] - 1) * 100, 2)
                p.update(_rozbicie(rynek, n, qty, now, prev, sk.get(tk) or {}, fx_a, wal if fx_a else None))
        elif kot and kot.aktywna():
            a = kot.pozycja(konto, tk, qty, None)
            if a:     # AL3: bez kursu Yahoo, ale z markPrice IBKR
                v_now += a["wartosc"]
                v_prev += a["wartosc"]
                v_prev_netto += a["wartosc"]
                fi = (kot.st.get("fx_ibkr") or {}).get(a["waluta"], 1.0) if a["waluta"] != rynek.base else 1.0
                na_d += qty * kot.st["pozycje"][tk]["mark"] * fi
                p.update(wartosc=round(a["wartosc"], 2), zmiana_dzien=0.0, zmiana_pct=0.0, zmiana_netto=0.0,
                         zmiana_netto_pct=0.0, kotwica=a["zrodlo"], kurs_ibkr=round(a["kurs"], 4), waluta_ibkr=a["waluta"])
                if p["koszt"] > 0:
                    p["pnl"] = round(a["wartosc"] - p["koszt"], 2)
                    p["pnl_pct"] = round((a["wartosc"] / p["koszt"] - 1) * 100, 2)
        pozycje.append(p)

    got = ledger.cash_balances(rows, base, override)
    uwagi: list[str] = []

    def got_wartosc(c, v):
        f = kot.fx(c) if kot and kot.aktywna() else None
        if not f:
            return rynek.do_bazowej(v, c)
        if f[1]:
            uwagi.append(f[1])
        return v * f[0]
    gotowka = [{"waluta": c, "kwota": v, "wartosc": round(got_wartosc(c, v), 2)} for c, v in sorted(got.items())]
    cash = sum(g["wartosc"] for g in gotowka)
    kot_info = None
    if kot and kot.aktywna():
        for c, v in got.items():
            fi = 1.0 if _n(c) == base else (kot.st.get("fx_ibkr") or {}).get(_n(c))
            na_d = None if na_d is None or fi is None else na_d + v * fi
        nv = kot.nav(konto)
        kot_info = {"data": kot.D, "ruch": kot.ruch, "nav": nv["total"] if nv else None,
                    "na_d": round(na_d, 2) if na_d is not None else None,
                    "roznica": round(nv["total"] - na_d, 2) + 0.0 if nv and na_d is not None else 0.0,
                    "yahoo": list(kot.yahoo.get(konto, [])), "uwagi": sorted(set(uwagi))}

    # przeniesienie papierów między kontami (C1a): wartość rynkowa i data z IBKR zamiast kosztu zakupu
    zast_ids, zast = nav_ibkr.zamiany(con, konto)
    przep: dict[str, float] = {}
    zastapione = 0.0
    for d, cur, amt, kind, r in ledger.iter_flows(rows, base, override):
        # kapitał w walucie obcej (koszt sprzed startu gotówki, przeniesienie A→B) po tym samym kursie co koszt pozycji
        kier = "+" if kind == "pre_cost" or amt < 0 else "-"
        v = rynek.do_bazowej_tx(amt, cur, d, kier) if kind in ledger.CONTRIB_KINDS else rynek.do_bazowej_dnia(amt, cur, d)
        if kind in ledger.CONTRIB_KINDS and r.get("id") in zast_ids:
            zastapione += v
            continue
        przep[kind] = przep.get(kind, 0.0) + v
    transfer = sum(k for _d, k in zast)
    wplacono = sum(przep.get(k, 0.0) for k in ledger.CONTRIB_KINDS) + transfer
    wplaty = przep.get("deposit", 0.0)          # wpłaty / wypłaty gotówki na to konto
    # papiery wniesione bez gotówki: przeniesienie z innego konta (+) / na inne konto (−) i koszt sprzed startu gotówki
    przeniesione = przep.get("pre_cost", 0.0) + transfer

    wartosc = v_now + cash
    prev_total = v_prev + cash
    for p in pozycje:
        p["udzial"] = round(p["wartosc"] / wartosc * 100, 2) if p["wartosc"] and wartosc else None
    return {
        "zakres": konto, "wartosc": round(wartosc, 2), "pozycje_wartosc": round(v_now, 2), "gotowka_wartosc": round(cash, 2),
        "prev_close": round(prev_total, 2), "zmiana_dzien": round(v_now - v_prev, 2),
        # jak w trackerze: % zmiany wartości pozycji (bez gotówki) vs poprzednie zamknięcie
        "zmiana_pct": round((v_now - v_prev) / v_prev * 100, 4) if v_prev else 0.0,
        "zmiana_netto": round(v_now - v_prev_netto, 2),
        "zmiana_netto_pct": round((v_now - v_prev_netto) / v_prev_netto * 100, 4) if v_prev_netto else 0.0,
        "_prev_netto": round(v_prev_netto, 2),
        "wplacono": round(wplacono, 2), "wplaty": round(wplaty, 2), "przeniesione": round(przeniesione, 2),
        "wynik": round(wartosc - wplacono, 2),
        "wynik_pct": round((wartosc / wplacono - 1) * 100, 2) if wplacono > 0 else None,
        "dywidendy": round(przep.get("dividend", 0.0) + przep.get("tax", 0.0), 2),
        "oplaty": round(przep.get("trade_fee", 0.0) + przep.get("fee", 0.0), 2),
        "wynik_hist": round(przep.get("hist", 0.0), 2),
        # różnica wartość rynkowa − koszt przeniesionych papierów: zysk sprzed przeniesienia zostaje na koncie źródłowym
        "korekta_przeniesienia": round(transfer - zastapione, 2),
        "pozycje": pozycje, "gotowka": gotowka, "kotwica": kot_info,
    }


def wycena(con, rynek: Rynek) -> dict:
    ust = db.ustawienia(con)
    override = ust.get("cash_tracking_start", "") or ""
    # AL3: kotwica IBKR (markPrice + kurs IBKR z dnia raportu); online — uzupełnienie sesji / zamknięć z D
    held = sorted({t for k in ZAKRESY for t in ledger.positions(db.transakcje(con, k))})
    kot = kotwica.Kotwica(kotwica.przygotuj(con, rynek, held), rynek)
    konta = {k: _konto(con, rynek, k, override, kot) for k in ZAKRESY}
    suma = {"zakres": "calosc"}
    for pole in ("wartosc", "pozycje_wartosc", "gotowka_wartosc", "prev_close", "zmiana_dzien", "zmiana_netto", "_prev_netto",
                 "wplacono", "wynik",
                 "dywidendy", "oplaty", "wynik_hist"):
        suma[pole] = round(sum(konta[k][pole] for k in ZAKRESY), 2)
    pv = suma["prev_close"] - suma["gotowka_wartosc"]
    suma["zmiana_pct"] = round(suma["zmiana_dzien"] / pv * 100, 4) if pv else 0.0
    suma["zmiana_netto_pct"] = round(suma["zmiana_netto"] / suma["_prev_netto"] * 100, 4) if suma["_prev_netto"] else 0.0
    suma["wynik_pct"] = round((suma["wartosc"] / suma["wplacono"] - 1) * 100, 2) if suma["wplacono"] > 0 else None
    suma["pozycje"] = [dict(p, konto=k) for k in ZAKRESY for p in konta[k]["pozycje"]]
    for p in suma["pozycje"]:
        p["udzial"] = round(p["wartosc"] / suma["wartosc"] * 100, 2) if p["wartosc"] and suma["wartosc"] else None
    gt: dict[str, dict] = {}
    for k in ZAKRESY:
        for g in konta[k]["gotowka"]:
            e = gt.setdefault(g["waluta"], {"waluta": g["waluta"], "kwota": 0.0, "wartosc": 0.0})
            e["kwota"] = round(e["kwota"] + g["kwota"], 2)
            e["wartosc"] = round(e["wartosc"] + g["wartosc"], 2)
    suma["gotowka"] = list(gt.values())
    for k in ZAKRESY:
        konta[k]["rozbicie"] = rozbicie_konta(konta[k])
    suma["rozbicie"] = {f: round(konta["A"]["rozbicie"][f] + konta["B"]["rozbicie"][f], 2) for f in konta["A"]["rozbicie"]}
    if kot.aktywna():
        ka, kb = konta["A"]["kotwica"], konta["B"]["kotwica"]
        suma["kotwica"] = {"data": kot.D, "ruch": kot.ruch,
                           "nav": round(ka["nav"] + kb["nav"], 2) if ka["nav"] is not None and kb["nav"] is not None else None,
                           "na_d": round(ka["na_d"] + kb["na_d"], 2) if ka["na_d"] is not None and kb["na_d"] is not None else None,
                           "roznica": round(ka["roznica"] + kb["roznica"], 2),
                           "yahoo": ka["yahoo"] + kb["yahoo"], "uwagi": sorted(set(ka["uwagi"] + kb["uwagi"]))}
        for z in ("A", "B", "calosc"):
            k = konta.get(z) or suma
            k["kotwica"]["opis"] = kotwica.opis(k, kot.D)
    else:
        suma["kotwica"] = None
    return {"ts": db.teraz(), "base": rynek.base, "online": rynek.online, "A": konta["A"], "B": konta["B"],
            "calosc": suma, "braki": list(rynek.braki)}


def zapisz_historie(con, w: dict) -> None:
    for z in ("A", "B", "calosc"):
        s = w[z]
        con.execute("INSERT INTO historia_wyceny(ts,data,zakres,wartosc,prev_close,pct,base,wplacono)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (w["ts"], w["ts"][:10], z, s["wartosc"], s["prev_close"], s["zmiana_pct"], w["base"], s["wplacono"]))
    con.commit()
