"""Moduły portfela w panelu: Pulpit, Kandydaci, Zasady, Dziennik, Kalendarz, Ustawienia portfela.

Rejestrowane w web.py przez zarejestruj(app, env, render, auth, back). Szablony dziedziczą "base" trackera.
"""
from __future__ import annotations

import datetime as dt
import json

from fastapi import Depends, Form, Query, Request
from fastapi.responses import JSONResponse

from . import db, portfel, store, tickery
from .rynek import Rynek

STATUSY = [("tak", "TAK"), ("watch", "Watchlist"), ("analiza", "Analiza"), ("nie", "NIE"),
           ("w_portfelu", "W portfelu"), ("sprzedany", "Sprzedany")]
STATUS_N = dict(STATUSY)
ZSTATUS = [("obowiazuje", "obowiązuje"), ("propozycja", "propozycja"), ("otwarta", "otwarta"), ("uchylona", "uchylona")]
AKCJE = ["kup", "sprzedaj", "trymuj", "czekaj", "nic", "decyzja", "inne"]
TYPY_EV = ["earnings", "dywidenda", "makro", "event", "plan", "regula"]

POLA = {
    "kandydaci": [
        ("ticker", "Ticker", "text"), ("ticker_zrodla", "Ticker w źródle danych (Yahoo, np. TEL2-B.ST)", "text"),
        ("nazwa", "Nazwa", "text"), ("rynek", "Rynek", "text"), ("waluta", "Waluta", "text"),
        ("grupa", "Grupa / temat", "text"), ("warstwa", "Warstwa łańcucha AI", "text"),
        ("kurs_tekst", "Kurs (opis)", "text"), ("kurs", "Kurs (liczba)", "number"), ("kurs_data", "Kurs z dnia", "date"),
        ("wejscie", "Wejście (opis)", "text"), ("wejscie_od", "Wejście od", "number"), ("wejscie_do", "Wejście do", "number"),
        ("warunek", "Warunek / wyzwalacz", "textarea"), ("katalizator", "Katalizator", "text"),
        ("sl", "Stop loss", "text"), ("tp", "Take profit", "text"), ("teza", "Teza / powód", "textarea"),
        ("uwaga", "Uwaga", "textarea"), ("powrot", "Warunek powrotu", "textarea"), ("zrodlo", "Źródło", "text")],
    "zasady": [
        ("kod", "Kod", "text"), ("kategoria", "Kategoria", "text"), ("tytul", "Tytuł", "text"),
        ("tresc", "Treść", "textarea"), ("status", "Status", "select:zstatus"), ("zrodlo", "Źródło", "text"),
        ("data", "Data", "date"), ("notatka", "Notatka", "textarea")],
    "dziennik": [
        ("data", "Data", "date"), ("konto", "Konto", "select:konto"), ("ticker", "Ticker", "text"),
        ("akcja", "Akcja", "select:akcje"), ("kwota", "Kwota / wielkość", "text"),
        ("teza", "Teza", "textarea"), ("uniewaznia", "Co unieważnia tezę", "textarea"),
        ("alternatywa", "Alternatywa odrzucona", "textarea"), ("emocje", "Stan emocjonalny", "text"),
        ("przeglad_30", "Przegląd po 30 dniach", "textarea"), ("wynik_30d", "Wynik po 30 dniach", "text"),
        ("przeglad_90", "Przegląd po 90 dniach", "textarea"), ("wynik_90d", "Wynik po 90 dniach", "text")],
    "wydarzenia": [
        ("data", "Data", "date"), ("przyblizona", "Data przybliżona", "select:01"), ("ticker", "Ticker", "text"),
        ("opis", "Opis", "text"), ("typ", "Typ", "select:typy_ev"), ("zrobione", "Zrobione", "select:01"),
        ("zrodlo", "Źródło", "text")],
}
DZIENNIK_ZABLOKOWANE = ["data", "konto", "ticker", "akcja", "kwota", "teza", "uniewaznia", "alternatywa", "emocje"]
OPCJE = {"zstatus": ZSTATUS, "konto": [("", "—"), ("A", "A"), ("B", "B")], "akcje": [(a, a) for a in AKCJE],
         "typy_ev": [(t, t) for t in TYPY_EV], "01": [("0", "nie"), ("1", "tak")],
         "statusy": STATUSY, "typ_odrz": [("", "—"), ("twarde", "twarde"), ("warunkowe", "warunkowe")],
         "priorytet": [("", "—"), ("gotowa", "gotowa"), ("czeka", "czeka"), ("brak", "brak")]}
# Ustawienia portfela — pokazywane na wspólnej stronie /settings (od 06.10.2026), w grupach poniżej.
USTAWIENIA_PORTFELA = [
    ("wplata_miesieczna", "Planowana wpłata miesięczna (zł)"), ("limit_b", "Limit B, % zainwestowanej całości (S4, bez gotówki)"),
    ("min_kotwica", "Kotwica min., % zainwestowanej całości (S1)"), ("max_teza", "Teza max., % zainwestowanej całości (S2)"),
    ("max_polska", "Polska max., % zainwestowanej całości (S3)"), ("prog_zprx", "Próg ZPRX (zł, A6)"),
    ("benchmark_b", "Benchmark B (symbol)"), ("wyrok_b", "Wyrok B (RRRR-MM)"),
    ("benchmark_a", "Benchmark A (symbol, shadow jak IWMO dla B)"),
    ("sklad_vwce_acwi", "VWCE: skład przybliżony pełnym składem iShares MSCI ACWI (1 = tak, 0 = top 10 z yfinance)"),
    ("sklad_acwi_url", "Adres CSV składu iShares MSCI ACWI (przybliżenie VWCE)"),
    ("r2_wyjatki", "Wyjątki R2 — zastane pozycje B z top 10 (po przecinku)"),
    ("ike_termin", "Termin limitów IKE/IKZE"), ("baza_start", "Baza startowa (zł, informacyjnie)"),
    ("strata_podatkowa", "Strata podatkowa do rozliczenia (zł)"), ("APP_NAME", "Nazwa aplikacji"),
    ("APP_SHORT", "Krótka nazwa (ikona)"), ("zrodlo_danych", "Źródło danych rynkowych"),
    ("telegram_wysylka", "Wysyłka Telegram (1 = tak, 0 = tylko log)"),
    ("kalendarz_dni", "Kalendarz na Pulpicie — okno (dni)"),
    ("telegram_awarie", "Awaria workera na Telegram (1 = tak, także przy wysyłce 0)"),
    ("ibkr_zapis", "Import IBKR rano: 1 = dopisuje transakcje, 0 = tylko kontrola"),
    ("ibkr_token_wazny_do", "Token IBKR Flex ważny do (RRRR-MM-DD)")]
GRUPY_USTAWIEN = [
    ("telegram", "Telegram i kalendarz", ["telegram_wysylka", "telegram_awarie", "kalendarz_dni"]),
    ("portfel", "Plan i zasady portfela", ["wplata_miesieczna", "min_kotwica", "max_teza", "max_polska", "limit_b",
                                           "prog_zprx", "benchmark_a", "benchmark_b", "wyrok_b", "r2_wyjatki", "ike_termin",
                                           "baza_start", "strata_podatkowa"]),
    ("ibkr", "Import IBKR (Flex)", ["ibkr_zapis", "ibkr_token_wazny_do"]),
    ("sklady", "Składy ETF (ekspozycja)", ["sklad_vwce_acwi", "sklad_acwi_url"]),
    ("aplikacja", "Aplikacja i dane", ["APP_NAME", "APP_SHORT", "zrodlo_danych"]),
]


def grupy_ustawien(ust: dict) -> list[dict]:
    etyk = dict(USTAWIENIA_PORTFELA)
    return [{"id": gid, "nazwa": n, "pola": [(k, etyk[k], ust.get(k, "")) for k in klucze]}
            for gid, n, klucze in GRUPY_USTAWIEN]


def _pola(tabela: str, rek: dict | None, zablokowane=()) -> list[dict]:
    out = []
    for name, label, typ in POLA[tabela]:
        opcje = OPCJE.get(typ.split(":", 1)[1]) if typ.startswith("select:") else None
        v = (rek or {}).get(name)
        out.append({"name": name, "label": label, "typ": "select" if opcje else typ, "opcje": opcje,
                    "value": "" if v is None else (store.fmt(v) if typ == "number" else str(v)),
                    "readonly": name in zablokowane})
    return out


def _z_formularza(tabela: str, form, pomin=()) -> dict:
    d = {}
    for name, _l, typ in POLA[tabela]:
        if name in pomin or name not in form:
            continue
        v = (form.get(name) or "").strip()
        if typ == "number":
            d[name] = float(v.replace(",", ".")) if v else None
        elif typ.startswith("select:") and name in ("przyblizona", "zrobione"):
            d[name] = int(v or 0)
        else:
            d[name] = v or None
    return d


def _insert(con, tabela: str, d: dict) -> int:
    tickery.normalizuj(con, tabela, d)          # TickerBlad → komunikat w formularzu
    kol = list(d)
    cur = con.execute(f"INSERT INTO {tabela}({','.join(kol)}) VALUES({','.join('?' * len(kol))})", list(d.values()))
    return cur.lastrowid


def _update(con, tabela: str, idv: int, d: dict) -> None:
    tickery.normalizuj(con, tabela, d)
    if d:
        con.execute(f"UPDATE {tabela} SET {', '.join(f'{k}=?' for k in d)} WHERE id=?", list(d.values()) + [idv])


def _stan(con):
    r = Rynek(con, online=False)
    return portfel.stan(con, r), r


def zl(v, dec=0) -> str:
    if v is None:
        return "—"
    return f"{v:,.{dec}f}".replace(",", " ").replace(".", ",") if dec else f"{v:,.0f}".replace(",", " ")


def zarejestruj(app, env, render, auth, back):
    env.loader.mapping.update(SZABLONY)
    env.filters["zl"] = zl
    env.globals["status_n"] = STATUS_N

    # ============================================================ PULPIT
    @app.get("/pulpit")
    def pulpit_stary(request: Request, _=Depends(auth)):
        # C1a: dawna strona „Plan i suwak celów” → Plan (wpłata, suwak, limit B); analiza → /analiza
        from fastapi.responses import RedirectResponse
        q = request.url.query
        return RedirectResponse("/plan" + (f"?{q}" if q else ""), status_code=303)

    @app.get("/analiza")
    def analiza(request: Request, _=Depends(auth)):
        con = store.con()
        try:
            s, r = _stan(con)
            from . import ekspozycja, wykresy_c2
            qp = request.query_params
            eks = qp.get("eks") if qp.get("eks") in ("A", "B", "calosc") else "calosc"
            widok = qp.get("widok") if qp.get("widok") in ("spolki", "kraje", "waluty", "sektory") else "spolki"
            bm = "wartosc" if qp.get("bm") == "wartosc" else "indeks"
            ek = ekspozycja.ekspozycja(con, s["w"], eks)
            bench = {}
            for k in ("A", "B"):
                sb = portfel.seria_benchmarku(con, r, k, s["w"])
                etf_sym = (sb["etf"] or "—").split(".")[0]
                bench[k] = dict(sb, sym=etf_sym, wykres=wykresy_c2.porownanie(sb["punkty"], bm, k, etf_sym),
                                roznica=wykresy_c2.roznica(sb["punkty"]))
            return render("pf_analiza", title="Analiza", page="analiza", msg=qp.get("msg"),
                          s=s, ust=db.ustawienia(con), ek=ek, eks=eks, widok=widok, bm=bm, bench=bench)
        finally:
            con.close()

    @app.get("/plan")
    def plan(request: Request, kwota: str = "", gotowka: list[str] = Query(["1"]), _=Depends(auth)):
        gotowka = "1" if "1" in gotowka else "0"
        con = store.con()
        try:
            s, r = _stan(con)
            ust = db.ustawienia(con)
            # gotówka na koncie = wpłata już zaksięgowana → domyślnie nowa wpłata 0 (bez podwójnego liczenia)
            dom = 0.0 if s["gotowka"]["A"] >= 100 else ust.get("wplata_miesieczna")
            kw = portfel._f(kwota if kwota != "" else dom, 0)
            jest_got = gotowka == "1" and s["gotowka"]["A"] >= 1
            kalk = portfel.kalkulator_wplaty(s, r, kw, gotowka == "1") if (kw > 0 or jest_got) else None
            kosz_all = db.wiersze(con, "SELECT * FROM koszyki WHERE grupa<>'sp' ORDER BY kolejnosc")
            zmiany = db.wiersze(con, "SELECT * FROM zmiany_planu ORDER BY id DESC LIMIT 10")
            return render("pf_pulpit", title="Plan", page="plan", msg=request.query_params.get("msg"),
                          s=s, kalk=kalk, kwota=kw, gotowka=gotowka == "1", kosz_all=kosz_all, zmiany=zmiany, ust=ust)
        finally:
            con.close()

    @app.post("/pulpit/symulacja")
    async def pulpit_symulacja(request: Request, _=Depends(auth)):
        body = await request.json()
        con = store.con()
        try:
            s, _r = _stan(con)
            return JSONResponse(portfel.symulacja(con, s, {k: float(v) for k, v in (body.get("cele") or {}).items()}))
        finally:
            con.close()

    @app.post("/pulpit/plan")
    async def pulpit_plan(request: Request, _=Depends(auth)):
        form = await request.form()
        cele = {k[4:]: float(v) for k, v in form.items() if k.startswith("cel_")}
        store.backup_db("plan")
        con = store.con()
        try:
            s, _r = _stan(con)
            ok, msg = portfel.zapisz_plan(con, s, cele, (form.get("opis") or "").strip(), form.get("wymus") == "1")
            return back("/plan", msg)
        finally:
            con.close()

    @app.get("/pulpit/ustawienia")
    def pulpit_ustawienia(_=Depends(auth)):
        # ustawienia w jednym miejscu: stary adres prowadzi do /settings
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/settings#portfel", status_code=303)

    @app.post("/pulpit/ustawienia")
    async def pulpit_ustawienia_zapisz(request: Request, _=Depends(auth)):
        form = await request.form()
        wroc = form.get("wroc") or "portfel"
        store.backup_db("ust")
        con = store.con()
        try:
            for k, _l in USTAWIENIA_PORTFELA:
                if k in form:
                    portfel.db_set(con, k, (form.get(k) or "").strip())
            con.commit()
            from fastapi.responses import RedirectResponse
            from urllib.parse import quote
            return RedirectResponse(f"/settings?msg={quote('Zapisano ustawienia.')}#{quote(wroc)}", status_code=303)
        finally:
            con.close()

    # ============================================================ KANDYDACI
    @app.get("/kandydaci")
    def kandydaci(request: Request, status: str = "tak", q: str = "", grupa: str = "", _=Depends(auth)):
        con = store.con()
        try:
            wsz = db.wiersze(con, "SELECT * FROM kandydaci ORDER BY ticker")
            licz = {k: sum(1 for x in wsz if x["status"] == k) for k, _n in STATUSY}
            grupy = sorted({x["grupa"] for x in wsz if x["grupa"]})
            ql = q.lower().strip()
            lista = [x for x in wsz if (status == "wszystko" or x["status"] == status) and (not grupa or x["grupa"] == grupa)
                     and (not ql or ql in " ".join(str(x.get(k) or "") for k in ("ticker", "nazwa", "teza", "uwaga", "warunek")).lower())]
            portfel.odleglosc_strefy(con, lista)
            lista.sort(key=lambda x: (not x["w_strefie"], x["odl"] is None, abs(x["odl"] or 0), x["ticker"]))
            s, _r = _stan(con)
            return render("pf_kand", title="Kandydaci", page="kand", msg=request.query_params.get("msg"),
                          lista=lista, licz=licz, razem=len(wsz), status=status, q=q, grupa=grupa, grupy=grupy,
                          statusy=STATUSY, lb=s["limit_b"])
        finally:
            con.close()

    def _kand_form(request, rek, nowy):
        con = store.con()
        try:
            hist = [] if nowy else db.wiersze(con, "SELECT * FROM historia_statusow WHERE kandydat_id=? ORDER BY data DESC, id DESC",
                                              (rek["id"],))
        finally:
            con.close()
        return render("pf_form", title=("Nowy kandydat" if nowy else f"Kandydat {rek['ticker']}"), page="kand",
                      msg=request.query_params.get("msg"), pola=_pola("kandydaci", rek),
                      akcja="/kandydaci/nowy" if nowy else f"/kandydaci/{rek['id']}", wstecz="/kandydaci?status=" + (rek or {}).get("status", "tak"),
                      kandydat=rek, nowy=nowy, hist=hist, opcje=OPCJE, usun=None if nowy else f"/kandydaci/{rek['id']}/usun")

    @app.get("/kandydaci/nowy")
    def kand_nowy(request: Request, status: str = "analiza", _=Depends(auth)):
        return _kand_form(request, {"status": status}, True)

    @app.post("/kandydaci/nowy")
    async def kand_nowy_zapisz(request: Request, _=Depends(auth)):
        form = await request.form()
        d = _z_formularza("kandydaci", form)
        if not d.get("ticker"):
            return back("/kandydaci/nowy", "Ticker jest wymagany.")
        d["ticker"] = d["ticker"].upper()
        st = form.get("status") or "analiza"
        d.update(status=st, typ_odrzucenia=form.get("typ_odrzucenia") or None, priorytet=form.get("priorytet") or None,
                 utworzono=db.teraz(), zmieniono=db.teraz())
        store.backup_db("kand")
        con = store.con()
        try:
            idv = _insert(con, "kandydaci", d)
            con.execute("INSERT INTO historia_statusow(kandydat_id,data,z_statusu,na_status,powod) VALUES(?,?,?,?,?)",
                        (idv, db.teraz(), None, st, (form.get("powod") or "dodanie").strip()))
            con.commit()
        except tickery.TickerBlad as e:
            return back("/kandydaci/nowy", str(e))
        finally:
            con.close()
        return back(f"/kandydaci/{idv}", f"Dodano {d['ticker']}.")

    @app.get("/kandydaci/{idv}")
    def kand_edytuj(request: Request, idv: int, _=Depends(auth)):
        con = store.con()
        try:
            r = con.execute("SELECT * FROM kandydaci WHERE id=?", (idv,)).fetchone()
        finally:
            con.close()
        if not r:
            return back("/kandydaci", "Nie ma takiego kandydata.")
        return _kand_form(request, dict(r), False)

    @app.post("/kandydaci/{idv}")
    async def kand_zapisz(request: Request, idv: int, _=Depends(auth)):
        form = await request.form()
        d = _z_formularza("kandydaci", form)
        if d.get("ticker"):
            d["ticker"] = d["ticker"].upper()
        d["zmieniono"] = db.teraz()
        store.backup_db("kand")
        con = store.con()
        try:
            _update(con, "kandydaci", idv, d)
            con.commit()
        except tickery.TickerBlad as e:
            return back(f"/kandydaci/{idv}", str(e))
        finally:
            con.close()
        return back(f"/kandydaci/{idv}", "Zapisano.")

    @app.post("/kandydaci/{idv}/status")
    def kand_status(idv: int, status: str = Form(...), powod: str = Form(...), typ_odrzucenia: str = Form(""),
                    priorytet: str = Form(""), powrot: str = Form(""), _=Depends(auth)):
        if status not in STATUS_N:
            return back(f"/kandydaci/{idv}", "Nieznany status.")
        if not powod.strip():
            return back(f"/kandydaci/{idv}", "Podaj uzasadnienie zmiany statusu.")
        store.backup_db("kand-status")
        con = store.con()
        try:
            r = con.execute("SELECT status FROM kandydaci WHERE id=?", (idv,)).fetchone()
            if not r:
                return back("/kandydaci", "Nie ma takiego kandydata.")
            con.execute("UPDATE kandydaci SET status=?, typ_odrzucenia=?, priorytet=?, powrot=COALESCE(NULLIF(?,''), powrot),"
                        " zmieniono=? WHERE id=?", (status, typ_odrzucenia or None, priorytet or None, powrot.strip(),
                                                   db.teraz(), idv))
            con.execute("INSERT INTO historia_statusow(kandydat_id,data,z_statusu,na_status,powod) VALUES(?,?,?,?,?)",
                        (idv, db.teraz(), r["status"], status, powod.strip()))
            con.commit()
        finally:
            con.close()
        return back(f"/kandydaci/{idv}", f"Status: {STATUS_N[status]}.")

    @app.post("/kandydaci/{idv}/usun")
    def kand_usun(idv: int, _=Depends(auth)):
        store.backup_db("kand-del")
        con = store.con()
        try:
            con.execute("DELETE FROM kandydaci WHERE id=?", (idv,))
            con.commit()
        finally:
            con.close()
        return back("/kandydaci", "Usunięto kandydata (kopia bazy w data/backup/edycje).")

    # ============================================================ ZASADY / DZIENNIK / KALENDARZ (wspólny CRUD)
    def crud(tabela: str, sciezka: str, tytul: str, page: str, zablokowane=()):
        def formularz(request, rek, nowy):
            return render("pf_form", title=(f"Nowy wpis — {tytul}" if nowy else tytul), page=page,
                          msg=request.query_params.get("msg"),
                          pola=_pola(tabela, rek, () if nowy else zablokowane),
                          akcja=f"{sciezka}/nowy" if nowy else f"{sciezka}/{rek['id']}", wstecz=sciezka,
                          kandydat=None, nowy=nowy, hist=[], opcje=OPCJE,
                          usun=None if nowy else f"{sciezka}/{rek['id']}/usun",
                          info=("Teza i kontekst decyzji są niezmienne po zapisie — uzupełniasz tylko przeglądy 30/90 dni."
                                if (tabela == "dziennik" and not nowy) else None))

        @app.get(f"{sciezka}/nowy", name=f"{tabela}_nowy")
        def nowy(request: Request, _=Depends(auth)):
            dom = {"data": dt.date.today().isoformat()} if tabela in ("dziennik", "wydarzenia") else {}
            if tabela == "zasady":
                dom = {"status": "propozycja", "data": dt.date.today().isoformat()}
            return formularz(request, dom, True)

        @app.post(f"{sciezka}/nowy", name=f"{tabela}_nowy_zapisz")
        async def nowy_zapisz(request: Request, _=Depends(auth)):
            d = _z_formularza(tabela, await request.form())
            wym = {"zasady": ("kod", "tytul"), "dziennik": ("data", "akcja"), "wydarzenia": ("data", "opis")}[tabela]
            if any(not d.get(k) for k in wym):
                return back(f"{sciezka}/nowy", "Wymagane: " + ", ".join(wym))
            if tabela == "dziennik":
                d["utworzono"] = db.teraz()
            store.backup_db(tabela)
            con = store.con()
            try:
                _insert(con, tabela, d)
                con.commit()
            except Exception as e:  # noqa
                return back(f"{sciezka}/nowy", f"Nie zapisano: {e}")
            finally:
                con.close()
            return back(sciezka, "Zapisano.")

        @app.get(f"{sciezka}/{{idv}}", name=f"{tabela}_edytuj")
        def edytuj(request: Request, idv: int, _=Depends(auth)):
            con = store.con()
            try:
                r = con.execute(f"SELECT * FROM {tabela} WHERE id=?", (idv,)).fetchone()
            finally:
                con.close()
            return formularz(request, dict(r), False) if r else back(sciezka, "Nie ma takiego wpisu.")

        @app.post(f"{sciezka}/{{idv}}", name=f"{tabela}_zapisz")
        async def zapisz(request: Request, idv: int, _=Depends(auth)):
            d = _z_formularza(tabela, await request.form(), pomin=zablokowane)
            store.backup_db(tabela)
            con = store.con()
            try:
                _update(con, tabela, idv, d)
                con.commit()
            except Exception as e:  # noqa
                return back(f"{sciezka}/{idv}", f"Nie zapisano: {e}")
            finally:
                con.close()
            return back(sciezka, "Zapisano.")

        @app.post(f"{sciezka}/{{idv}}/usun", name=f"{tabela}_usun")
        def usun(idv: int, _=Depends(auth)):
            store.backup_db(tabela + "-del")
            con = store.con()
            try:
                con.execute(f"DELETE FROM {tabela} WHERE id=?", (idv,))
                con.commit()
            finally:
                con.close()
            return back(sciezka, "Usunięto (kopia bazy w data/backup/edycje).")

    @app.get("/zasady")
    def zasady(request: Request, _=Depends(auth)):
        con = store.con()
        try:
            z = db.wiersze(con, "SELECT * FROM zasady ORDER BY kod")
        finally:
            con.close()
        kat = []
        for x in z:
            if x["kategoria"] not in kat:
                kat.append(x["kategoria"])
        return render("pf_zasady", title="Zasady", page="zas", msg=request.query_params.get("msg"),
                      zasady=z, kategorie=kat, zstatus=dict(ZSTATUS))

    @app.get("/dziennik")
    def dziennik(request: Request, _=Depends(auth)):
        con = store.con()
        try:
            d = db.wiersze(con, "SELECT * FROM dziennik ORDER BY data DESC, id DESC")
        finally:
            con.close()
        dzis = dt.date.today()
        for x in d:
            try:
                dni = (dzis - dt.date.fromisoformat(x["data"])).days
            except (TypeError, ValueError):
                dni = 0
            x["zalegle"] = " · ".join(t for t, war in (("przegląd 30 dni", dni >= 30 and not x["przeglad_30"]),
                                                       ("przegląd 90 dni", dni >= 90 and not x["przeglad_90"])) if war)
        return render("pf_dziennik", title="Dziennik", page="dz", msg=request.query_params.get("msg"), wpisy=d)

    @app.get("/kalendarz")
    def kalendarz(request: Request, wszystko: str = "", _=Depends(auth)):
        con = store.con()
        try:
            ev = db.wiersze(con, "SELECT * FROM wydarzenia ORDER BY data, id")
            zal = portfel.zalegle(con)
        finally:
            con.close()
        dzis = dt.date.today().isoformat()
        if not wszystko:
            ev = [e for e in ev if not e["zrobione"] and e["data"] >= dzis]
        return render("pf_kal", title="Kalendarz", page="kal", msg=request.query_params.get("msg"), ev=ev,
                      wszystko=bool(wszystko), dzis=dzis, zal=zal)

    @app.post("/kalendarz/{idv}/zrobione")
    def kal_zrobione(idv: int, _=Depends(auth)):
        con = store.con()
        try:
            con.execute("UPDATE wydarzenia SET zrobione=1-COALESCE(zrobione,0) WHERE id=?", (idv,))
            con.commit()
        finally:
            con.close()
        return back("/kalendarz", "Zmieniono.")

    # kolejność: trasy stałe (/kalendarz/{id}/zrobione) przed ogólnymi
    crud("zasady", "/zasady", "Zasada", "zas")
    crud("dziennik", "/dziennik", "Dziennik — wpis", "dz", zablokowane=DZIENNIK_ZABLOKOWANE)
    crud("wydarzenia", "/kalendarz", "Kalendarz — wydarzenie", "kal")


# ======================================================================== SZABLONY
STYL = """
<style>
.pill{display:inline-block;font-size:12px;padding:2px 8px;border-radius:10px;border:1px solid var(--btn-line);white-space:nowrap}
.pill.ok{color:var(--up);background:var(--ok-bg);border-color:transparent} .pill.bad{color:var(--crit-tx);background:var(--crit-bg);border-color:transparent} .pill.acc{color:var(--tx);border-color:var(--btn-line)}
.al{padding:8px 12px;border-radius:6px;margin-bottom:6px;background:var(--msg-bg);font-size:14px}
.al.krytyczny{background:var(--crit-bg);color:var(--crit-tx)} .al.ostrzezenie{background:var(--w-bg);color:var(--w-tx)} .al.info{background:var(--sub)}
.bar{height:10px;background:var(--field);border:1px solid var(--line);border-radius:3px;position:relative;margin:4px 0 12px}
.bar i{position:absolute;left:0;top:0;bottom:0;background:var(--acc);border-radius:2px}
.bar i.bad{background:var(--down)} .bar b{position:absolute;top:-4px;bottom:-4px;width:2px;background:var(--tx)}
.band{position:relative;height:12px;min-width:150px;background:var(--field);border:1px solid var(--line);border-radius:3px}
.band .z{position:absolute;top:0;bottom:0;background:var(--up);opacity:.22}
.band .t{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--dim)}
.band .c{position:absolute;top:1px;width:8px;height:8px;margin-left:-4px;border-radius:50%;background:var(--acc)}
.band .c.bad{background:var(--down)}
.kand{border:1px solid var(--line);border-left:4px solid var(--dim);border-radius:4px;padding:10px 12px;margin-bottom:8px}
.kand.s-tak{border-left-color:var(--up)} .kand.s-watch{border-left-color:var(--acc)} .kand.s-nie{border-left-color:var(--down)} .kand.s-w_portfelu{border-left-color:var(--focus)}
.kg{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:4px 14px;margin:6px 0;font-size:13px}
.kg span{display:block;color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.05em}
.kt{font-size:13px;margin:4px 0;color:var(--tx)} .kt span{color:var(--dim);font-size:11px;text-transform:uppercase;margin-right:6px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.chips a{text-decoration:none;padding:4px 11px;border:1px solid var(--btn-line);border-radius:12px;color:var(--tx);font-size:13px;background:var(--panel)}
.chips a.on{color:var(--ink-tx);background:var(--ink-bg);border-color:var(--ink-bg)}
.sl{display:grid;grid-template-columns:70px 1fr 56px 130px;gap:10px;align-items:center;margin-bottom:6px}
.sl input[type=range]{width:100%}
@media (max-width:720px){.sl{grid-template-columns:60px 1fr 50px}.sl .pb{grid-column:1/-1}}
</style>

<style>
 .ek2{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:20px;align-items:start}
 .ek2b{display:grid;grid-template-columns:1fr 1fr;gap:24px}
 .ekbar{height:6px;border-radius:3px;background:var(--line);position:relative;min-width:60px}
 .ekbar i{position:absolute;left:0;top:0;bottom:0;border-radius:3px;background:var(--acc)}
 .ekbar b{position:absolute;top:0;bottom:0;border-radius:3px;background:#B9A27A}
 .sw1,.sw2,.sw3{display:inline-block;width:14px;height:3px;margin:0 5px 0 10px;vertical-align:middle;background:var(--acc)}
 .sw2{background:#B9A27A} .sw3{background:none;border-top:2px dashed var(--cost-line);height:0}
 @media (max-width:900px){.ek2,.ek2b{grid-template-columns:1fr} .hm{display:none}}
</style>"""

ANALIZA = """{% extends "base" %}{% block body %}""" + STYL + """
{% set w = s.w %}
<section id="stopy"><h2>Stopa zwrotu i obsunięcie</h2>
  {% macro p2(v) %}{{ ('%+.1f'|format(v))|replace('.', ',')|replace('-', '−') ~ '%' if v is not none else '—' }}{% endmacro %}
  {% macro dd(t) %}{% if t.dd is not none %}{{ p2(t.dd) }}{% if t.dd_od %} <span class="dim">({{ t.dd_od[8:10] }}.{{ t.dd_od[5:7] }} → {{ t.dd_do[8:10] }}.{{ t.dd_do[5:7] }})</span>{% endif %}{% else %}—{% endif %}{% endmacro %}
  <table><tr><th></th><th class="num">TWR od początku</th><th class="num">TWR od 1.01</th><th class="num">TWR 30 dni</th><th class="num">XIRR</th><th class="num">Maks. obsunięcie</th><th class="num">Kontrola IBKR</th></tr>
  {% for k, n in (('A', 'A'), ('B', 'B'), ('calosc', 'Całość')) %}{% set t = s.stopy[k] %}{% set ib = s.stopy.ibkr.get(k) %}
  <tr><td><b>{{ n }}</b>{% if t.poczatek.od %} <span class="dim">od {{ t.poczatek.od[8:10] }}.{{ t.poczatek.od[5:7] }}</span>{% endif %}</td>
    <td class="num">{{ p2(t.poczatek.twr) }}</td><td class="num">{{ p2(t.rok.twr) }}</td><td class="num">{{ p2(t.d30.twr) }}</td>
    <td class="num">{{ p2(s.xirr[k]) if s.xirr[k] is number else s.xirr[k] }}</td><td class="num">{{ dd(t.poczatek) }}</td>
    <td class="num">{% if ib %}IBKR {{ p2(ib.twr) }} · wallet {{ p2(ib.wallet) }} <span class="dim">({{ ib.od[8:10] }}.{{ ib.od[5:7] }}–{{ ib.do[8:10] }}.{{ ib.do[5:7] }})</span>{% else %}<span class="dim">—</span>{% endif %}</td></tr>
  {% endfor %}</table>
  <p class="dim">TWR pomija wpływ wpłat i ich terminów — ocenia decyzje inwestycyjne. XIRR uwzględnia moment wpłat — Twój faktyczny zwrot w skali roku.
    Obsunięcie: największy spadek indeksu TWR od szczytu. Dni zamknięte: wartość konta z IBKR (raport Flex), dziś: zmiana z bieżących kursów. Kontrola IBKR: TWR z ostatniego raportu vs wallet za ten sam okres.</p>
</section>

<section id="wykres"><h2>Wartość vs wpłacono</h2>
  <div class="row" style="justify-content:space-between;margin-bottom:8px">
    <span class="chips" id="aw-dni" style="margin:0"><a href="#" data-v="30">30 dni</a><a href="#" data-v="90">3 mies.</a><a href="#" data-v="365">rok</a><a href="#" class="on" data-v="100000">całość</a></span>
    <span class="chips" id="aw-zakres" style="margin:0"><a href="#" class="on" data-v="calosc">Całość</a><a href="#" data-v="A">A</a><a href="#" data-v="B">B</a></span></div>
  <div id="aw-body" class="dim">Wczytuję…</div>
  <p class="dim">Pełna historia dzienna, miesięczna i roczna oraz eksport CSV: <a href="/history">Historia →</a></p>
  <script>(function(){ var st={dni:'100000',zakres:'calosc'}, body=document.getElementById('aw-body');
    function laduj(){ body.textContent='Wczytuję…';
      fetch('/pulpit/wykres?dni='+st.dni+'&zakres='+st.zakres,{credentials:'same-origin'}).then(function(r){return r.text()})
        .then(function(t){ body.innerHTML=t; if(window.walletCharts) window.walletCharts(body); }).catch(function(e){ body.textContent='Błąd: '+e; }); }
    [['aw-dni','dni'],['aw-zakres','zakres']].forEach(function(p){ var ch=document.querySelectorAll('#'+p[0]+' a');
      ch.forEach(function(a){ a.addEventListener('click',function(e){ e.preventDefault(); ch.forEach(function(x){x.classList.toggle('on',x===a)}); st[p[1]]=a.dataset.v; laduj(); }); }); });
    laduj(); })();</script>
</section>

<section id="rozbicie"><h2>Wynik: cena · waluta · prowizje</h2>
  {% macro z(v, bold=False) %}<td class="num {{ 'ok' if (v or 0) > 0.5 else ('bad' if (v or 0) < -0.5 else '') }}">{% if bold %}<b>{% endif %}{{ '%+.0f'|format(v)|replace('-', '−') if v is not none else '—' }}{% if bold %}</b>{% endif %}</td>{% endmacro %}
  {% macro kurs(v) %}{{ ('%.4f'|format(v))|replace('.', ',') if v else '' }}{% endmacro %}
  <div class="row" style="margin-bottom:6px"><span class="chips" id="rz-tryb"><a href="#rozbicie" class="on" data-t="od">od zakupu</a><a href="#rozbicie" data-t="dz">dziś</a></span>
    <span class="dim">kurs zakupu = kurs przewalutowania IBKR z dnia transakcji (bez przewalutowania — zamknięcie z dnia)</span></div>
  <table data-t="od"><tr><th>Pozycja</th><th>Konto</th><th>Waluta</th><th class="num">Wynik zł</th><th class="num">Cena</th><th class="num">Waluta</th><th class="num">Prowizje</th><th class="num">Kurs zakupu → dziś</th></tr>
  {% for k in ('A', 'B') %}{% set kk = w[k] %}
    {% for p in kk.pozycje if p.rozbicie %}<tr><td><b>{{ p.symbol }}</b></td><td>{{ k }}</td><td>{{ p.waluta }}</td>{{ z(p.pnl) }}{{ z(p.rozbicie.cena) }}{{ z(p.rozbicie.waluta) if p.kurs_zakupu else '<td class="num dim">—</td>'|safe }}{{ z(p.rozbicie.prowizje) }}
      <td class="num">{% if p.kurs_zakupu %}{{ kurs(p.kurs_zakupu) }} → {{ kurs(p.kurs_dzis) }}{% else %}<span class="dim">—</span>{% endif %}</td></tr>{% endfor %}
    {% set r = kk.rozbicie %}
    <tr style="border-top:2px solid var(--line)"><td colspan="3">Pozycje {{ k }}</td>{{ z(r.pozycje) }}{{ z(r.cena) }}{{ z(r.waluta) }}{{ z(r.prowizje) }}<td></td></tr>
    {% if r.przeniesienie|abs >= 0.5 %}<tr><td colspan="3" class="dim">Przeniesienie A→B po wartości rynkowej (zysk sprzed przeniesienia zostaje w A)</td>{{ z(r.przeniesienie) }}<td></td><td></td><td></td><td></td></tr>{% endif %}
    {% if r.inne|abs >= 0.5 %}<tr><td colspan="3" class="dim">Zamknięte pozycje, dywidendy, opłaty, gotówka w walutach</td>{{ z(r.inne) }}<td></td><td></td><td></td><td></td></tr>{% endif %}
    <tr><td colspan="3"><b>{{ k }} razem</b></td>{{ z(kk.wynik, True) }}{{ z(r.cena) }}{{ z(r.waluta) }}{{ z(r.prowizje) }}<td></td></tr>
  {% endfor %}
  {% set r = w.calosc.rozbicie %}
  <tr style="border-top:2px solid var(--line)"><td colspan="3"><b>Całość</b></td>{{ z(w.calosc.wynik, True) }}{{ z(r.cena) }}{{ z(r.waluta) }}{{ z(r.prowizje) }}<td></td></tr>
  </table>
  <table data-t="dz" style="display:none"><tr><th>Pozycja</th><th>Konto</th><th>Waluta</th><th class="num">Dziś zł</th><th class="num">Cena</th><th class="num">Waluta</th></tr>
  {% for k in ('A', 'B') %}{% set kk = w[k] %}
    {% for p in kk.pozycje if p.rozbicie %}<tr><td><b>{{ p.symbol }}</b></td><td>{{ k }}</td><td>{{ p.waluta }}</td>{{ z(p.dzis.cena + p.dzis.waluta) }}{{ z(p.dzis.cena) }}{{ z(p.dzis.waluta) if p.kurs_zakupu else '<td class="num dim">—</td>'|safe }}</tr>{% endfor %}
    {% set r = kk.rozbicie %}<tr style="border-top:2px solid var(--line)"><td colspan="3"><b>{{ k }} razem</b></td>{{ z(r.dzis_cena + r.dzis_waluta, True) }}{{ z(r.dzis_cena) }}{{ z(r.dzis_waluta) }}</tr>
  {% endfor %}
  {% set r = w.calosc.rozbicie %}<tr style="border-top:2px solid var(--line)"><td colspan="3"><b>Całość</b></td>{{ z(r.dzis_cena + r.dzis_waluta, True) }}{{ z(r.dzis_cena) }}{{ z(r.dzis_waluta) }}</tr>
  </table>
  <p class="dim">Od zakupu: cena + waluta + prowizje = wynik pozycji; wynik konta = pozycje + zamknięte pozycje, dywidendy, opłaty, gotówka w walutach i różnica przy przeniesieniu papierów między kontami.
    Dziś: zmiana od poprzedniego zamknięcia — cena (jak kolumna „Dziś” na pulpicie) i kurs waluty od wczoraj.</p>
  <script>(function(){ var ch=document.querySelectorAll('#rz-tryb a');
    ch.forEach(function(a){ a.addEventListener('click',function(e){ e.preventDefault(); ch.forEach(function(x){ x.classList.toggle('on', x===a); });
      document.querySelectorAll('#rozbicie table[data-t]').forEach(function(t){ t.style.display = t.dataset.t===a.dataset.t ? '' : 'none'; }); }); }); })();</script>
</section>

<section id="ekspozycja">{% set q = '/analiza?eks=' ~ eks ~ '&bm=' ~ bm %}
  <div class="row" style="justify-content:space-between;align-items:center"><h2 style="margin:0">Ekspozycja (look-through ETF, bez gotówki)</h2>
    <span class="chips" style="margin:0">{% for k, n in (('A','A'),('B','B'),('calosc','Całość')) %}<a href="/analiza?eks={{ k }}&widok={{ widok }}&bm={{ bm }}#ekspozycja" class="{{ 'on' if eks == k else '' }}">{{ n }}</a>{% endfor %}</span></div>
  <div class="row" style="justify-content:space-between;margin:8px 0">
    <span class="chips" style="margin:0">{% for k, n in (('spolki','Spółki'),('kraje','Kraje'),('waluty','Waluty'),('sektory','Sektory')) %}<a href="{{ q }}&widok={{ k }}#ekspozycja" class="{{ 'on' if widok == k else '' }}">{{ n }}</a>{% endfor %}</span>
    <span class="dim">wartość pozycji {{ ek.suma|zl }} zł = znany skład {{ (ek.suma - ek.nieznane)|zl }} zł ({{ ('%.1f'|format((ek.suma - ek.nieznane) / ek.suma * 100 if ek.suma else 0))|replace('.', ',') }}%) + reszta / nieznane {{ ek.nieznane|zl }} zł</span></div>
  <div class="ek2">
  <div>
  {% if widok == 'spolki' %}
  {% set mx = (ek.top[0].lacznie if ek.top else 1) %}
  <table><tr><th>#</th><th>Spółka</th><th class="num">Łącznie</th><th class="num">%</th><th class="num hm">Bezpośr.</th><th class="hm">Przez ETF</th><th class="hm" style="width:20%"></th></tr>
  {% for e in ek.top %}<tr><td class="dim">{{ loop.index }}</td><td>{% if e.isin %}<b title="ISIN {{ e.isin }}">{{ e.etykieta[:34] }}</b>{% elif e.etykieta != e.ticker %}<b title="ticker {{ e.ticker }}">{{ e.etykieta[:34] }}</b>{% if e.ticker_yahoo %} <span class="dim hm">{{ e.ticker_yahoo }}</span>{% endif %}{% else %}<b>{{ e.ticker }}</b> <span class="dim hm">{{ e.nazwa[:28] }}</span>{% endif %}</td>
    <td class="num">{{ e.lacznie|zl }}</td><td class="num">{{ ('%.1f'|format(e.pct))|replace('.', ',') }}%</td>
    <td class="num hm">{{ e.bezposrednio|zl if e.bezposrednio else '—' }}</td>
    <td class="dim hm" style="font-size:12px">{% for n, v in e.etf %}{{ n }} {{ v|zl }}{{ ' · ' if not loop.last }}{% endfor %}</td>
    <td class="hm"><div class="ekbar"><i style="width:{{ e.bezposrednio / mx * 100 }}%"></i><b style="left:{{ e.bezposrednio / mx * 100 }}%;width:{{ (e.lacznie - e.bezposrednio) / mx * 100 }}%"></b></div></td></tr>{% endfor %}
  {% if ek.pozostale.n %}<tr><td></td><td class="dim">pozostałe znane ({{ ek.pozostale.n }} spółek)</td><td class="num">{{ ek.pozostale.wartosc|zl }}</td><td class="num">{{ ('%.1f'|format(ek.pozostale.wartosc / ek.suma * 100 if ek.suma else 0))|replace('.', ',') }}%</td><td class="hm"></td><td class="hm"></td><td class="hm"></td></tr>{% endif %}
  <tr><td></td><td class="dim">reszta / nieznane</td><td class="num">{{ ek.nieznane|zl }}</td><td class="num">{{ ('%.1f'|format(ek.nieznane / ek.suma * 100 if ek.suma else 0))|replace('.', ',') }}%</td><td class="hm"></td><td class="dim hm" style="font-size:12px">część ETF bez znanego składu</td><td class="hm"></td></tr>
  </table>
  <div class="dim" style="font-size:12px"><span class="sw1"></span>bezpośrednio <span class="sw2"></span>przez ETF</div>
  {% else %}{% set lista = ek[widok] %}{% set mx = ([lista[0].wartosc] if lista else [1])|max %}
  <table><tr><th>{{ {'kraje':'Kraj','waluty':'Waluta','sektory':'Sektor'}[widok] }}</th><th class="num">Wartość</th><th class="num">%</th><th style="width:35%"></th></tr>
  {% for x in lista %}<tr><td class="{{ 'dim' if x.nazwa == 'nieznane' else '' }}">{{ x.nazwa }}</td><td class="num">{{ x.wartosc|zl }}</td><td class="num">{{ ('%.1f'|format(x.pct))|replace('.', ',') }}%</td>
    <td><div class="ekbar"><b style="left:0;width:{{ [x.wartosc / mx * 100, 100]|min }}%;{{ 'opacity:.45' if x.nazwa == 'nieznane' else '' }}"></b></div></td></tr>{% endfor %}
  </table>{% endif %}
  </div>
  <div>
  <h3 style="font-size:14px;margin:0 0 6px">Pokrycie składów ETF</h3>
  <table><tr><th>ETF</th><th class="num hm">Wartość</th><th class="num">Pokrycie</th><th>Skład</th></tr>
  {% for p in ek.pokrycie %}<tr><td><b>{{ p.symbol }}</b>{% if eks == 'calosc' %} <span class="dim">{{ p.konto }}</span>{% endif %}</td><td class="num hm">{{ p.wartosc|zl }}</td>
    <td class="num {{ 'bad' if p.pokrycie < 50 else '' }}">{{ ('%.1f'|format(p.pokrycie))|replace('.', ',') }}%</td>
    <td style="font-size:12px;white-space:normal">{% if p.reczny %}<span class="pill">skład ręczny</span>{% elif p.przyblizenie %}<span class="pill" title="Skład VWCE przybliżony pełnym składem iShares MSCI ACWI (ustawienie sklad_vwce_acwi)">przybliżenie (ACWI)</span>{% else %}<span class="dim">{{ p.zrodlo }}</span>{% endif %}
      <span class="dim">{{ p.pozycji }} poz.{% if p.data %} · {{ p.data[8:10] }}.{{ p.data[5:7] }}{% endif %}</span></td></tr>
  {% else %}<tr><td colspan="4" class="dim">Brak ETF w {{ eks }}.</td></tr>{% endfor %}</table>
  <p class="dim" style="font-size:12px">Ekspozycja = wartość pozycji ETF × waga składnika + pozycje bezpośrednie. Część ETF poza znanym składem i składniki bez kraju / sektora / waluty → „nieznane”. Składy ręczne ETF z GPW: Polska, PLN.</p>
  </div></div>
</section>

<section id="benchmark"><div class="row" style="justify-content:space-between;align-items:center"><h2 style="margin:0">Portfel vs benchmark</h2>
  <span class="chips" style="margin:0"><a href="/analiza?eks={{ eks }}&widok={{ widok }}&bm=wartosc#benchmark" class="{{ 'on' if bm == 'wartosc' else '' }}">Wartość zł</a><a href="/analiza?eks={{ eks }}&widok={{ widok }}&bm=indeks#benchmark" class="{{ 'on' if bm == 'indeks' else '' }}">Indeks (start = 100)</a></span></div>
  {% macro p2(v) %}{{ ('%+.1f'|format(v))|replace('.', ',')|replace('-', '−') ~ '%' if v is not none else '—' }}{% endmacro %}
  <div class="ek2b">
  {% for k in ('A', 'B') %}{% set b = bench[k] %}{% set sh = s.shadow_a if k == 'A' else s.shadow %}
  <div><div class="row" style="justify-content:space-between;margin:8px 0 2px"><b>{{ k }} vs {{ b.sym }}</b>
      <span class="dim" style="font-size:12px">{% if b.punkty %}od {{ b.punkty[0].date[8:10] }}.{{ b.punkty[0].date[5:7] }}{% endif %}{% if k == 'B' %} · wyrok {{ ust.get('wyrok_b','') }}{% endif %}</span></div>
    {% if b.wykres %}{% set c = b.wykres %}
    <div style="font-size:13px;margin-bottom:4px">TWR {{ k }}{% if b.twr_od %} od {{ b.twr_od[8:10] }}.{{ b.twr_od[5:7] }}{% endif %} <b>{{ p2(b.twr) }}</b> · kurs {{ b.sym }} w PLN <b>{{ p2(b.twr_b) }}</b> · różnica <b class="{{ 'ok' if b.twr >= b.twr_b else 'bad' }}">{{ ('%+.1f'|format(b.twr - b.twr_b))|replace('.', ',')|replace('-', '−') }} pkt</b>
      {% if sh.wynik_pct is not none %} · XIRR {{ k }} {{ p2(s.xirr[k]) if s.xirr[k] is number else s.xirr[k] }} / {{ b.sym }} {{ p2(sh.xirr) if sh.xirr is number else sh.xirr }}{% endif %}</div>
    <div class="chart-wrap" data-points='{{ c.points|tojson }}'>
      <svg viewBox="0 0 {{ c.w }} {{ c.h }}" width="100%" role="img" aria-label="{{ k }} i benchmark {{ b.sym }}">
        {% for t in c.yticks %}<line class="grid-line" x1="{{ c.L }}" x2="{{ c.R }}" y1="{{ t.y }}" y2="{{ t.y }}"/><text class="axis" x="{{ c.L - 6 }}" y="{{ t.y + 4 }}" text-anchor="end">{{ t.label }}</text>{% endfor %}
        {% for t in c.xticks %}<text class="axis" x="{{ t.x }}" y="{{ c.B + 18 }}" text-anchor="middle">{{ t.label }}</text>{% endfor %}
        <path d="{{ c.p2 }}" fill="none" stroke="var(--cost-line)" stroke-width="1.8" stroke-dasharray="5 4"/>
        <path d="{{ c.p1 }}" fill="none" stroke="var(--acc)" stroke-width="2.2" stroke-linejoin="round"/>
        <line class="cross" y1="{{ c.T }}" y2="{{ c.B }}" stroke="var(--dim)" stroke-dasharray="2 3" style="display:none"/>
        <circle class="hover-dot" r="4" fill="var(--acc)" style="display:none"/>
        <rect x="{{ c.L }}" y="{{ c.T }}" width="{{ c.R - c.L }}" height="{{ c.B - c.T }}" fill="transparent"/></svg>
      <div class="chart-tip"></div></div>
    <div class="dim" style="font-size:12px">{% if bm == 'wartosc' %}<span class="sw1"></span>{{ k }} (zł) <span class="sw3"></span>{{ b.sym }} przy tych samych wpłatach (zł){% else %}Indeks: <span class="sw1"></span>{{ k }} — TWR (bez wpływu wpłat); <span class="sw3"></span>{{ b.sym }} — zmiana kursu w PLN (kupuj i trzymaj){% endif %}</div>
    {% set d = b.roznica %}<div class="dim" style="font-size:12px;margin-top:6px">Różnica portfel − benchmark (pkt %)</div>
    <div class="chart-wrap" data-points='{{ d.points|tojson }}'>
      <svg viewBox="0 0 {{ d.w }} {{ d.h }}" width="100%" role="img" aria-label="Różnica {{ k }} − {{ b.sym }}">
        {% for t in d.yticks %}<text class="axis" x="{{ d.L - 6 }}" y="{{ t.y + 4 }}" text-anchor="end">{{ t.label }}</text>{% endfor %}
        {% for t in d.xticks %}<text class="axis" x="{{ t.x }}" y="{{ d.B + 16 }}" text-anchor="middle">{{ t.label }}</text>{% endfor %}
        <line x1="{{ d.L }}" x2="{{ d.R }}" y1="{{ d.y0 }}" y2="{{ d.y0 }}" stroke="var(--line)"/>
        <path d="{{ d.obszar }}" fill="{{ 'var(--up)' if d.ostatnia >= 0 else 'var(--down)' }}" fill-opacity=".14"/>
        <path d="{{ d.linia }}" fill="none" stroke="{{ 'var(--up)' if d.ostatnia >= 0 else 'var(--down)' }}" stroke-width="1.8"/>
        <line class="cross" y1="{{ d.T }}" y2="{{ d.B }}" stroke="var(--dim)" stroke-dasharray="2 3" style="display:none"/>
        <circle class="hover-dot" r="4" fill="var(--acc)" style="display:none"/>
        <rect x="{{ d.L }}" y="{{ d.T }}" width="{{ d.R - d.L }}" height="{{ d.B - d.T }}" fill="transparent"/></svg>
      <div class="chart-tip"></div></div>
    {% else %}<p class="dim">Brak danych do wykresu {{ k }} vs {{ b.sym }}{% if b.braki %} — brak kursów benchmarku z dni: {{ b.braki[:5]|join(', ') }}{% if b.braki|length > 5 %} …{% endif %} (uzupełni wycena workera){% endif %}.</p>{% endif %}
  </div>{% endfor %}
  </div>
  <p class="dim" style="margin:10px 0 0">Indeks: konto — TWR (wpłata na początku dnia, bez wpływu wpłat; ten sam co w kaflu i w tabeli stóp); benchmark — zmiana kursu w PLN (kupuj i trzymaj). Różnica w punktach procentowych.
    Widok zł: benchmark przy tych samych wpłatach — wpłaty i wypłaty konta (przeniesienie A→B 11.09 po wartości z IBKR) zainwestowane w benchmark po zamknięciu dnia; to samo daje wynik „(te same wpłaty)” w kaflach.</p>
</section>

<section><h2>Struktura całości (bez gotówki)</h2>
  {% set nazwy = {'kotwica':'Kotwica (VWCE)','teza':'Teza (IWVL + IWMO + ZPRV)','polska':'Polska','sp':'Koszyk B'} %}
  {% for x in s.struktura %}{% set mx = [70, x.udzial + 5]|max %}
  <div class="row" style="justify-content:space-between"><span>{{ nazwy[x.grupa] }}</span>
    <span><b class="{{ '' if x.ok else 'bad' }}">{{ '%.1f'|format(x.udzial) }}%</b> <span class="pill {{ 'ok' if x.ok else 'bad' }}">{{ '≥' if x.typ=='min' else '≤' }} {{ x.granica|round(1) }}%</span></span></div>
  <div class="bar"><i class="{{ '' if x.ok else 'bad' }}" style="width:{{ x.udzial / mx * 100 }}%"></i><b style="left:{{ x.granica / mx * 100 }}%"></b></div>
  {% endfor %}
</section>

<section><h2>Portfel A — koszyki vs pasma</h2>
  <p class="dim">Udział A = udział w wartości zainwestowanej A ({{ s.gotowka.zainwestowane_A|zl }} zł){% if s.gotowka.A >= 1 %} · gotówka do rozdziału {{ s.gotowka.A|zl }} zł ({{ ('%.1f'|format(s.gotowka.udzial_A))|replace('.', ',') }}% A) poza pasmami{% endif %}.</p>
  <table><tr><th>Koszyk</th><th class="num">Wartość</th><th class="num">Udział A</th><th class="num">Cel</th><th class="num">Pasmo</th><th>Położenie</th><th>Stan</th><th class="num">Luka</th></tr>
  {% for k in s.koszyki %}{% set mx = [30, k.pasmo[1] + 8, k.udzial + 5]|max %}
  <tr><td><b>{{ k.kod }}</b> <span class="dim">{{ k.nazwa }}</span></td><td class="num">{{ k.wartosc|zl }}</td><td class="num">{{ '%.1f'|format(k.udzial) }}%</td>
    <td class="num">{{ k.cel|round(2) }}%</td><td class="num">{{ k.pasmo[0]|round(2) }}–{{ k.pasmo[1]|round(2) }}%</td>
    <td><div class="band"><div class="z" style="left:{{ k.pasmo[0]/mx*100 }}%;width:{{ (k.pasmo[1]-k.pasmo[0])/mx*100 }}%"></div>
      <div class="t" style="left:{{ k.cel/mx*100 }}%"></div><div class="c {{ '' if k.stan=='w paśmie' else 'bad' }}" style="left:{{ [k.udzial/mx*100,100]|min }}%"></div></div></td>
    <td><span class="pill {{ 'ok' if k.stan=='w paśmie' else 'bad' }}">{{ k.stan }}</span>{% if k.poza_od %}<div class="dim">od {{ k.poza_od }} · {{ k.miesiecy }} mies.{% if k.wyjatek_sprzedazy %} · A4: sprzedaż dopuszczalna{% endif %}</div>{% endif %}</td>
    <td class="num {{ 'ok' if k.luka >= 0 else 'bad' }}">{{ '%+.0f'|format(k.luka) }}</td></tr>
  {% endfor %}</table>
</section>

<section id="alerty"><h2>Alerty — pełna lista</h2>
  {% for a in s.alerty %}<div class="al {{ a.poziom }}">{{ a.tekst }}</div>{% else %}<p class="dim">Brak alertów.</p>{% endfor %}
</section>

{% endblock %}"""

PULPIT = """{% extends "base" %}{% block body %}""" + STYL + """
{% set w = s.w %}
<section><h2>Limit B — odmrożenie zakupów</h2>{% set lb = s.limit_b %}
  <div class="grid">
    <div><div class="val {{ 'bad' if lb.zamrozony else 'ok' }}">{{ '%.1f'|format(lb.udzial) }}%</div><div class="dim">udział B w zainwestowanej całości (bez gotówki; limit {{ lb.limit|round(1) }}%) · {{ 'zamrożony' if lb.zamrozony else 'wolny limit ' ~ (lb.wolny|zl) ~ ' zł' }}</div></div>
    <div><div class="val">{{ lb.prog|zl }} zł</div><div class="dim">odmrożenie przy zainwestowanej całości · brakuje {{ lb.brakuje|zl }} zł (~{{ lb.wplat }} wpłat)</div></div>
  </div>
  <table style="margin-top:10px"><tr><th>Po sprzedaży</th><th class="num">Wartość</th><th class="num">Wynik</th><th class="num">B po</th><th class="num">Wolny limit</th></tr>
  {% for c in lb.scenariusze %}<tr><td>{{ c.symbol }}</td><td class="num">{{ c.wartosc|zl }}</td>
    <td class="num {{ 'ok' if (c.pnl_pct or 0) >= 0 else 'bad' }}">{{ '%+.2f'|format(c.pnl_pct) ~ '%' if c.pnl_pct is not none else '—' }}</td>
    <td class="num">{{ '%.1f'|format(c.udzial_po) }}%</td><td class="num">{{ c.wolny_po|zl }}</td></tr>{% endfor %}
  </table><p class="dim">Do sprzedaży pod nowe wejście: pozycja najsłabsza względem benchmarku (B4).</p>
</section>

<section id="wplata"><h2>Następna wpłata (A3)</h2>
  <form method="get" action="/plan#wplata" class="row">
    <label class="dim" for="kw_n">nowa wpłata (jeszcze nie na koncie)</label>
    <input id="kw_n" type="number" name="kwota" value="{{ '%.0f'|format(kwota) }}" step="100" style="width:140px"> zł
    <input type="hidden" name="gotowka" value="0">
    <label class="dim"><input type="checkbox" name="gotowka" value="1" {{ 'checked' if gotowka else '' }}> dolicz gotówkę A ({{ s.gotowka.A|zl }} zł)</label>
    <button>Rozdziel</button></form>
  {% if kalk %}{% if kalk.ostrzezenie %}<div class="al ostrzezenie" style="margin-top:10px">{{ kalk.ostrzezenie }}</div>{% endif %}
  <table style="margin-top:10px"><tr><th>Koszyk</th><th class="num">Kwota</th><th>Instrument</th><th class="num">Udział przed → po</th><th>Po</th></tr>
  {% for p in kalk.przydzial %}<tr><td><b>{{ p.kod }}</b></td><td class="num">{{ p.kwota|zl }}</td>
    <td>{% for i in p.instrumenty %}{{ i.symbol }}: {{ '%.2f'|format(i.kwota_waluta) }} {{ i.waluta }} ≈ {{ i.sztuk }} szt.<br>{% else %}—{% endfor %}</td>
    <td class="num">{{ '%.1f'|format(p.udzial_przed) }}% → <b>{{ '%.1f'|format(p.udzial_po) }}%</b></td>
    <td><span class="pill {{ 'ok' if p.stan_po=='w paśmie' else 'bad' }}">{{ p.stan_po }}</span></td></tr>{% endfor %}
  </table>
  <p class="dim">Do rozdziału {{ kalk.do_rozdzialu|zl }} zł (w tym gotówka {{ kalk.gotowka|zl }} zł) · {{ kalk.kroki|join(' · ') }} · udziały od wartości zainwestowanej A; sztuki zaokrąglone w dół.</p>
  {% endif %}
</section>

<section id="suwak"><h2>Suwak celów — podgląd nowych wag</h2>
  <p class="dim">Przesuwasz wagi i widzisz pasma oraz naruszenia S1–S3 bez zapisu. Zapis zmienia plan — zgodnie z zasadą A5 wagi zmienia się w styczniu; poza styczniem wymaga potwierdzenia.</p>
  <form method="post" action="/pulpit/plan" id="plan">
  {% for k in kosz_all %}
    <div class="sl"><b>{{ k.kod }}</b><input type="range" min="0" max="80" step="0.5" name="cel_{{ k.kod }}" value="{{ k.cel or 0 }}">
      <span class="num" id="v_{{ k.kod }}">{{ k.cel or 0 }}%</span><span class="pb dim" id="p_{{ k.kod }}"></span></div>
  {% endfor %}
    <div id="symw" class="dim" style="margin:8px 0"></div>
    <div class="grid"><div class="field"><label for="opis">Opis zmiany (do historii)</label><input id="opis" name="opis"></div>
      <div class="field"><label><input type="checkbox" name="wymus" value="1"> potwierdzam zmianę poza oknem stycznia</label></div></div>
    <p class="row"><button>Zapisz jako plan</button><a href="/plan">przywróć</a></p>
  </form>
  {% if zmiany %}<details class="fold"><summary><span class="dim">Historia zmian planu ({{ zmiany|length }})</span></summary>
    <table>{% for z in zmiany %}<tr><td class="dim">{{ z.data }}</td><td>{{ z.opis }}</td><td class="dim">{{ z.cele_json }}</td></tr>{% endfor %}</table></details>{% endif %}
  <script>
  (function(){
    var f=document.getElementById('plan'),t;
    function licz(){
      var c={};f.querySelectorAll('input[type=range]').forEach(function(i){c[i.name.slice(4)]=+i.value;document.getElementById('v_'+i.name.slice(4)).textContent=i.value+'%'});
      fetch('/pulpit/symulacja',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cele:c})})
       .then(function(r){return r.json()}).then(function(r){
         Object.keys(r.pasma).forEach(function(k){var e=document.getElementById('p_'+k);if(e)e.textContent=r.cele[k]>0?('pasmo '+r.pasma[k][0]+'–'+r.pasma[k][1]+'%'):''});
         var h='Suma <b class="'+(Math.abs(r.suma-100)>0.01?'bad':'ok')+'">'+r.suma+'%</b>';
         r.warianty.forEach(function(v){h+=' · '+v.wariant+': kotwica '+v.kotwica+'%, teza '+v.teza+'%, Polska '+v.polska+'% całości'});
         r.naruszenia.forEach(function(n){h+='<div class="al krytyczny">'+n+'</div>'});
         document.getElementById('symw').innerHTML=h;});
    }
    f.addEventListener('input',function(){clearTimeout(t);t=setTimeout(licz,150)});licz();
  })();
  </script>
</section>

{% endblock %}"""

KAND = """{% extends "base" %}{% block body %}""" + STYL + """
<style>
 .kt2 td{white-space:nowrap} .kt2 td.w{white-space:normal;max-width:380px} .kt2 tr[data-id]{cursor:pointer} .kt2 tr[data-id]:hover{background:var(--sub)}
 .st-tak{font-size:11px;padding:1px 7px;border-radius:8px;background:var(--ink-bg);color:var(--ink-tx)}
 .st-x{font-size:11px;padding:1px 7px;border-radius:8px;border:1px solid var(--btn-line)}
 .scrim{position:fixed;inset:0;background:rgba(23,25,29,.28);z-index:60}
 .kpanel{position:fixed;top:0;right:0;height:100vh;width:min(640px,100vw);background:var(--panel);border-left:1px solid var(--line);z-index:61;
   padding:28px 32px;box-sizing:border-box;overflow:auto;display:flex;flex-direction:column;gap:12px}
 .hid{display:none!important}
 @media (max-width:720px){.kt2 .hm{display:none}}
</style>
<section>
  <div class="chips">
    <a href="/kandydaci?status=wszystko" class="{{ 'on' if status=='wszystko' else '' }}">Wszystko {{ razem }}</a>
    {% for k,n in statusy %}<a href="/kandydaci?status={{ k }}" class="{{ 'on' if status==k else '' }}">{{ n }} {{ licz[k] }}</a>{% endfor %}
  </div>
  <form method="get" action="/kandydaci" class="row">
    <input type="hidden" name="status" value="{{ status }}">
    <input name="q" value="{{ q }}" placeholder="szukaj: ticker, nazwa, teza" style="flex:1;min-width:160px">
    <select name="grupa" style="width:auto"><option value="">wszystkie grupy</option>{% for g in grupy %}<option {{ 'selected' if g==grupa else '' }}>{{ g }}</option>{% endfor %}</select>
    <button>Filtruj</button><a href="/kandydaci/nowy?status={{ status if status in status_n else 'analiza' }}">+ Dodaj kandydata</a>
  </form>
  {% if status=='tak' and lb.zamrozony %}<div class="al info" style="margin-top:10px">B zamrożony — pozycje TAK czekają na limit: sprzedaż w B albo zainwestowana całość ≥ {{ lb.prog|zl }} zł.</div>{% endif %}
</section>
<section>
{% if lista %}
<table class="kt2">
  <thead><tr><th>Ticker</th><th class="hm">Grupa</th><th class="num">Kurs</th><th class="num">Strefa wejścia</th><th class="num">Odległość</th><th class="hm">Katalizator</th><th class="hm">SL</th></tr></thead>
  <tbody>
  {% for x in lista %}<tr data-id="{{ x.id }}">
    <td><b>{{ x.ticker }}</b> {% if x.status=='tak' %}<span class="st-tak">TAK</span>{% else %}<span class="st-x">{{ status_n[x.status] }}{{ ' · ' ~ x.priorytet if x.status=='watch' and x.priorytet else '' }}{{ ' · ' ~ x.typ_odrzucenia if x.status=='nie' and x.typ_odrzucenia else '' }}</span>{% endif %}
      <div class="dim">{{ x.nazwa or '' }}</div></td>
    <td class="hm dim">{{ x.grupa or '' }}</td>
    <td class="num">{% if x.cena is not none %}{{ '%.2f'|format(x.cena) }} {{ x.waluta_k }}<div class="dim">{{ x.kurs_ts }}</div>{% else %}<span class="dim">{{ x.kurs_tekst or '—' }}</span>{% endif %}</td>
    <td class="num">{% if x.wejscie_od is not none or x.wejscie_do is not none %}{{ x.wejscie_od|zl1 if x.wejscie_od is not none else '' }}–{{ x.wejscie_do|zl1 if x.wejscie_do is not none else '' }}{% else %}<span class="dim">{{ (x.wejscie or '—')[:24] }}</span>{% endif %}</td>
    <td class="num">{% if x.w_strefie %}<span class="pill ok">w strefie</span>{% elif x.odl is not none %}<span class="{{ 'bad' if x.odl > 0 else '' }}">{{ '%+.1f'|format(x.odl)|replace('.', ',') }}%</span>{% else %}<span class="dim">—</span>{% endif %}</td>
    <td class="hm w">{{ (x.katalizator or '')[:80] }}</td>
    <td class="hm dim">{{ (x.sl or '')[:16] }}</td></tr>{% endfor %}
  </tbody></table>
<p class="dim" style="margin:8px 0 0">Kolejność: w strefie → najbliżej strefy → bez danych. Kliknij wiersz, aby zobaczyć szczegóły.</p>
{% else %}<p class="dim">Brak pozycji.</p>{% endif %}
</section>
{% for x in lista %}<template id="k-{{ x.id }}">
  <div class="row" style="justify-content:space-between"><h2 style="margin:0;font-size:20px;color:var(--tx)">{{ x.ticker }} <span class="dim" style="font-size:14px">{{ x.nazwa or '' }}</span></h2>
    <button type="button" data-close aria-label="Zamknij panel" style="width:40px;height:40px;padding:0">✕</button></div>
  <div class="row"><span class="pill">{{ x.rynek or '' }}</span>{% if x.grupa %}<span class="pill acc">{{ x.grupa }}</span>{% endif %}
    <span class="pill {{ 'ok' if x.status=='tak' else 'bad' if x.status=='nie' else '' }}">{{ status_n[x.status] }}</span>
    {% if x.cena is not none %}<span class="num">{{ '%.2f'|format(x.cena) }} {{ x.waluta_k }}</span> <span class="dim">({{ x.kurs_ts }})</span>{% endif %}</div>
  <div class="kg">
    {% if x.wejscie or x.wejscie_do %}<div><span>Wejście</span>{{ x.wejscie or '' }} {% if x.wejscie_od is not none or x.wejscie_do is not none %}({{ x.wejscie_od|zl1 if x.wejscie_od is not none else '' }}–{{ x.wejscie_do|zl1 if x.wejscie_do is not none else '' }}){% endif %}</div>{% endif %}
    {% if x.sl %}<div><span>Stop loss</span>{{ x.sl }}</div>{% endif %}{% if x.tp %}<div><span>Take profit</span>{{ x.tp }}</div>{% endif %}
    {% if x.katalizator %}<div><span>Katalizator</span>{{ x.katalizator }}</div>{% endif %}</div>
  {% if x.warunek %}<div class="kt"><span>{{ 'Wyzwalacz' if x.status=='watch' else 'Warunek' }}</span>{{ x.warunek }}</div>{% endif %}
  {% if x.teza %}<div class="kt"><span>{{ 'Powód' if x.status=='nie' else 'Teza' }}</span>{{ x.teza }}</div>{% endif %}
  {% if x.uwaga %}<div class="kt"><span>Uwaga</span>{{ x.uwaga }}</div>{% endif %}
  {% if x.powrot %}<div class="kt"><span>Powrót</span>{{ x.powrot }}</div>{% endif %}
  <div class="row" style="justify-content:space-between;margin-top:8px"><a class="btn" href="/kandydaci/{{ x.id }}">Edytuj / zmień status / historia →</a>
    <span class="dim">{{ x.zrodlo or '' }} · zm. {{ (x.zmieniono or '')[:16] }}</span></div>
</template>{% endfor %}
<div class="scrim hid" id="kscrim"></div><aside class="kpanel hid" id="kpanel" role="dialog" aria-label="Szczegóły kandydata"></aside>
<script>
(function(){
  var p=document.getElementById('kpanel'), sc=document.getElementById('kscrim');
  function zamknij(){p.classList.add('hid');sc.classList.add('hid');}
  sc.addEventListener('click',zamknij); document.addEventListener('keydown',function(e){if(e.key==='Escape')zamknij()});
  document.querySelectorAll('tr[data-id]').forEach(function(r){r.addEventListener('click',function(){
    var t=document.getElementById('k-'+r.dataset.id); p.innerHTML=''; p.appendChild(t.content.cloneNode(true));
    p.querySelector('[data-close]').addEventListener('click',zamknij); p.classList.remove('hid'); sc.classList.remove('hid');
  });});
})();
</script>
{% endblock %}"""

FORM = """{% extends "base" %}{% block body %}""" + STYL + """
<section>
  <p class="dim"><a href="{{ wstecz }}">← wróć</a></p>
  {% if info %}<div class="al info">{{ info }}</div>{% endif %}
  <form method="post" action="{{ akcja }}">
    <div class="grid">
    {% for p in pola %}
      <div class="field" {% if p.typ=='textarea' %}style="grid-column:1/-1"{% endif %}><label for="f_{{ p.name }}">{{ p.label }}</label>
      {% if p.readonly %}<div style="white-space:pre-wrap">{{ p.value or '—' }}</div>
      {% elif p.typ=='textarea' %}<textarea id="f_{{ p.name }}" name="{{ p.name }}" style="min-height:70px">{{ p.value }}</textarea>
      {% elif p.typ=='select' %}<select id="f_{{ p.name }}" name="{{ p.name }}">{% for k,n in p.opcje %}<option value="{{ k }}" {{ 'selected' if k==p.value else '' }}>{{ n }}</option>{% endfor %}</select>
      {% else %}<input id="f_{{ p.name }}" name="{{ p.name }}" type="{{ 'number' if p.typ=='number' else 'date' if p.typ=='date' else 'text' }}" step="any" value="{{ p.value }}">{% endif %}
      </div>
    {% endfor %}
    {% if nowy and kandydat is not none %}
      <div class="field"><label>Status</label><select name="status">{% for k,n in opcje.statusy %}<option value="{{ k }}" {{ 'selected' if k==kandydat.status else '' }}>{{ n }}</option>{% endfor %}</select></div>
      <div class="field"><label>Typ odrzucenia</label><select name="typ_odrzucenia">{% for k,n in opcje.typ_odrz %}<option value="{{ k }}">{{ n }}</option>{% endfor %}</select></div>
      <div class="field"><label>Priorytet (watch)</label><select name="priorytet">{% for k,n in opcje.priorytet %}<option value="{{ k }}">{{ n }}</option>{% endfor %}</select></div>
    {% endif %}
    </div>
    <p class="row"><button>Zapisz</button></p>
  </form>
</section>
{% if kandydat is not none and not nowy %}
<section><h2>Zmień status — obecnie: {{ status_n[kandydat.status] }}</h2>
  <form method="post" action="/kandydaci/{{ kandydat.id }}/status">
    <div class="grid">
      <div class="field"><label>Nowy status</label><select name="status">{% for k,n in opcje.statusy %}<option value="{{ k }}" {{ 'selected' if k==kandydat.status else '' }}>{{ n }}</option>{% endfor %}</select></div>
      <div class="field"><label>Typ odrzucenia (NIE)</label><select name="typ_odrzucenia">{% for k,n in opcje.typ_odrz %}<option value="{{ k }}" {{ 'selected' if k==(kandydat.typ_odrzucenia or '') else '' }}>{{ n }}</option>{% endfor %}</select></div>
      <div class="field"><label>Priorytet (watchlist)</label><select name="priorytet">{% for k,n in opcje.priorytet %}<option value="{{ k }}" {{ 'selected' if k==(kandydat.priorytet or '') else '' }}>{{ n }}</option>{% endfor %}</select></div>
      <div class="field" style="grid-column:1/-1"><label>Warunek powrotu (opcjonalnie — nadpisuje obecny)</label><textarea name="powrot"></textarea></div>
      <div class="field" style="grid-column:1/-1"><label>Uzasadnienie * (trafia do historii)</label><textarea name="powod" required></textarea></div>
    </div>
    <p><button>Zmień status</button></p>
  </form>
</section>
<section><h2>Historia statusu</h2>
  <table><tr><th>Data</th><th>Z</th><th>Na</th><th>Uzasadnienie</th></tr>
  {% for h in hist %}<tr><td class="dim">{{ h.data[:16] }}</td><td>{{ status_n.get(h.z_statusu, '—') }}</td><td>{{ status_n.get(h.na_status) }}</td><td>{{ h.powod or '' }}</td></tr>{% endfor %}</table>
</section>
{% endif %}
{% if usun %}<section><form method="post" action="{{ usun }}" onsubmit="return confirm('Usunąć na stałe? (kopia bazy zostaje w data/backup/edycje)')">
  <button class="warn">Usuń</button> <span class="dim">{{ 'Zamiast usuwać, zwykle lepiej zmienić status na NIE — zostaje ślad.' if kandydat is not none else '' }}</span></form></section>{% endif %}
{% endblock %}"""

ZASADY = """{% extends "base" %}{% block body %}""" + STYL + """
<section style="position:sticky;top:64px;z-index:5">
  <div class="row" style="justify-content:space-between">
    <div class="chips" style="margin:0">{% for k in kategorie %}<a href="#kat-{{ loop.index }}">{{ k or 'inne' }}</a>{% endfor %}</div>
    <div class="chips" style="margin:0" id="zst"><a href="#" class="on" data-st="">wszystkie</a>{% for k,n in zstatus.items() %}<a href="#" data-st="{{ k }}">{{ n }}</a>{% endfor %}</div>
    <a href="/zasady/nowy">+ Dodaj zasadę</a>
  </div>
</section>
<section>
{% for k in kategorie %}
  <h2 style="margin-top:14px" id="kat-{{ loop.index }}">{{ k }}</h2>
  <table>{% for z in zasady if z.kategoria == k %}
    <tr data-st="{{ z.status }}"><td style="width:44px"><b>{{ z.kod }}</b></td>
      <td><a href="/zasady/{{ z.id }}"><b>{{ z.tytul }}</b></a><div class="dim">{{ z.tresc or '' }}</div>{% if z.notatka %}<div class="dim"><i>{{ z.notatka }}</i></div>{% endif %}</td>
      <td style="width:110px"><span class="pill {{ 'ok' if z.status=='obowiazuje' else 'bad' if z.status=='uchylona' else 'acc' }}">{{ zstatus[z.status] }}</span></td>
      <td class="dim" style="width:140px">{{ z.zrodlo or '' }}</td></tr>{% endfor %}</table>
{% endfor %}</section>
<script>
document.querySelectorAll('#zst a').forEach(function(a){a.addEventListener('click',function(e){e.preventDefault();
  document.querySelectorAll('#zst a').forEach(function(x){x.classList.remove('on')}); a.classList.add('on');
  var st=a.dataset.st; document.querySelectorAll('tr[data-st]').forEach(function(r){r.style.display=(!st||r.dataset.st===st)?'':'none'});
});});
</script>{% endblock %}"""

DZIENNIK = """{% extends "base" %}{% block body %}""" + STYL + """
<section><p class="dim">Jeden wpis = jedna transakcja albo świadoma decyzja o niedziałaniu. Zapis PRZED wynikiem; po 30 i 90 dniach uzupełniasz przegląd bez edycji tezy.</p>
  <p class="row"><a href="/dziennik/nowy">+ Nowy wpis</a></p>
{% for x in wpisy %}
  <div class="kand">
    <div class="row" style="justify-content:space-between"><span><b>{{ x.data }}</b> <span class="pill acc">{{ x.akcja }}</span>
      {% if x.konto %}<span class="pill">{{ x.konto }}</span>{% endif %} <b>{{ x.ticker or '' }}</b> {{ x.kwota or '' }}</span>
      {% if x.zalegle %}<span class="pill bad">do uzupełnienia: {{ x.zalegle }}</span>{% endif %}</div>
    <div class="kt"><span>Teza</span>{{ x.teza or '' }}</div><div class="kt"><span>Unieważnia</span>{{ x.uniewaznia or '' }}</div>
    <div class="kt"><span>Alternatywa</span>{{ x.alternatywa or '' }}</div><div class="kt"><span>Emocje</span>{{ x.emocje or '' }}</div>
    {% if x.przeglad_30 %}<div class="kt"><span>30 dni</span>{{ x.przeglad_30 }} {{ x.wynik_30d or '' }}</div>{% endif %}
    {% if x.przeglad_90 %}<div class="kt"><span>90 dni</span>{{ x.przeglad_90 }} {{ x.wynik_90d or '' }}</div>{% endif %}
    <p style="margin:6px 0 0"><a href="/dziennik/{{ x.id }}">Uzupełnij przegląd →</a></p>
  </div>
{% else %}<p class="dim">Brak wpisów.</p>{% endfor %}</section>{% endblock %}"""

KAL = """{% extends "base" %}{% block body %}""" + STYL + """
{% if zal %}<section><h2>Zaległe ({{ zal|length }})</h2>
  <p class="dim">Nieodhaczone wpisy z przeszłości, które wymagają działania (plan, reguła, rewizja indeksu). Zostają tu i w porannym Telegramie, aż oznaczysz „zrobione”.</p>
  <table><tr><th>Data</th><th>Ticker</th><th>Opis</th><th>Typ</th><th></th></tr>
  {% for e in zal %}<tr><td style="white-space:nowrap" class="bad">{{ e.data }}</td><td><b>{{ e.ticker or '' }}</b></td>
    <td><a href="/kalendarz/{{ e.id }}">{{ e.opis }}</a></td><td><span class="pill">{{ e.typ or '' }}</span></td>
    <td><form method="post" action="/kalendarz/{{ e.id }}/zrobione" class="inline"><button>zrobione</button></form></td></tr>{% endfor %}
  </table>
</section>{% endif %}
<section><p class="row"><a href="/kalendarz/nowy">+ Dodaj wydarzenie</a>
  <a href="/kalendarz{{ '' if wszystko else '?wszystko=1' }}">{{ 'tylko nadchodzące' if wszystko else 'pokaż przeszłe i zrobione' }}</a></p>
  <table><tr><th>Data</th><th>Ticker</th><th>Opis</th><th>Typ</th><th></th></tr>
  {% for e in ev %}<tr class="{{ 'dim' if e.zrobione or e.data < dzis else '' }}"><td style="white-space:nowrap">{{ e.data }}{{ ' ~' if e.przyblizona else '' }}</td>
    <td><b>{{ e.ticker or '' }}</b></td><td><a href="/kalendarz/{{ e.id }}">{{ e.opis }}</a></td><td><span class="pill">{{ e.typ or '' }}</span></td>
    <td><form method="post" action="/kalendarz/{{ e.id }}/zrobione" class="inline"><button>{{ 'cofnij' if e.zrobione else 'zrobione' }}</button></form></td></tr>
  {% else %}<tr><td class="dim" colspan="5">Brak.</td></tr>{% endfor %}</table>
</section>{% endblock %}"""

SZABLONY = {"pf_pulpit": PULPIT, "pf_analiza": ANALIZA, "pf_kand": KAND, "pf_form": FORM, "pf_zasady": ZASADY, "pf_dziennik": DZIENNIK,
            "pf_kal": KAL}
