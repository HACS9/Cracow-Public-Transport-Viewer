"""Wartość kont z IBKR (C1a, 10.2026): dzienna historia i przeniesienia papierów po wartości rynkowej.

Raport Flex „wallet” (sekcja Net Asset Value (NAV) in Base) podaje wartość każdego konta na koniec każdego dnia.
Za dni zamknięte historia_wyceny bierze tę wartość zamiast wyceny z Yahoo — TWR walleta = TWR IBKR.
Dzień bieżący zostaje z kursów na żywo.

- `zapisz(con, rap)`   — wartości dzienne, wpłaty i przeniesienia (Transfers), kursy walut IBKR (ConversionRates)
- `zastosuj(con, rynek)` — historia_wyceny: dni z IBKR nadpisane wartością konta; „wpłacono” przeliczone dla wszystkich dni
- `zamiany(con, konto)` — przeniesienie papierów A→B: w księdze zamiast kosztu zakupu wartość rynkowa z IBKR,
  z datą przeniesienia (jak w IBKR). Bez danych z IBKR — dotychczasowy sposób (koszt, data zakupu).

    python -m app.worker nav --plik X.xml --dry-run     # podgląd z raportu na dysku (np. czerwiec–wrzesień)
    python -m app.worker nav --plik X.xml               # zapis (kopia bazy przed)
    python -m app.worker nav [--dry-run]                # tylko przeliczenie historii z danych już w bazie

Codzienny import (`worker ibkr`) robi to samo z pobranym raportem — bez dodatkowych zapytań do IBKR.
"""
from __future__ import annotations

import datetime as dt
import re

from . import db, ledger
from .import_ibkr import KONTA_DOMYSLNE

GODZINA = "22:00:00"
ZAKRESY = ("A", "B")
_RE_A = re.compile(r"przeniesienie do \w+: .*transfer (\d{4}-\d{2}-\d{2})")
_RE_B = re.compile(r"przeniesione z konta \w+ (\d{4}-\d{2}-\d{2})")


def _f(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _d(v) -> str:
    s = (v or "").strip().split(";")[0][:8]
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 and s.isdigit() else ""


# ------------------------------------------------------------------ odczyt raportu
def z_raportu(rap: dict, konta: dict[str, str] | None = None) -> dict:
    """rap = import_flex.czytaj(). Zwraca nav [(konto, data, total, cash, stock)], przeplywy [dict], kursy {(para, data): kurs},
    twr {konto: %}, okres {konto: (od, do)}."""
    konta = konta or KONTA_DOMYSLNE
    out = {"nav": [], "przeplywy": [], "kursy": {}, "twr": {}, "okres": {}}
    for nr, sek in rap.items():
        k = konta.get(nr)
        if not k:
            continue
        a = sek.get("_attr", {})
        out["okres"][k] = (_d(a.get("fromDate")), _d(a.get("toDate")))
        cin = (sek.get("ChangeInNAV") or [{}])[0]
        if _f(cin.get("twr")) is not None:
            out["twr"][k] = _f(cin.get("twr"))
        for e in sek.get("EquitySummaryInBase", []):
            d, tot = _d(e.get("reportDate")), _f(e.get("total"))
            if d and tot is not None:
                out["nav"].append((k, d, tot, _f(e.get("cash")), _f(e.get("stock"))))
        for c in sek.get("CashTransactions", []):
            if c.get("type") != "Deposits/Withdrawals" or (c.get("levelOfDetail") or "DETAIL") == "SUMMARY":
                continue
            d = _d(c.get("reportDate") or c.get("dateTime"))
            kw = (_f(c.get("amount")) or 0.0) * (_f(c.get("fxRateToBase")) or 1.0)
            out["przeplywy"].append({"id": f"dep:{k}:" + (c.get("transactionID") or f"{d}:{c.get('amount')}"),
                                     "konto": k, "data": d, "kwota": kw, "rodzaj": "wplata", "symbol": None,
                                     "ilosc": None, "opis": c.get("description")})
        for t in sek.get("Transfers", []):
            v = _f(t.get("positionAmountInBase"))
            if (t.get("levelOfDetail") or "TRANSFER") != "TRANSFER" or v is None:
                continue
            d = _d(t.get("date") or t.get("dateTime"))
            out["przeplywy"].append({"id": f"tr:{k}:" + (t.get("transactionID") or f"{d}:{t.get('symbol')}:{t.get('quantity')}"),   # to samo ID po obu stronach
                                     "konto": k, "data": d, "kwota": v, "rodzaj": "transfer", "symbol": t.get("symbol"),
                                     "ilosc": _f(t.get("quantity")),
                                     "opis": f"{t.get('type', '')} {t.get('direction', '')} {t.get('account', '')}".strip()})
        for r in sek.get("ConversionRates", []):
            d, kurs = _d(r.get("reportDate")), _f(r.get("rate"))
            if d and kurs:
                out["kursy"][(f"{r.get('fromCurrency', '')}{r.get('toCurrency', '')}", d)] = kurs
    return out


def waluty_uzywane(con, base: str) -> set[str]:
    out = {(r[0] or "").upper() for r in con.execute("SELECT DISTINCT currency FROM transakcje")}
    out |= {(r[0] or "").upper() for r in con.execute("SELECT DISTINCT waluta FROM instrumenty")}
    out = {"GBP" if c in ("GBX", "GBP") else c for c in out if c}
    return out - {base}


def zapisz(con, dane: dict, base: str = "PLN") -> dict:
    """Wartości dzienne, przepływy i kursy walut (tylko waluty używane w portfelu). Bez commit."""
    n_nav = n_pr = n_fx = 0
    for k, d, tot, cash, stock in dane["nav"]:
        con.execute("INSERT INTO nav_ibkr(konto,data,total,cash,stock,zapisano) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(konto,data) DO UPDATE SET total=excluded.total, cash=excluded.cash, stock=excluded.stock, "
                    "zapisano=excluded.zapisano", (k, d, tot, cash, stock, db.teraz()))
        n_nav += 1
    for p in dane["przeplywy"]:
        con.execute("INSERT OR REPLACE INTO przeplywy_ibkr(id,konto,data,kwota,rodzaj,symbol,ilosc,opis) VALUES(?,?,?,?,?,?,?,?)",
                    (p["id"], p["konto"], p["data"], p["kwota"], p["rodzaj"], p["symbol"], p["ilosc"], p["opis"]))
        n_pr += 1
    wal = waluty_uzywane(con, base)
    for (para, d), kurs in dane["kursy"].items():
        if para.endswith(base) and para[:-len(base)] in wal:
            con.execute("INSERT OR REPLACE INTO fx(para,data,kurs,zrodlo) VALUES(?,?,?,'ibkr')", (para, d, kurs))
            n_fx += 1
    return {"nav": n_nav, "przeplywy": n_pr, "kursy": n_fx}


# ------------------------------------------------------------------ przeniesienia papierów (A→B) po wartości rynkowej
def zamiany(con, konto: str) -> tuple[set[int], list[tuple[str, float]]]:
    """(id wierszy księgi zastąpionych, [(data, kwota PLN)]) dla przeniesień papierów z/do konta.
    Wiersze księgi: A — WPLATA „przeniesienie do B … transfer RRRR-MM-DD” (koszt), B — loty „przeniesione z konta A RRRR-MM-DD”.
    Zastąpienie tylko, gdy IBKR ma przeniesienie z tą samą datą (wartość rynkowa z raportu)."""
    ibkr: dict[str, float] = {}
    for r in con.execute("SELECT data, SUM(kwota) s FROM przeplywy_ibkr WHERE konto=? AND rodzaj='transfer' GROUP BY data",
                         (konto,)):
        ibkr[r["data"]] = r["s"]
    if not ibkr:
        return set(), []
    ids: set[int] = set()
    daty: set[str] = set()
    for r in con.execute("SELECT id, ticker, note FROM transakcje WHERE konto=? AND note LIKE '%przenies%'", (konto,)):
        note = r["note"] or ""
        m = _RE_A.search(note) if r["ticker"] == ledger.CASH_TICKER else _RE_B.search(note)
        if m and m.group(1) in ibkr:
            ids.add(r["id"])
            daty.add(m.group(1))
    return ids, sorted((d, round(ibkr[d], 2)) for d in daty)


# ------------------------------------------------------------------ historia
def _wplacono_dni(con, rynek, konto: str, daty: list[str]) -> dict[str, float]:
    """„Wpłacono” na koniec każdego dnia — te same zasady co księga (ksiega._konto), z zamianą przeniesień."""
    override = db.ustawienia(con).get("cash_tracking_start", "") or ""
    rows = db.transakcje(con, konto)
    ids, zam = zamiany(con, konto)
    zdarz = []
    for d, cur, amt, kind, r in ledger.iter_flows(rows, rynek.base, override):
        if kind in ledger.CONTRIB_KINDS and r.get("id") not in ids:
            zdarz.append((d, rynek.do_bazowej_tx(amt, cur, d, "+" if kind == "pre_cost" or amt < 0 else "-")))
    zdarz += zam
    zdarz.sort()
    out, s, i = {}, 0.0, 0
    for d in sorted(daty):
        while i < len(zdarz) and zdarz[i][0] <= d:
            s += zdarz[i][1]
            i += 1
        out[d] = round(s, 2)
    return out


def zastosuj(con, rynek, dzis: dt.date | None = None, zapis: bool = True) -> dict:
    """Dni z wartością IBKR (przed dziś): wiersz dzienny A/B = wartość IBKR; całość = A + B.
    „Wpłacono” we wszystkich wierszach historii (też dzisiejszych) przeliczone jak w księdze. Bez commit."""
    dzis = (dzis or dt.date.today()).isoformat()
    nav = {k: {r["data"]: r["total"] for r in con.execute(
        "SELECT data, total FROM nav_ibkr WHERE konto=? AND data<? ORDER BY data", (k, dzis))} for k in ZAKRESY}
    hist = {z: {} for z in ("A", "B", "calosc")}     # data -> (wartosc) ostatniego odczytu dnia
    for r in con.execute("SELECT data, zakres, wartosc FROM historia_wyceny ORDER BY ts, id"):
        hist[r["zakres"]][r["data"]] = r["wartosc"]
    daty_wszystkie = sorted(set(hist["A"]) | set(hist["B"]) | set(hist["calosc"]) | set(nav["A"]) | set(nav["B"]))
    wpl = {k: _wplacono_dni(con, rynek, k, daty_wszystkie) for k in ZAKRESY}
    rap = {"dni": {k: len(nav[k]) for k in ZAKRESY}, "roznice": {}, "zakres": {}, "zapisano": zapis}
    nowe: dict[str, dict[str, tuple[float, float]]] = {z: {} for z in ("A", "B", "calosc")}
    for k in ZAKRESY:
        if nav[k]:
            rap["zakres"][k] = (min(nav[k]), max(nav[k]))
        roz = [(abs(v - hist[k][d]), d) for d, v in nav[k].items() if d in hist[k]]
        if roz:
            m = max(roz)
            rap["roznice"][k] = {"dni": len(roz), "maks": round(m[0], 2), "dzien": m[1],
                                 "srednio": round(sum(x for x, _ in roz) / len(roz), 2)}
        for d, v in nav[k].items():
            nowe[k][d] = (v, wpl[k][d])
    for d in sorted(set(nav["A"]) | set(nav["B"])):
        va = nav["A"].get(d, hist["A"].get(d, 0.0))
        vb = nav["B"].get(d, hist["B"].get(d, 0.0))
        nowe["calosc"][d] = (round(va + vb, 2), round(wpl["A"][d] + wpl["B"][d], 2))
    rap["wiersze"] = sum(len(v) for v in nowe.values())
    if not zapis:
        return rap
    for z, dni in nowe.items():
        for d, (v, w) in dni.items():
            con.execute("DELETE FROM historia_wyceny WHERE zakres=? AND data=?", (z, d))
            con.execute("INSERT INTO historia_wyceny(ts,data,zakres,wartosc,prev_close,pct,base,wplacono) VALUES(?,?,?,?,?,?,?,?)",
                        (f"{d}T{GODZINA}", d, z, v, None, None, rynek.base, w))
    # „wpłacono” w pozostałych wierszach (dni bez IBKR, dzisiejsze odczyty) — te same zasady
    for z in ("A", "B", "calosc"):
        for r in con.execute("SELECT id, data FROM historia_wyceny WHERE zakres=?", (z,)).fetchall():
            w = (wpl["A"].get(r["data"], 0.0) + wpl["B"].get(r["data"], 0.0)) if z == "calosc" else wpl[z].get(r["data"])
            if w is not None:
                con.execute("UPDATE historia_wyceny SET wplacono=? WHERE id=?", (round(w, 2), r["id"]))
    _zmiany_dnia(con)
    return rap


def _zmiany_dnia(con) -> None:
    """prev_close i pct (zmiana dnia bez wpłat, wpłata na początku dnia — jak IBKR) w wierszach dziennych z IBKR."""
    for z in ("A", "B", "calosc"):
        pop = None
        for r in con.execute("SELECT id, data, ts, wartosc, wplacono FROM historia_wyceny WHERE zakres=? ORDER BY data, ts, id",
                             (z,)).fetchall():
            if pop and r["ts"].endswith(GODZINA) and pop["data"] < r["data"]:
                baza = pop["wartosc"] + ((r["wplacono"] or 0) - (pop["wplacono"] or 0))
                con.execute("UPDATE historia_wyceny SET prev_close=?, pct=? WHERE id=?",
                            (pop["wartosc"], round((r["wartosc"] / baza - 1) * 100, 4) if baza > 0 else None, r["id"]))
            pop = r


def kontrola_twr(con, dane: dict) -> list[str]:
    """TWR walleta (po zastosowaniu) za okres raportu vs TWR IBKR (Change in NAV)."""
    from . import portfel
    out = []
    for k, t in sorted(dane["twr"].items()):
        od, do = dane["okres"].get(k, ("", ""))
        if not od or not do:
            continue
        w = portfel.twr_okres(portfel.seria_dzienna(con, k), dt.date.fromisoformat(od), dt.date.fromisoformat(do))
        out.append(f"{k}: TWR {od} – {do}: IBKR {t:.2f}% · wallet {w['twr'] if w['twr'] is not None else '—'}%")
    return out


def drukuj(zap: dict, rap: dict, kontrola: list[str]) -> None:
    print(f"### NAV IBKR: zapisano={rap['zapisano']} · dni z IBKR: A {rap['dni']['A']}, B {rap['dni']['B']} · "
          f"wierszy historii {rap['wiersze']}")
    if zap:
        print(f"  z raportu: wartości dzienne {zap['nav']}, przepływy {zap['przeplywy']}, kursy walut {zap['kursy']}")
    for k, (od, do) in rap["zakres"].items():
        print(f"  {k}: wartości IBKR {od} – {do}")
    for k, r in rap["roznice"].items():
        print(f"  {k}: dotychczasowa historia vs IBKR — {r['dni']} dni, różnica średnio {r['srednio']:.2f} zł, "
              f"maks. {r['maks']:.2f} zł ({r['dzien']})")
    for x in kontrola:
        print("  kontrola: " + x)


# ------------------------------------------------------------------ przebieg (worker nav / codzienny import)
def _podsumowanie(con) -> dict:
    from . import ksiega, portfel
    from .rynek import Rynek
    w = ksiega.wycena(con, Rynek(con, online=False))
    st = portfel.stopy(con)
    return {k: {"wplacono": w[k]["wplacono"], "wynik": w[k]["wynik"], "twr": st[k]["poczatek"]["twr"]}
            for k in ("A", "B", "calosc")}


def przebieg(con, plik=None, tresc: bytes | None = None, dry: bool = True, kopia: bool = True, log=print) -> dict:
    """Zapis danych z raportu (opcjonalnie) + przeliczenie historii. dry → wszystko wycofane (rollback)."""
    from . import import_flex, store
    from .rynek import Rynek
    if not dry:
        con.commit()
    if not dry and kopia:
        store.backup_db("nav")
    przed = _podsumowanie(con)
    dane, zap = None, {}
    if plik is not None or tresc is not None:
        rap_x = import_flex.czytaj(tresc if tresc is not None else plik)
        dane = z_raportu(rap_x)
        if not dane["nav"]:
            log("NAV IBKR: raport bez sekcji Net Asset Value (NAV) in Base — historia bez zmian")
        zap = zapisz(con, dane, Rynek(con, online=False).base)
        # AL3: kotwica wyceny „dziś” — markPrice i kursy IBKR z dnia raportu (dry → wycofane razem z resztą)
        from . import kotwica
        try:
            kotwica.zapisz_z_raportu(con, rap_x, base=Rynek(con, online=False).base, log=log)
        except Exception as e:  # noqa — błąd kotwicy nie blokuje historii; wycena wg Yahoo jak dotąd
            log(f"KOTWICA IBKR: BŁĄD — {e} (kotwica bez zmian)")
    rap = zastosuj(con, Rynek(con, online=False))
    rap["zapisano"] = not dry
    po = _podsumowanie(con)
    kontrola = kontrola_twr(con, dane) if dane else []
    drukuj(zap, rap, kontrola)
    for k, n in (("A", "A"), ("B", "B"), ("calosc", "całość")):
        a, b = przed[k], po[k]
        log(f"  {n}: wpłacono {a['wplacono']:,.2f} → {b['wplacono']:,.2f} · wynik {a['wynik']:,.2f} → {b['wynik']:,.2f} · "
            f"TWR od początku {a['twr']}% → {b['twr']}%".replace(",", " "))
    if dry:
        con.rollback()
    else:
        con.commit()
    rap.update(przed=przed, po=po, kontrola=kontrola, zapis_raportu=zap)
    return rap
