"""Test paczki AL3 (wycena „dziś” zakotwiczona w IBKR) na sztucznych danych — osobna baza, bez sieci:
kotwica z raportu Flex (markPrice, kursy IBKR, NAV), przed sesją wartość = NAV IBKR (±0,01 zł/konto, naliczenia jako
osobna różnica), weekend (piątek → poniedziałek przed sesją), w trakcie sesji ruch % = ruch % wg Yahoo, zamknięcie z D
z prevClose / z danych dziennych, FX: kurs IBKR × zmiana Yahoo, pozycja kupiona po raporcie wg Yahoo, panel bez sieci
= ta sama wartość, starszy raport nie cofa kotwicy, ekspozycja: ISIN → nazwa.

    python -m app.test_al3       # kod 0 = OK
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

wyniki: list[tuple[bool, str]] = []
A, B = "U26077644", "U28765045"
D = dt.date(2026, 10, 7)
FXI = {"EUR": 4.3783, "USD": 3.9104}
MARK = {"VWCE": (172.92, "EUR", A, 10), "AMZN": (259.92, "USD", A, 3), "ETFBW20TR": (86.14, "PLN", A, 20),
        "MSFT": (529.76, "USD", B, 1), "DVL": (7.52, "PLN", A, 50)}
GOT_A = {"PLN": 3050.0, "EUR": 500.0, "USD": 400.0}
GOT_B = {"USD": 100.0}
NALICZ_A = 12.34


def ok(war: bool, opis: str) -> None:
    wyniki.append((bool(war), opis))
    print(f"[{'OK ' if war else 'BŁĄD'}] {opis}", flush=True)


def suma_ibkr(konto: str) -> float:
    s = sum(q * m * (FXI.get(c, 1.0)) for _sym, (m, c, k, q) in MARK.items() if k == konto)
    got = GOT_A if konto == A else GOT_B
    return s + sum(v * FXI.get(c, 1.0) for c, v in got.items())


def raport(d: dt.date = D, mark_x: float = 1.0) -> bytes:
    ds = d.strftime("%Y%m%d")

    def st(nr, nalicz):
        op = "".join(f'<OpenPosition accountId="{nr}" currency="{c}" fxRateToBase="{FXI.get(c, 1)}" assetCategory="STK" '
                     f'symbol="{s}" listingExchange="X" reportDate="{ds}" position="{q}" markPrice="{m * mark_x}" '
                     f'positionValue="{q * m}" levelOfDetail="SUMMARY" />' for s, (m, c, k, q) in MARK.items() if k == nr)
        tot = round(suma_ibkr(nr) + nalicz, 2)
        return (f'<FlexStatement accountId="{nr}" fromDate="{ds}" toDate="{ds}" period="" whenGenerated="{ds};230000">'
                f'<EquitySummaryInBase><EquitySummaryByReportDateInBase accountId="{nr}" currency="PLN" reportDate="{ds}" '
                f'cash="0" stock="0" total="{tot}" /></EquitySummaryInBase><OpenPositions>{op}</OpenPositions>'
                f'<ConversionRates><ConversionRate reportDate="{ds}" fromCurrency="EUR" toCurrency="PLN" rate="{FXI["EUR"]}" />'
                f'<ConversionRate reportDate="{ds}" fromCurrency="USD" toCurrency="PLN" rate="{FXI["USD"]}" /></ConversionRates>'
                f'</FlexStatement>')
    return (f'<FlexQueryResponse queryName="wallet" type="AF"><FlexStatements count="2">{st(A, NALICZ_A)}{st(B, 0)}'
            f'</FlexStatements></FlexQueryResponse>').encode()


class Sztuczne:
    """notowanie (wycena), notowanie_sesji (Ticker.info), historia (dzienne), kursy walut."""
    nazwa = "yahoo"

    def __init__(self):
        self.n, self.s, self.h, self.spot, self.fxd = {}, {}, {}, {}, {}
        self.bez_dzis = set()

    def notowanie(self, t):
        from .zrodla import Notowanie
        x = self.n.get(t)
        return Notowanie(x[0], x[1], x[2]) if x else None

    def notowanie_sesji(self, t):
        from .zrodla import Notowanie
        x = self.s.get(t)
        return (x[0], Notowanie(x[1], x[2], "X"), dt.datetime.combine(x[0], dt.time(17, 0))) if x else None

    def historia(self, t, od, do):
        return [(d, c) for d, c in self.h.get(t, []) if od <= d <= do]

    def kurs_walut(self, a, b):
        return 1.0 if a == b else self.spot.get(a)

    def kurs_walut_dnia(self, a, b, d):
        return 1.0 if a == b else self.fxd.get((a, d))

    def waluta(self, t):
        return None

    def notowania_dzienne(self, tickery):
        return {}


def main() -> int:
    kat = Path(tempfile.mkdtemp(prefix="wallet-test-al3-"))
    os.environ.update(WALLET_DATA=str(kat), WALLET_DB=str(kat / "wallet.db"), TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="")
    from . import db, ekspozycja, import_flex, kotwica, ksiega, nav_ibkr, zrodla
    from .rynek import Rynek
    db.DB_PATH = kat / "wallet.db"
    con = db.polacz(db.DB_PATH)
    db.inicjuj(con)
    db.uzupelnij_ustawienia(con)
    sleep = mock.patch("time.sleep")
    sleep.start()
    con.execute("INSERT INTO ustawienia(klucz,wartosc) VALUES('zrodlo_danych','yahoo') "
                "ON CONFLICT(klucz) DO UPDATE SET wartosc=excluded.wartosc")
    for tk, sym, wal, typ in (("VWCE.DE", "VWCE", "EUR", "etf"), ("AMZN", "AMZN", "USD", "stock"),
                              ("ETFBW20TR.WA", "ETFBW20TR", "PLN", "etf"), ("MSFT", "MSFT", "USD", "stock"),
                              ("GOOG", "GOOG", "USD", "stock"), ("DVL.WA", "DVL", "PLN", "stock")):
        con.execute("INSERT OR REPLACE INTO instrumenty(ticker,symbol,typ,waluta) VALUES(?,?,?,?)", (tk, sym, typ, wal))
    t0 = db.teraz()

    def tx(d, k, tk, q, p, c):
        con.execute("INSERT INTO transakcje(date,konto,ticker,qty,price,currency,fee,utworzono,zrodlo) "
                    "VALUES(?,?,?,?,?,?,0,?,'panel')", (d, k, tk, q, p, c, t0))
    for c, v in (("PLN", 5000), ("EUR", 2000), ("USD", 1000)):
        tx("2026-09-01", "A", "WPLATA", v, 1, c)
    tx("2026-09-02", "A", "VWCE.DE", 10, 150, "EUR")
    tx("2026-09-02", "A", "AMZN", 3, 200, "USD")
    tx("2026-09-02", "A", "ETFBW20TR.WA", 20, 80, "PLN")
    tx("2026-09-03", "A", "DVL.WA", 50, 7, "PLN")              # bez kursu Yahoo → markPrice IBKR
    tx("2026-09-11", "B", "WPLATA", 500, 1, "USD")
    tx("2026-09-12", "B", "MSFT", 1, 400, "USD")
    con.commit()

    # ---- kotwica z raportu (przez nav_ibkr.przebieg jak w imporcie 07:45)
    logi: list[str] = []
    nav_ibkr.przebieg(con, tresc=raport(), dry=False, kopia=False, log=logi.append)
    st = kotwica.stan(con)
    ok(st.get("data") == "2026-10-07" and st["pozycje"].get("VWCE.DE", {}).get("mark") == 172.92
       and st["pozycje"].get("DVL.WA", {}).get("mark") == 7.52
       and st["fx_ibkr"] == FXI and abs(st["nav"]["A"]["total"] - round(suma_ibkr(A) + NALICZ_A, 2)) < 1e-9
       and any(x.startswith("KOTWICA IBKR: 2026-10-07") for x in logi),
       f"kotwica z raportu: {len(st.get('pozycje', {}))} poz. markPrice, kursy IBKR {st.get('fx_ibkr')}, NAV A/B")

    src = Sztuczne()
    zrodla._instancje["yahoo"] = src
    src.spot = {"EUR": 4.3900, "USD": 3.9000}                 # FX po nocy inny niż IBKR z D
    src.fxd = {("EUR", D): 4.3800, ("USD", D): 3.9120}
    # Yahoo „zamknięcia” różnią się od markPrice IBKR (inne źródło)
    src.n = {"VWCE.DE": (172.50, 172.50, "EUR"), "AMZN": (260.10, 260.10, "USD"), "ETFBW20TR.WA": (86.10, 86.10, "PLN"),
             "MSFT": (530.00, 530.00, "USD")}
    src.s = {"VWCE.DE": (D, 172.50, 171.0), "AMZN": (D, 260.10, 258.0), "ETFBW20TR.WA": (D, 86.10, 85.0),
             "MSFT": (D, 530.0, 527.0)}

    def wycena(online=True):
        r = Rynek(con, online=online)
        w = ksiega.wycena(con, r)
        con.commit()
        return w

    # ---- przed sesją 08.10: wartość = NAV IBKR (A z naliczeniem 12,34 jako osobna różnica)
    w = wycena()
    a, b = w["A"], w["B"]
    ok(abs(a["wartosc"] - round(suma_ibkr(A), 2)) <= 0.01 and abs(b["wartosc"] - st["nav"]["B"]["total"]) <= 0.01,
       f"przed sesją: A {a['wartosc']:.2f} = pozycje+gotówka wg IBKR {suma_ibkr(A):.2f}; B {b['wartosc']:.2f} = NAV "
       f"{st['nav']['B']['total']:.2f} (±0,01)")
    ok(abs(a["kotwica"]["roznica"] - NALICZ_A) < 0.006 and "różnica 12,34" in a["kotwica"]["opis"]
       and abs(a["wartosc"] + a["kotwica"]["roznica"] - st["nav"]["A"]["total"]) <= 0.01,
       f"naliczenia A: różnica {a['kotwica']['roznica']} zł osobnym wierszem podpowiedzi (wartość + różnica = NAV)")
    ok(a["zmiana_netto"] == 0 and w["calosc"]["zmiana_netto"] == 0 and not w["calosc"]["kotwica"]["ruch"],
       "przed sesją: zmiana dziś 0 (FX po nocy pominięty — brak sesji po raporcie)")
    w_off = wycena(online=False)
    ok(w_off["calosc"]["wartosc"] == w["calosc"]["wartosc"], f"panel (bez sieci) = worker: {w_off['calosc']['wartosc']:.2f}")

    # ---- w trakcie sesji 08.10: GPW i Xetra z sesją 08.10, USA jeszcze bez sesji
    src.n.update({"VWCE.DE": (174.00, 172.50, "EUR"), "ETFBW20TR.WA": (87.00, 86.10, "PLN")})
    src.s.update({"VWCE.DE": (dt.date(2026, 10, 8), 174.0, 172.50), "ETFBW20TR.WA": (dt.date(2026, 10, 8), 87.0, 86.10)})
    src.h = {"VWCE.DE": [(dt.date(2026, 10, 6), 171.0), (D, 172.40)], "ETFBW20TR.WA": [(dt.date(2026, 10, 6), 85.0), (D, 85.0)]}
    w = wycena()
    a = w["A"]
    oczek = (10 * 172.92 * (174 / 172.5) * FXI["EUR"] * (4.39 / 4.38) + 20 * 86.14 * (87 / 86.10)
             + 3 * 259.92 * FXI["USD"] * (3.90 / 3.912)
             + 50 * 7.52 + 3050 + 500 * FXI["EUR"] * 4.39 / 4.38 + 400 * FXI["USD"] * 3.90 / 3.912)
    st = kotwica.stan(con)
    ok(abs(a["wartosc"] - oczek) <= 0.02 and st["ref"]["VWCE.DE"] == [172.5, "notowanie"]
       and st["ref"]["ETFBW20TR.WA"] == [86.1, "notowanie"],
       f"sesja: A {a['wartosc']:.2f} = ręcznie {oczek:.2f}; zamknięcie z D = prevClose notowania (nie dzienne 172,40 / 85,00)")
    poz = {p["ticker"]: p for p in a["pozycje"]}
    yv = (174 / 172.5) * (4.39 / FXI["EUR"]) - 1
    ok(abs(poz["VWCE.DE"]["zmiana_netto_pct"] - yv * 100) < 1e-3 and poz["AMZN"]["zmiana_dzien"] == 0
       and poz["VWCE.DE"]["kotwica"] == "notowanie",
       f"ruch % pozycji = wg Yahoo (VWCE {poz['VWCE.DE']['zmiana_netto_pct']:+.3f}% = {yv * 100:+.3f}%); AMZN bez sesji → cena bez zmian")
    with mock.patch.object(kotwica, "stan", return_value={}):
        wy = wycena()
    roz = abs(w["calosc"]["zmiana_netto_pct"] - wy["calosc"]["zmiana_netto_pct"])
    ok(roz < 0.02, f"ruch % kafla Całość {w['calosc']['zmiana_netto_pct']:+.3f}% vs wg Yahoo {wy['calosc']['zmiana_netto_pct']:+.3f}% "
                   f"(różnica {roz:.4f} pkt — wagi wg cen IBKR)")
    print(f"     porównanie wartości: kotwica {w['calosc']['wartosc']:.2f} · wg Yahoo {wy['calosc']['wartosc']:.2f}")

    # ---- zamknięcie z D z danych dziennych, gdy od D minęła więcej niż jedna sesja (import 08.10 nie przyszedł)
    con.execute("UPDATE stan_alertow SET wartosc=json_remove(wartosc,'$.ref.\"ETFBW20TR.WA\"') WHERE klucz='ibkr_kotwica'")
    src.s["ETFBW20TR.WA"] = (dt.date(2026, 10, 9), 88.0, 87.0)
    src.h["ETFBW20TR.WA"] = [(dt.date(2026, 10, 6), 85.0), (D, 86.0), (dt.date(2026, 10, 8), 87.0)]
    wycena()
    st = kotwica.stan(con)
    ok(st["ref"]["ETFBW20TR.WA"] == [86.0, "dzienne"], f"sesja 09.10 (po 08.10): zamknięcie z D z danych dziennych {st['ref']['ETFBW20TR.WA']}")

    # ---- pozycja kupiona po raporcie → wg Yahoo, w podpowiedzi
    tx("2026-10-08", "B", "WPLATA", 300, 1, "USD")
    tx("2026-10-08", "B", "GOOG", 1, 280, "USD")
    con.commit()
    src.n["GOOG"] = (281.0, 280.0, "USD")
    w = wycena()
    pg = next(p for p in w["B"]["pozycje"] if p["ticker"] == "GOOG")
    ok(pg["kotwica"] == "yahoo" and abs(pg["wartosc"] - 281 * 3.90) < 0.01 and "część pozycji wg Yahoo: GOOG" in w["B"]["kotwica"]["opis"]
       and "GOOG" in w["calosc"]["kotwica"]["opis"],
       "GOOG kupiony 08.10 (brak markPrice) → wg Yahoo; podpowiedź „część pozycji wg Yahoo”")
    con.execute("DELETE FROM transakcje WHERE date='2026-10-08'")
    con.commit()

    # ---- weekend: raport z piątku, poniedziałek przed sesją → NAV
    pt = dt.date(2026, 10, 9)
    nav_ibkr.przebieg(con, tresc=raport(pt), dry=False, kopia=False, log=logi.append)
    for t in src.s:
        src.s[t] = (pt, src.n[t][0], src.n[t][1])
    w = wycena()
    st = kotwica.stan(con)
    ok(st["data"] == "2026-10-09" and st["ref"] == {} and abs(w["B"]["wartosc"] - st["nav"]["B"]["total"]) <= 0.01
       and abs(w["A"]["wartosc"] + w["A"]["kotwica"]["roznica"] - st["nav"]["A"]["total"]) <= 0.01,
       f"weekend: raport pt 09.10, pn przed sesją → wartość = NAV IBKR (B {w['B']['wartosc']:.2f})")
    ok(kotwica.Kotwica(st, Rynek(con, online=False), dt.date(2026, 10, 12)).pozycja("A", "AMZN", 3, 260)["zamrozona"],
       "poniedziałek 12.10 przed sesją: sesja = pt 09.10 → zmiana ceny 0")
    # starszy raport nie cofa kotwicy
    nav_ibkr.przebieg(con, tresc=raport(D), dry=False, kopia=False, log=logi.append)
    ok(kotwica.stan(con)["data"] == "2026-10-09" and any("starszy niż kotwica" in x for x in logi),
       "raport z 07.10 (np. worker nav --plik) nie cofa kotwicy z 09.10")
    # dry-run importu nie zapisuje kotwicy
    nav_ibkr.przebieg(con, tresc=raport(dt.date(2026, 10, 12)), dry=True, kopia=False, log=logi.append)
    ok(kotwica.stan(con)["data"] == "2026-10-09", "worker nav --dry-run: kotwica bez zmian (wycofana z resztą)")

    # ---- ekspozycja: składnik z kluczem ISIN → nazwa, ISIN w podpowiedzi
    con.execute("INSERT INTO sklad_etf(etf,ticker,nazwa,waga,zrodlo,data,reczny,kraj) VALUES"
                "('ETFBW20TR.WA','PLPZU0000011','POWSZECHNY ZAKLAD UBEZPIECZEN',50,'ssga','2026-10-08',0,'Poland')")
    con.commit()
    ek = ekspozycja.ekspozycja(con, w, "A")
    e = next((x for x in ek["wszystkie"] if x["ticker"] == "PLPZU0000011"), None)
    ok(e and e["etykieta"] == "POWSZECHNY ZAKLAD UBEZPIECZEN" and e["isin"] == "PLPZU0000011",
       "ekspozycja: ISIN → nazwa (Security Name), ISIN w podpowiedzi")

    # ---- dopisek 2: kraje, tickery liczbowe ACWI, „przez ETF” < 1 zł
    from .sklad import _norm_ticker
    nt = [_norm_ticker("2330", "Taiwan Stock Exchange"), _norm_ticker("700", "Hong Kong Exchanges And Clearing Ltd"),
          _norm_ticker("WALMEX", "Bolsa Mexicana De Valores"), _norm_ticker("ITX", "Bolsa De Madrid"),
          _norm_ticker("BRK.B", "New York Stock Exchange Inc."), _norm_ticker("BP.", "London Stock Exchange")]
    ok(nt == ["2330.TW", "0700.HK", "WALMEX.MX", "ITX.MC", "BRK-B", "BP.L"], f"giełda iShares → ticker Yahoo: {nt}")
    ok([ekspozycja._kraj(k) for k in ("Greece", "Hungary", "Czech Republic")] == ["Grecja", "Węgry", "Czechy"],
       "kraje: Greece / Hungary / Czech Republic → Grecja / Węgry / Czechy")
    con.execute("INSERT INTO sklad_etf(etf,ticker,nazwa,waga,zrodlo,data,reczny,kraj) VALUES"
                "('ETFBW20TR.WA','2330.TW','TAIWAN SEMICONDUCTOR MANUFACTURING',10,'ssga','2026-10-08',0,'Taiwan'),"
                "('ETFBW20TR.WA','7203','TOYOTA MOTOR CORP',5,'ssga','2026-10-08',0,'Japan'),"
                "('ETFBW20TR.WA','MIL.WA','MILLENNIUM',0.0001,'ssga','2026-10-08',0,'Poland')")
    con.commit()
    ek = ekspozycja.ekspozycja(con, w, "A")
    sp = {x["ticker"]: x for x in ek["wszystkie"]}
    ok(sp["2330.TW"]["etykieta"] == "TAIWAN SEMICONDUCTOR MANUFACTURING" and sp["2330.TW"]["ticker_yahoo"] == "2330.TW"
       and sp["7203"]["etykieta"] == "TOYOTA MOTOR CORP" and sp["7203"]["ticker_yahoo"] is None,
       "spółki: ticker liczbowy → nazwa + ticker Yahoo (2330.TW), bez sufiksu → sama nazwa")
    ok(sp["MIL.WA"]["etf"] == [] and sp["2330.TW"]["etf"], "„przez ETF”: udział < 1 zł pominięty (MIL.WA)")

    sleep.stop()
    zrodla._instancje.pop("yahoo", None)
    con.close()
    zle = [o for w_, o in wyniki if not w_]
    print(f"\n{'WSZYSTKO OK' if not zle else f'BŁĘDY: {len(zle)}'} ({len(wyniki)} sprawdzeń)")
    return 1 if zle else 0


if __name__ == "__main__":
    sys.exit(main())
