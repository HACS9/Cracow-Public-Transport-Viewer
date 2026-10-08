"""AL3: wycena „dziś” zakotwiczona w IBKR.

Punkt wyjścia = ostatni raport Flex (dzień D): markPrice z OpenPositions i kurs waluty IBKR z dnia D (ConversionRates,
zapasowo fxRateToBase pozycji). Na to nakładany jest ruch od D wg Yahoo — każdy iloraz z jednego źródła:

    wartość pozycji = ilość × markPrice × (cena Yahoo teraz / zamknięcie Yahoo z D) × kurs IBKR(D) × (FX Yahoo teraz / FX Yahoo z D)
    gotówka         = saldo × kurs IBKR(D) × (FX Yahoo teraz / FX Yahoo z D)

- Zamknięcie Yahoo z D („ref”): notowanie bieżące (Ticker.info) z sesji = pierwsza sesja po D → jego prevClose;
  inaczej zamknięcie z danych dziennych z D (zapas: ostatni kurs w bazie sprzed końca D).
- Brak sesji po D (przed sesją, weekend, święto) → zmiana ceny 0. Gdy żadna trzymana pozycja nie miała sesji po D,
  także FX bez zmiany → wartość = NAV IBKR z D (bez naliczeń — patrz „różnica”).
- Pozycja bez markPrice (kupiona po raporcie) albo bez zamknięcia z D → wycena jak dotąd (Yahoo), oznaczona.

Stan: stan_alertow.ibkr_kotwica (bez zmian schematu):
  {data, zapisano, pozycje {ticker: {mark, waluta, fx}}, fx_ibkr {waluta: kurs}, nav {konto: {total, cash, stock}},
   ref {ticker: [cena, skąd]}, fx_ref {waluta: kurs}, sesje {ticker: [data sesji, cena, prev, ts]}}
Zapis: import IBKR (nav_ibkr.przebieg), uzupełnianie ref/sesji: wycena online (worker). Panel czyta bez sieci.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sys

from . import db

KLUCZ = "ibkr_kotwica"
STARE_DNI = 4            # brak informacji o sesjach dłużej niż tyle dni po D → wycena wg Yahoo (nie trzymamy starego NAV)


def _f(v) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _d(v) -> str:
    s = (v or "").strip().split(";")[0][:8]
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 and s.isdigit() else ""


def _norm(cur: str | None) -> str:
    c = (cur or "").strip()
    return "GBP" if c.upper() in ("GBP", "GBX") else c.upper()


def stan(con) -> dict:
    r = con.execute("SELECT wartosc FROM stan_alertow WHERE klucz=?", (KLUCZ,)).fetchone()
    try:
        return json.loads(r["wartosc"]) if r else {}
    except ValueError:
        return {}


def _zapisz_stan(con, st: dict) -> None:
    con.execute("INSERT INTO stan_alertow(klucz,wartosc) VALUES(?,?) ON CONFLICT(klucz) DO UPDATE SET wartosc=excluded.wartosc",
                (KLUCZ, json.dumps(st, ensure_ascii=False)))


# ------------------------------------------------------------------ zapis z raportu Flex (import)
def z_raportu(con, rap: dict, konta: dict[str, str] | None = None, base: str = "PLN") -> dict | None:
    """rap = import_flex.czytaj(). Zwraca nowy stan kotwicy (bez zapisu) albo None (brak OpenPositions / brak daty)."""
    from .import_flex import _instrumenty
    from .import_ibkr import KONTA_DOMYSLNE, mapa_tickerow
    konta = konta or KONTA_DOMYSLNE
    tick = mapa_tickerow(con, _instrumenty(rap))
    data, poz, fx_poz, nav, kursy = "", {}, {}, {}, {}
    for nr, sek in rap.items():
        k = konta.get(nr)
        if not k:
            continue
        d_st = _d(sek.get("_attr", {}).get("toDate"))
        for w in sek.get("OpenPositions", []):
            if (w.get("levelOfDetail") or "SUMMARY").upper() != "SUMMARY" or w.get("assetCategory") != "STK":
                continue
            mark, q = _f(w.get("markPrice")), _f(w.get("position"))
            if not mark or not q:
                continue
            d = _d(w.get("reportDate")) or d_st
            data = max(data, d)
            cur = _norm(w.get("currency"))
            tk = tick.get(w.get("symbol", ""), w.get("symbol", ""))
            poz[tk] = {"mark": mark, "waluta": cur, "fx": _f(w.get("fxRateToBase"))}
            if _f(w.get("fxRateToBase")) and cur != base:
                fx_poz.setdefault(cur, _f(w.get("fxRateToBase")))
        for e in sek.get("EquitySummaryInBase", []):
            d, tot = _d(e.get("reportDate")), _f(e.get("total"))
            if d and tot is not None:
                nav.setdefault(k, {})[d] = {"total": tot, "cash": _f(e.get("cash")), "stock": _f(e.get("stock"))}
        for r in sek.get("ConversionRates", []):
            d, kurs = _d(r.get("reportDate")), _f(r.get("rate"))
            if d and kurs and (r.get("toCurrency") or "").upper() == base:
                kursy[(_norm(r.get("fromCurrency")), d)] = kurs
    if not data:
        return None
    fx_ibkr = {c: v for (c, d), v in kursy.items() if d == data}
    for c, v in fx_poz.items():
        fx_ibkr.setdefault(c, v)
    return {"data": data, "zapisano": db.teraz(), "pozycje": poz, "fx_ibkr": fx_ibkr,
            "nav": {k: v.get(data) for k, v in nav.items() if v.get(data)}, "ref": {}, "fx_ref": {}, "sesje": {}}


def zapisz_z_raportu(con, rap: dict, konta: dict[str, str] | None = None, base: str = "PLN", log=print) -> dict | None:
    """Nowa kotwica z raportu. Starszy raport (np. `worker nav --plik` z historią) nie cofa kotwicy. Bez commit."""
    nowy = z_raportu(con, rap, konta, base)
    if not nowy:
        log("KOTWICA IBKR: raport bez OpenPositions z markPrice — kotwica bez zmian")
        return None
    st = stan(con)
    if st.get("data") and st["data"] > nowy["data"]:
        log(f"KOTWICA IBKR: raport z {nowy['data']} starszy niż kotwica {st['data']} — bez zmian")
        return None
    if st.get("data") == nowy["data"]:
        nowy["ref"], nowy["fx_ref"] = st.get("ref", {}), st.get("fx_ref", {})
    nowy["sesje"] = st.get("sesje", {})
    _zapisz_stan(con, nowy)
    navs = " · ".join(f"{k} {v['total']:,.2f}".replace(",", " ") for k, v in sorted(nowy["nav"].items()))
    log(f"KOTWICA IBKR: {nowy['data']} — {len(nowy['pozycje'])} poz. (markPrice), kursy IBKR "
        + ", ".join(f"{c} {v:g}" for c, v in sorted(nowy["fx_ibkr"].items())) + (f" · NAV {navs}" if navs else ""))
    return nowy


# ------------------------------------------------------------------ uzupełnianie (wycena online)
def _ref(rynek, tk: str, D: dt.date, sesja: dt.date, prev_i: float | None) -> tuple[float, str] | None:
    """Zamknięcie Yahoo z dnia D (patrz opis modułu)."""
    bary = []
    try:
        bary = [(d, _f(c)) for d, c in rynek._zrodlo(tk).historia(tk, D - dt.timedelta(days=10), sesja)]
        bary = [(d, c) for d, c in bary if c and c > 0]
    except Exception as e:  # noqa
        print(f"[kotwica] {tk}: dane dzienne: {e}", file=sys.stderr)
    miedzy = [d for d, _c in bary if D < d < sesja]
    nastepna = (sesja - D).days == 1 or (D.weekday() == 4 and sesja.weekday() == 0 and (sesja - D).days == 3)
    if prev_i and not miedzy and (bary or nastepna):
        return prev_i, "notowanie"
    z_d = [c for d, c in bary if d <= D]
    if z_d:
        return z_d[-1], "dzienne"
    r = rynek.con.execute("SELECT cena FROM kursy WHERE ticker=? AND ts<? ORDER BY ts DESC LIMIT 1",
                          (tk, (D + dt.timedelta(days=1)).isoformat())).fetchone()
    return (r["cena"], "baza") if r else None


def przygotuj(con, rynek, tickery: list[str]) -> dict:
    """Stan kotwicy do wyceny. Online: notowania bieżące trzymanych tickerów (data sesji), brakujące ref i FX z D."""
    st = stan(con)
    if not st.get("data") or not rynek.online:
        return st
    D = dt.date.fromisoformat(st["data"])
    zmiana = False
    lista = [t for t in tickery if t in st.get("pozycje", {})]
    for nazwa in sorted({rynek.zrodlo_nazwa(t) for t in lista}):
        grupa = [t for t in lista if rynek.zrodlo_nazwa(t) == nazwa]
        notow = rynek._notowania_sesji(grupa, nazwa)
        for t in grupa:
            if t in notow:
                d, n = notow[t]
                st.setdefault("sesje", {})[t] = [d.isoformat(), n.cena, n.prev_close, db.teraz()]
                zmiana = True
            elif t not in (st.get("sesje") or {}) or st["sesje"][t][0] <= st["data"]:
                # brak notowania (błąd / źródło bez Ticker.info): data ostatniej sesji z danych dziennych
                try:
                    bary = [(d, _f(c)) for d, c in rynek._zrodlo(t).historia(t, D - dt.timedelta(days=10), dt.date.today())]
                    bary = [d for d, c in bary if c and c > 0]
                except Exception:  # noqa
                    bary = []
                if bary:
                    st.setdefault("sesje", {})[t] = [max(bary).isoformat(), None, None, db.teraz()]
                    zmiana = True
    for t in lista:
        s = (st.get("sesje") or {}).get(t)
        if not s or s[0] <= st["data"] or t in st.setdefault("ref", {}):
            continue
        r = _ref(rynek, t, D, dt.date.fromisoformat(s[0]), _f(s[2]))
        if r:
            st["ref"][t] = [r[0], r[1]]
            zmiana = True
    for cur in sorted({p["waluta"] for t, p in st["pozycje"].items() if t in tickery} | set(st.get("fx_ibkr", {}))):
        if cur == rynek.base or cur in st.setdefault("fx_ref", {}):
            continue
        try:
            v = rynek._zrodlo().kurs_walut_dnia(cur, rynek.base, D)
        except Exception as e:  # noqa
            print(f"[kotwica] {cur}{rynek.base} z {D}: {e}", file=sys.stderr)
            v = None
        if _f(v):
            st["fx_ref"][cur] = float(v)
            zmiana = True
    if zmiana:
        _zapisz_stan(con, st)
    return st


# ------------------------------------------------------------------ wycena
class Kotwica:
    """Używana przez ksiega._konto. Brak kotwicy → wszystkie metody zwracają None (wycena jak dotąd)."""

    def __init__(self, st: dict, rynek, dzis: dt.date | None = None):
        self.st, self.rynek = st or {}, rynek
        self.D = self.st.get("data")
        self.dzis = dzis or dt.date.today()
        poz, ses = self.st.get("pozycje", {}), self.st.get("sesje", {})
        # ruch po raporcie: którakolwiek pozycja z kotwicy miała sesję po D
        self.ruch = bool(self.D) and any(ses.get(t) and ses[t][0] > self.D for t in poz)
        self.yahoo: dict[str, list[str]] = {}

    def aktywna(self) -> bool:
        return bool(self.D)

    def fx(self, cur: str | None) -> tuple[float, str | None] | None:
        """(kurs IBKR(D) × zmiana FX Yahoo od D, uwaga) dla waluty; None = brak kursu IBKR (wycena jak dotąd)."""
        c = _norm(cur) or self.rynek.base
        if c == self.rynek.base:
            return 1.0, None
        fi = self.st.get("fx_ibkr", {}).get(c)
        if not fi:
            return None
        if not self.ruch:
            return fi, None
        fr = self.st.get("fx_ref", {}).get(c)
        if not fr:
            return fi, f"{c}: brak kursu Yahoo z {self.D} — kurs IBKR bez zmiany"
        return fi * self.rynek.fx_spot(c) / fr, None

    def pozycja(self, konto: str, tk: str, qty: float, cena_yahoo: float | None) -> dict | None:
        """{wartosc, fx, kurs, zamrozona, zrodlo} albo None (pozycja wg Yahoo — dopisywana do listy)."""
        if not self.D:
            return None
        p = self.st.get("pozycje", {}).get(tk)
        if not p:
            self.yahoo.setdefault(konto, []).append(f"{tk}: brak w raporcie IBKR z {self.D} (np. kupiona później)")
            return None
        f = self.fx(p["waluta"])
        if not f:
            self.yahoo.setdefault(konto, []).append(f"{tk}: brak kursu IBKR {p['waluta']} z {self.D}")
            return None
        s = (self.st.get("sesje") or {}).get(tk)
        ref = (self.st.get("ref") or {}).get(tk)
        if cena_yahoo is None:            # brak kursu Yahoo (np. DVL.WA) → markPrice IBKR bez zmiany ceny
            zm, zamr, zr = 1.0, True, "markPrice (brak kursu Yahoo)"
        elif s and s[0] > self.D:
            if not ref or not cena_yahoo:
                self.yahoo.setdefault(konto, []).append(f"{tk}: brak zamknięcia Yahoo z {self.D}")
                return None
            zm, zamr, zr = cena_yahoo / ref[0], False, ref[1]
        elif s or (self.dzis - dt.date.fromisoformat(self.D)).days <= STARE_DNI:
            zm, zamr, zr = 1.0, True, "bez sesji po raporcie"
        else:
            self.yahoo.setdefault(konto, []).append(f"{tk}: brak danych o sesjach po {self.D}")
            return None
        kurs = p["mark"] * zm
        return {"wartosc": qty * kurs * f[0], "fx": f[0], "kurs": kurs, "waluta": p["waluta"], "zamrozona": zamr,
                "zrodlo": zr, "uwaga": f[1]}

    def nav(self, konto: str) -> dict | None:
        return (self.st.get("nav") or {}).get(konto)


def opis(k: dict, D: str | None) -> str:
    """Podpowiedź kafla (konto albo całość) z k['kotwica']."""
    x = k.get("kotwica")
    if not x:
        return ""
    dd = f"{D[8:10]}.{D[5:7]}" if D else "—"
    zl = lambda v: f"{v:,.2f}".replace(",", " ").replace(".", ",")
    out = [f"Wycena: ceny IBKR (markPrice) z {dd} × ruch wg Yahoo od {dd}"
           + (" — brak sesji po raporcie: wartość = NAV IBKR (bez naliczeń)" if not x["ruch"] else "")]
    if x.get("nav") is not None:
        out.append(f"NAV IBKR {dd}: {zl(x['nav'])} zł · pozycje + gotówka wg cen IBKR: {zl(x['na_d'])} zł")
        if abs(x["roznica"]) >= 0.01:
            out.append(f"różnica {zl(x['roznica'])} zł (naliczone dywidendy / odsetki / inne — poza wyceną)")
    if x.get("yahoo"):
        out.append("część pozycji wg Yahoo: " + "; ".join(x["yahoo"]))
    for u in x.get("uwagi", []):
        out.append(u)
    return "\n".join(out)
