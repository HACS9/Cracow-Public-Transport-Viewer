"""Test przed wdrożeniem — na KOPII bazy, bez zapisu do prawdziwych danych i bez wysyłki Telegram.

Uruchamiany przez deploy.sh na NOWYM obrazie, zanim kontenery zostaną podmienione:
  docker compose run --rm --no-deps -T wallet-web python -m app.test_wdrozenia
Ręcznie (w działającym kontenerze):
  docker compose exec -T wallet-web python -m app.test_wdrozenia [--pelny]

Sprawdza: kopia bazy się otwiera; worker `stan` (wycena z bazy), `kontrola --dry-run` i `nav --dry-run`;
testy na sztucznych danych (app.test_c1, app.test_c1a, app.test_al, app.test_p, app.test_t, app.test_c2a, app.test_al2, app.test_al3); import IBKR Flex na sztucznym
raporcie (app.test_flex, osobna baza tymczasowa); panel na porcie testowym:
główne strony (200), stary adres ustawień (303 → /settings), API (przegląd, stan, kandydaci, kalkulator wpłaty).
--pelny dodatkowo: `wyniki --dry-run` i `wieczor --dry-run` (pobiera dane z sieci, trwa dłużej).
Kod wyjścia 0 = OK, 1 = co najmniej jeden błąd.
"""
from __future__ import annotations

import argparse
import base64
import http.cookiejar
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PORT = int(os.environ.get("WALLET_TEST_PORT", "18080"))
STRONY = ["/", "/analiza", "/plan", "/pulpit", "/kandydaci", "/kandydaci?status=wszystko", "/kalendarz", "/kalendarz?wszystko=1",
          "/zasady", "/dziennik", "/settings", "/transactions", "/history", "/etf", "/cash", "/status", "/logs"]
API_GET = ["/api/stan", "/api/kandydaci", "/api/wydarzenia", "/api/ustawienia"]

wyniki: list[tuple[bool, str]] = []


def wynik(ok: bool, opis: str) -> None:
    wyniki.append((ok, opis))
    print(f"[{'OK ' if ok else 'BŁĄD'}] {opis}", flush=True)


def kopia_bazy(src: Path, kat: Path) -> Path:
    kat.mkdir(parents=True, exist_ok=True)
    dst = kat / "wallet.db"
    a, b = sqlite3.connect(f"file:{src}?mode=ro", uri=True), sqlite3.connect(str(dst))
    a.backup(b)
    b.close()
    a.close()
    return dst


def uruchom(env: dict, *args: str, timeout: int = 300) -> tuple[int, str]:
    p = subprocess.run([sys.executable, "-m", *args], env=env, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="Test przed wdrożeniem (kopia bazy)")
    ap.add_argument("--pelny", action="store_true", help="także wyniki/wieczor --dry-run (sieć)")
    ap.add_argument("--baza", default=os.environ.get("WALLET_DB") or str(Path(os.environ.get("WALLET_DATA", "/data")) / "wallet.db"))
    a = ap.parse_args()

    src = Path(a.baza)
    if not src.exists():
        wynik(False, f"brak bazy {src}")
        return 1
    kat = Path(tempfile.mkdtemp(prefix="wallet-test-"))
    dst = kopia_bazy(src, kat)
    wynik(True, f"kopia bazy: {src} → {dst}")

    env = dict(os.environ, WALLET_DATA=str(kat), WALLET_DB=str(dst), WALLET_WEB_HEALTH="",
               TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="", PYTHONUNBUFFERED="1")
    env.setdefault("WEB_USER", "test")
    env.setdefault("WEB_PASS", "test")

    # --- worker
    tryby = [("stan",), ("kontrola", "--dry-run"), ("nav", "--dry-run")]
    if a.pelny:
        tryby += [("wyniki", "--dry-run"), ("wieczor", "--dry-run")]
    for t in tryby:
        rc, out = uruchom(env, "app.worker", *t)
        bledy = [x for x in out.splitlines() if "BŁĄD" in x or "Traceback" in x]
        wynik(rc == 0 and not bledy, f"worker {' '.join(t)} (rc={rc})" + (f": {bledy[0][:160]}" if bledy else ""))
        if rc != 0:
            print("\n".join(out.splitlines()[-15:]))

    # --- C1: rozbicie wyniku, TWR, odtworzenie historii — sztuczne dane, osobna baza tymczasowa (bez sieci)
    rc, out = uruchom(env, "app.test_c1")
    bledy = [x for x in out.splitlines() if x.startswith("[BŁĄD]") or "Traceback" in x]
    wynik(rc == 0 and not bledy, f"rozbicie wyniku / TWR / historia (app.test_c1, rc={rc})" + (f": {bledy[0][:160]}" if bledy else ""))
    if rc != 0:
        print("\n".join(out.splitlines()[-15:]))

    # --- C1a: wartości dzienne z IBKR, przeniesienie po wartości rynkowej, TWR = IBKR (sztuczne dane)
    rc, out = uruchom(env, "app.test_c1a")
    bledy = [x for x in out.splitlines() if x.startswith("[BŁĄD]") or "Traceback" in x]
    wynik(rc == 0 and not bledy, f"historia z IBKR / przeniesienie / TWR (app.test_c1a, rc={rc})" + (f": {bledy[0][:160]}" if bledy else ""))
    if rc != 0:
        print("\n".join(out.splitlines()[-15:]))

    # --- import IBKR Flex: sztuczny raport na osobnej bazie tymczasowej (bez sieci)
    rc, out = uruchom(env, "app.test_flex")
    bledy = [x for x in out.splitlines() if x.startswith("[BŁĄD]") or "Traceback" in x]
    wynik(rc == 0 and not bledy, f"import IBKR Flex (app.test_flex, rc={rc})" + (f": {bledy[0][:160]}" if bledy else ""))
    if rc != 0:
        print("\n".join(out.splitlines()[-15:]))

    # --- AL: watchlista (data zamknięcia, druga próba, brak kursu), „bez koszyka” tylko dla pozycji (sztuczne dane)
    rc, out = uruchom(env, "app.test_al")
    bledy = [x for x in out.splitlines() if x.startswith("[BŁĄD]") or "Traceback" in x]
    wynik(rc == 0 and not bledy, f"watchlista / bez koszyka (app.test_al, rc={rc})" + (f": {bledy[0][:160]}" if bledy else ""))
    if rc != 0:
        print("\n".join(out.splitlines()[-15:]))

    # --- P: mini-wykres bez wpłat, składowe 22:30 bez kursów z bazy, „kurs śróddzienny” (sztuczne dane)
    rc, out = uruchom(env, "app.test_p")
    bledy = [x for x in out.splitlines() if x.startswith("[BŁĄD]") or "Traceback" in x]
    wynik(rc == 0 and not bledy, f"mini-wykres bez wpłat / składowe / kurs śróddzienny (app.test_p, rc={rc})"
          + (f": {bledy[0][:160]}" if bledy else ""))
    if rc != 0:
        print("\n".join(out.splitlines()[-15:]))

    # --- T: transakcje jako rejestr (sztuczne dane)
    rc, out = uruchom(env, "app.test_t")
    bledy = [x for x in out.splitlines() if x.startswith("[BŁĄD]") or "Traceback" in x]
    wynik(rc == 0 and not bledy, f"rejestr transakcji (app.test_t, rc={rc})" + (f": {bledy[0][:160]}" if bledy else ""))
    if rc != 0:
        print("\n".join(out.splitlines()[-15:]))

    # --- C2a: ekspozycja look-through, składy z krajem/sektorem/walutą, benchmark A (sztuczne dane)
    rc, out = uruchom(env, "app.test_c2a")
    bledy = [x for x in out.splitlines() if x.startswith("[BŁĄD]") or "Traceback" in x]
    wynik(rc == 0 and not bledy, f"ekspozycja / benchmark A (app.test_c2a, rc={rc})" + (f": {bledy[0][:160]}" if bledy else ""))
    if rc != 0:
        print("\n".join(out.splitlines()[-15:]))

    # --- AL2: zamknięcia z notowania bieżącego (Ticker.info), zapas dzienny, limit / 429 (sztuczne dane)
    rc, out = uruchom(env, "app.test_al2")
    bledy = [x for x in out.splitlines() if x.startswith("[BŁĄD]") or "Traceback" in x]
    wynik(rc == 0 and not bledy, f"notowanie bieżące / zapas dzienny (app.test_al2, rc={rc})" + (f": {bledy[0][:160]}" if bledy else ""))
    if rc != 0:
        print("\n".join(out.splitlines()[-15:]))

    # --- AL3: wycena „dziś” z kotwicy IBKR (markPrice + kurs IBKR z D, ruch wg Yahoo), ekspozycja: nazwy (sztuczne dane)
    rc, out = uruchom(env, "app.test_al3")
    bledy = [x for x in out.splitlines() if x.startswith("[BŁĄD]") or "Traceback" in x]
    wynik(rc == 0 and not bledy, f"kotwica IBKR / NAV przed sesją (app.test_al3, rc={rc})" + (f": {bledy[0][:160]}" if bledy else ""))
    if rc != 0:
        print("\n".join(out.splitlines()[-15:]))

    # --- panel na porcie testowym
    log = open(kat / "uvicorn.log", "w")
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.web:app", "--host", "127.0.0.1", "--port", str(PORT)],
                           env=env, stdout=log, stderr=subprocess.STDOUT)
    baza_url = f"http://127.0.0.1:{PORT}"
    try:
        for _ in range(60):
            try:
                if urllib.request.urlopen(baza_url + "/healthz", timeout=2).read().startswith(b"ok"):
                    break
            except Exception:  # noqa
                time.sleep(0.5)
        else:
            wynik(False, "panel nie wystartował (uvicorn.log poniżej)")
            log.flush()
            print((kat / "uvicorn.log").read_text()[-2000:])
            return 1
        wynik(True, "panel: /healthz ok")

        class BezPrzekierowan(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kw):
                return None

        jar = http.cookiejar.CookieJar()
        sesja = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        surowy = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), BezPrzekierowan)
        dane = urllib.parse.urlencode({"username": env["WEB_USER"], "password": env["WEB_PASS"], "next": "/"}).encode()
        try:
            surowy.open(baza_url + "/login", dane, timeout=20)
        except urllib.error.HTTPError as e:
            if e.code != 303:
                wynik(False, f"logowanie: HTTP {e.code}")
                return 1
        wynik(any(c.name == "wallet_session" for c in jar), "logowanie: sesja")

        for s in STRONY:
            try:
                r = sesja.open(baza_url + s, timeout=60)
                body = r.read().decode("utf-8", "replace")
                ok = r.status == 200 and "Traceback" not in body and "Internal Server Error" not in body
                wynik(ok, f"strona {s} ({r.status})")
            except urllib.error.HTTPError as e:
                wynik(False, f"strona {s} (HTTP {e.code})")

        # B1: status importu IBKR w pasku pulpitu (stan wpisany do KOPII bazy; panel czyta go przy każdym wejściu)
        def ustaw_ibkr(wynik_ibkr: dict | None) -> None:
            c = sqlite3.connect(str(dst))
            c.execute("DELETE FROM stan_alertow WHERE klucz='ibkr_wynik'")
            if wynik_ibkr:
                c.execute("INSERT INTO stan_alertow(klucz,wartosc) VALUES('ibkr_wynik',?)", (json.dumps(wynik_ibkr),))
                rp = c.execute("SELECT wartosc FROM stan_alertow WHERE klucz='przebiegi'").fetchone()
                prz = json.loads(rp[0]) if rp else {}
                prz["ibkr"] = {"ts": wynik_ibkr["ts"], "ok": True, "bledy": [], "wyjatek": None, "ostatni_ok": wynik_ibkr["ts"]}
                c.execute("INSERT INTO stan_alertow(klucz,wartosc) VALUES('przebiegi',?) "
                          "ON CONFLICT(klucz) DO UPDATE SET wartosc=excluded.wartosc", (json.dumps(prz),))
            c.commit()
            c.close()
        oryg = None
        c = sqlite3.connect(str(dst))
        r0 = c.execute("SELECT wartosc FROM stan_alertow WHERE klucz='ibkr_wynik'").fetchone()
        c.close()
        oryg = json.loads(r0[0]) if r0 else None
        teraz_ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        for opis_st, w_st, oczek, mob in (
                ("zgodny", {"ts": teraz_ts, "wynik": "zgodny", "nowe": 2, "stan_na": teraz_ts[:10], "historia": "ok"},
                 "· zgodny", False),
                ("niezgodność", {"ts": teraz_ts, "wynik": "niezgodnosc", "nowe": 1, "stan_na": teraz_ts[:10],
                                 "blad": "A gotówka PLN: wallet 1 · IBKR 2"}, "IBKR: niezgodność", True)):
            ustaw_ibkr(w_st)
            try:
                body = sesja.open(baza_url + "/", timeout=60).read().decode("utf-8", "replace")
                ok = oczek in body and ('class="ibkr-mob"' in body) == mob and "Traceback" not in body
                wynik(ok, f"pulpit: status IBKR „{opis_st}”" + ("" if ok else " — brak w HTML"))
            except urllib.error.HTTPError as e:
                wynik(False, f"pulpit: status IBKR „{opis_st}” (HTTP {e.code})")
        ustaw_ibkr(oryg)

        try:
            surowy.open(baza_url + "/pulpit/ustawienia", timeout=20)
            wynik(False, "/pulpit/ustawienia: brak przekierowania")
        except urllib.error.HTTPError as e:
            wynik(e.code == 303 and "/settings" in (e.headers.get("location") or ""),
                  f"/pulpit/ustawienia → {e.headers.get('location')} ({e.code})")

        # T: wiersz IBKR tylko do odczytu (próba usunięcia przez panel odrzucona), /cash bez formularzy zapisu,
        # usunięte trasy (edycja całej księgi CSV) — na KOPII bazy, z jednym sztucznym wierszem IBKR
        c = sqlite3.connect(str(dst))
        tid = c.execute("INSERT INTO transakcje(date,konto,ticker,qty,price,currency,utworzono,zrodlo,ibkr_id) "
                        "VALUES('2000-01-03','A','OPLATA',-0.01,1,'PLN','x','flex','TEST-T-RO')").lastrowid
        c.commit()
        c.close()
        try:
            body = sesja.open(baza_url + "/transactions/usun", urllib.parse.urlencode({"id": tid}).encode(),
                              timeout=60).read().decode("utf-8", "replace")
            c = sqlite3.connect(str(dst))
            jest = c.execute("SELECT 1 FROM transakcje WHERE id=?", (tid,)).fetchone()
            c.execute("DELETE FROM transakcje WHERE id=?", (tid,))
            c.commit()
            c.close()
            wynik(bool(jest) and "tylko do odczytu" in body, "transakcje: usunięcie wiersza IBKR przez panel odrzucone")
        except urllib.error.HTTPError as e:
            wynik(False, f"transakcje: usunięcie wiersza IBKR (HTTP {e.code})")
        try:
            body = sesja.open(baza_url + "/cash", timeout=60).read().decode("utf-8", "replace")
            wynik("/cash/move" not in body and "/transactions/delete" not in body and "/transactions?typ=gotowka" in body,
                  "/cash: tylko salda + link do rejestru (bez formularzy zapisu)")
            wynik("Stan z bieżącej wyceny" in body and "Sprawdz portfel" not in body and "Saldo gotówki" in body,
                  "/cash: polskie znaki, „Stan z bieżącej wyceny (kursy z HH:MM)”")
        except urllib.error.HTTPError as e:
            wynik(False, f"/cash (HTTP {e.code})")
        kody = []
        for sc in ("/transactions/raw", "/transactions/delete", "/cash/move"):
            try:
                surowy.open(baza_url + sc, b"content=x&idx=0&amount=1", timeout=20)
                kody.append(200)
            except urllib.error.HTTPError as e:
                kody.append(e.code)
        wynik(all(k in (404, 405) for k in kody), f"usunięte trasy zapisu (raw CSV, delete, /cash/move): {kody}")

        # C2a: Analiza — sekcje ekspozycji i benchmarku; kafel A z dopiskiem benchmarku
        try:
            body = sesja.open(baza_url + "/analiza?eks=calosc&widok=kraje&bm=wartosc", timeout=60).read().decode("utf-8", "replace")
            wynik('id="ekspozycja"' in body and "Portfel vs benchmark" in body and "Pokrycie składów ETF" in body,
                  "/analiza: ekspozycja (kraje), pokrycie składów, portfel vs benchmark")
            body = sesja.open(baza_url + "/", timeout=60).read().decode("utf-8", "replace")
            ust = sqlite3.connect(str(dst)).execute("SELECT wartosc FROM ustawienia WHERE klucz='benchmark_a'").fetchone()
            sym = ((ust[0] if ust else "") or "VWCE").split(".")[0].upper()
            wynik(f"TWR" in body and f"{sym} (te same wpłaty)" in body, f"pulpit: kafel A z linią benchmarku „{sym} (te same wpłaty) …”")
            wynik("(te same wpłaty)" in body, "pulpit: kafle A/B — „(te same wpłaty)” przy benchmarku (AL2)")
        except urllib.error.HTTPError as e:
            wynik(False, f"C2a: strony (HTTP {e.code})")

        basic = "Basic " + base64.b64encode(f"{env['WEB_USER']}:{env['WEB_PASS']}".encode()).decode()

        def api(sciezka: str, body: dict | None = None):
            req = urllib.request.Request(baza_url + sciezka, headers={"Authorization": basic, "Content-Type": "application/json"},
                                         data=json.dumps(body).encode() if body is not None else None)
            r = urllib.request.urlopen(req, timeout=60)
            return r.status, json.loads(r.read().decode("utf-8"))

        try:
            req = urllib.request.Request(baza_url + "/api/przeglad", headers={"Authorization": basic})
            r = urllib.request.urlopen(req, timeout=60)
            txt = r.read().decode("utf-8")
            wynik(r.status == 200 and txt.startswith("# Przegląd"), f"API /api/przeglad ({r.status}, {len(txt)} znaków)")
        except Exception as e:  # noqa
            wynik(False, f"API /api/przeglad: {e}")
        for s in API_GET:
            try:
                st, _d = api(s)
                wynik(st == 200, f"API {s} ({st})")
            except Exception as e:  # noqa
                wynik(False, f"API {s}: {e}")
        try:
            st, d = api("/api/wplata", {"kwota": 0, "z_gotowka": True})
            wynik(st == 200 and "przydzial" in d, f"API /api/wplata kwota 0 (do rozdziału {d.get('do_rozdzialu')})")
        except Exception as e:  # noqa
            wynik(False, f"API /api/wplata: {e}")
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()
        log.close()

    zle = [o for ok, o in wyniki if not ok]
    print(f"\n=== TEST: {len(wyniki) - len(zle)}/{len(wyniki)} OK" + (f" — BŁĘDY: {len(zle)}" if zle else " — można wdrażać"))
    return 1 if zle else 0


if __name__ == "__main__":
    sys.exit(main())
