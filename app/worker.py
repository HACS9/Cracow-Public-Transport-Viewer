"""Worker (harmonogram): wyceny, alerty, kalendarz wyników, kopie bazy.

  python -m app.worker wycena                  # kursy, wycena A/B/całość, historia + alerty (portfel, ruchy, strefy, zasady)
  python -m app.worker wycena --bez-alertow    # sama wycena
  python -m app.worker wycena --dry-run        # bez zapisu historii i stanu alertów, Telegram tylko w logu
  python -m app.worker alerty                  # alerty bez zapisu historii wyceny
  python -m app.worker wyniki                  # 08:10: skład ETF, daty wyników → kalendarz; wyniki, watchlista w strefie, zasady
  python -m app.worker wieczor                 # 22:30: kandydaci TAK w strefie, podsumowanie składowych ETF
  python -m app.worker wszystko                # wycena + alerty + wyniki + wieczor
  python -m app.worker cele                    # lista śledzonych spółek
  python -m app.worker kontrola                # czy przebiegi idą zgodnie z harmonogramem (Telegram przy awarii)
  python -m app.worker wycena --porownaj /DATA/AppData/portfolio-tracker/data   # test zgodności z trackerem
  python -m app.worker stan                    # wycena z bazy, bez sieci
  python -m app.worker kopia                   # kopia wallet.db do data/backup (14 ostatnich)
  python -m app.worker tickery [--dry-run]     # jednorazowo: wszędzie ticker Yahoo (DVL → DVL.WA), kalendarz_dni 14
  python -m app.worker historia [--dry-run]    # jednorazowo: odtworzenie dziennej historii A/B/całość od 1. transakcji
  python -m app.worker ibkr [--dry-run]        # 07:45 pn–sb: raport IBKR Flex → import „dopisz” + kontrola (Telegram)
  python -m app.worker ibkr --ponow            # 12:00: ponowienie, tylko gdy dzisiejszy import nie przeszedł
  python -m app.worker ibkr --plik X.xml --dry-run   # test na raporcie z dysku (bez pobierania)
  python -m app.worker nav --plik X.xml [--dry-run]  # wartości dzienne kont z raportu IBKR → historia (np. czerwiec–wrzesień)
  python -m app.worker nav [--dry-run]               # przeliczenie historii z wartości IBKR już w bazie
  python -m app.worker sklad [--dry-run]             # C2a: pełne składy ETF (kraj/sektor/waluta), podgląd przed/po
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path

from . import db, ksiega
from .rynek import Rynek


def zl(x) -> str:
    return "—" if x is None else f"{x:,.2f}".replace(",", " ").replace(".", ",")


def pc(x) -> str:
    return "—" if x is None else f"{x:+.2f}%"


def drukuj(w: dict, dry: bool = False, tryb: str = "wycena") -> None:
    print(f"### RUN {w['ts']} mode={tryb} dry_run={dry} ###")
    print(f"### WYCENA {w['ts']}  ({'online' if w['online'] else 'z bazy'}, {w['base']})")
    for z, nazwa in (("A", "Portfel A"), ("B", "Portfel B"), ("calosc", "Całość")):
        s = w[z]
        print(f"{nazwa:10s} wartość {zl(s['wartosc']):>12}  dzień {s['zmiana_pct']:+.2f}%  "
              f"wpłacono {zl(s['wplacono']):>12}  wynik {zl(s['wynik']):>10} "
              f"({pc(s['wynik_pct'])})  gotówka {zl(s['gotowka_wartosc'])}")
    print("\nPozycje:")
    for p in w["calosc"]["pozycje"]:
        print(f"  {p['konto']} {p['symbol']:10s} {p['ilosc']:>8g} × {p['kurs'] or 0:>9.2f} {p['waluta'] or '':4s} = "
              f"{zl(p['wartosc']):>11}  koszt {zl(p['koszt']):>11}  P&L {zl(p['pnl']):>9} "
              f"({pc(p['pnl_pct'])})" + (f"  [IBKR {p['kurs_ibkr']:g} {p['waluta_ibkr']} · {p['kotwica']}]" if p.get("kurs_ibkr")
                                          else ("  [wg Yahoo]" if p.get("kotwica") == "yahoo" else "")))
    for b in w["braki"]:
        print("  ! " + b)
    k = w["calosc"].get("kotwica")
    if k:            # AL3: kontrola z NAV IBKR (przed sesją wartość = NAV − naliczenia)
        print(f"\nKotwica IBKR {k['data']} ({'ruch po raporcie' if k['ruch'] else 'bez sesji po raporcie'}):")
        for z, nazwa in (("A", "A"), ("B", "B"), ("calosc", "Całość")):
            x = w[z]["kotwica"]
            print(f"  {nazwa:7s} wycena {zl(w[z]['wartosc']):>12}  NAV IBKR {zl(x['nav']) if x['nav'] is not None else '—':>12}  "
                  f"wg cen IBKR {zl(x['na_d']) if x['na_d'] is not None else '—':>12}  różnica {zl(x['roznica'])}")
        for u in k["yahoo"] + k["uwagi"]:
            print("  · " + u)


def porownaj(w: dict, tracker_dir: Path) -> int:
    p = tracker_dir / "history.csv"
    if not p.exists():
        print(f"\n[porównanie] brak {p}")
        return 1
    rows = list(csv.DictReader(p.read_text(encoding="utf-8").splitlines()))
    if not rows:
        print("\n[porównanie] history.csv trackera jest pusty")
        return 1
    last = rows[-1]
    t_ts = dt.datetime.fromisoformat(last["ts"])
    w_ts = dt.datetime.fromisoformat(w["ts"])
    minuty = abs((w_ts - t_ts).total_seconds()) / 60
    t_val = float(last["value"])
    t_wpl = float(last.get("contributed") or last.get("cost_basis") or 0)
    roznica = (w["calosc"]["wartosc"] / t_val - 1) * 100
    ok_wart = abs(roznica) <= 0.1
    ok_wpl = abs(w["calosc"]["wplacono"] - t_wpl) <= 1.0
    ok_czas = minuty <= 60
    print("\n[porównanie z trackerem]")
    print(f"  tracker  {last['ts']}  wartość {zl(t_val)}  wpłacono {zl(t_wpl)}")
    print(f"  wallet   {w['ts']}  wartość {zl(w['calosc']['wartosc'])}  wpłacono {zl(w['calosc']['wplacono'])}")
    print(f"  [{'OK  ' if ok_czas else 'UWAGA'}] odstęp czasu {minuty:.0f} min (≤ 60)")
    print(f"  [{'OK  ' if ok_wart else 'BŁĄD'}] różnica wartości {roznica:+.3f}% (tolerancja ±0,1%)")
    print(f"  [{'OK  ' if ok_wpl else 'BŁĄD'}] różnica wpłacono {w['calosc']['wplacono'] - t_wpl:+.2f} zł (±1 zł)")
    wynik = ok_wart and ok_wpl
    print(f"  {'ZGODNE' if wynik else 'NIEZGODNE'}" + ("" if ok_czas else " — uruchom tuż po pełnej godzinie (tracker liczy o :05)"))
    return 0 if wynik else 1


def _ok(con, a) -> None:
    """Udany przebieg (bez wyjątku) → stan dla `kontrola`; błędy sekcji alertów zapisane osobno."""
    if a.dry_run or a.tryb not in ("wycena", "alerty", "wyniki", "wieczor", "wszystko"):
        return
    from . import alerty, zdrowie
    zdrowie.zapisz_przebieg(con, a.tryb, True, list(alerty.BLEDY))


def kopia(zachowaj: int = 14) -> None:
    kat = db.DB_PATH.parent / "backup"
    kat.mkdir(parents=True, exist_ok=True)
    cel = kat / f"wallet-{dt.date.today().isoformat()}.db"
    src, dst = sqlite3.connect(str(db.DB_PATH)), sqlite3.connect(str(cel))
    src.backup(dst)
    dst.close()
    src.close()
    stare = sorted(kat.glob("wallet-*.db"))[:-zachowaj]
    for s in stare:
        s.unlink()
    print(f"kopia: {cel} (usunięto {len(stare)} starych)")


def main():
    try:
        _main()
    except SystemExit:
        raise
    except Exception as e:  # noqa — zapis awarii dla `kontrola`, potem normalny traceback do logu
        _zapisz_awarie(e)
        raise


_TRYB = {'nazwa': None, 'dry': False}


def _zapisz_awarie(e: Exception) -> None:
    if not _TRYB['nazwa'] or _TRYB['dry']:
        return
    try:
        from . import alerty, zdrowie
        con = db.polacz()
        zdrowie.zapisz_przebieg(con, _TRYB['nazwa'], False, list(alerty.BLEDY), f"{type(e).__name__}: {e}")
        con.close()
    except Exception:  # noqa
        pass


def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tryb", choices=["wycena", "alerty", "wyniki", "wieczor", "wszystko", "cele", "stan", "kopia", "kontrola", "tickery", "ibkr", "historia", "nav", "sklad"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--bez-alertow", action="store_true")
    ap.add_argument("--porownaj", type=Path, help="katalog data/ trackera")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--plik", type=Path, help="ibkr: raport Flex XML z dysku zamiast pobierania (test)")
    ap.add_argument("--ponow", action="store_true", help="ibkr: tylko gdy dzisiejszy import jeszcze nie przeszedł (12:00)")
    a = ap.parse_args()

    _TRYB.update(nazwa=a.tryb, dry=a.dry_run)
    if a.tryb == "kopia":
        kopia(int(os.environ.get("WALLET_BACKUP_KEEP", "14")))
        from . import zdrowie
        con = db.polacz()
        print(f"porządki: usunięto {zdrowie.porzadki_kursow(con)} starych kursów (zostaje ostatni z każdego dnia)")
        zdrowie.zapisz_przebieg(con, "kopia", True, [])
        con.close()
        return
    con = db.polacz()
    db.inicjuj(con)
    db.uzupelnij_ustawienia(con)
    if a.tryb == "historia":
        from . import alerty, historia
        alerty.naglowek_run("historia", a.dry_run)
        try:
            r = historia.odtworz(con, Rynek(con, online=True), zapis=not a.dry_run)
        except historia.HistoriaBlad as e:
            print(f"historia: BŁĄD — {e}")
            con.close()
            sys.exit(1)
        historia.drukuj(r)
        con.close()
        return
    if a.tryb == "sklad":        # C2a: pełne składy ETF (kraj / sektor / waluta), VWCE ≈ ACWI; najpierw --dry-run
        from . import sklad
        sys.exit(sklad.przeglad(con, zapis=not a.dry_run))
    if a.tryb == "nav":
        from . import alerty, nav_ibkr
        alerty.naglowek_run("nav", a.dry_run)
        if a.plik and not a.plik.exists():
            print(f"nav: brak pliku {a.plik}")
            con.close()
            sys.exit(1)
        nav_ibkr.przebieg(con, plik=a.plik, dry=a.dry_run)
        con.close()
        return
    if a.tryb == "ibkr":
        from . import alerty, ibkr_auto
        alerty.naglowek_run("ibkr", a.dry_run)
        rc = ibkr_auto.przebieg(con, a.dry_run, a.plik, a.ponow)
        con.close()
        sys.exit(rc if rc in (0, 1, 2) else 0)
    from . import alerty, portfel
    rynek = Rynek(con, online=(a.tryb != "stan"))
    if a.tryb == "kontrola":
        from . import zdrowie
        alerty.naglowek_run("kontrola", a.dry_run)
        zdrowie.kontrola(con, a.dry_run)
        con.close()
        return
    if a.tryb == "tickery":
        from . import tickery
        if not a.dry_run:
            from . import store
            store.backup_db("tickery")
            print("kopia przed zmianą: data/backup/edycje/…-tickery.db")
        rap = tickery.migracja(con, a.dry_run)
        print(f"### porządek tickerów (wariant B) dry_run={a.dry_run}")
        for k in ("kandydaci", "wydarzenia", "dziennik", "niejednoznaczne"):
            print(f"{k}: {len(rap[k])}")
            for x in rap[k]:
                print(f"  {x}")
        print(f"stan alertów wyników: przepisane klucze {rap['alerty']}")
        print(f"kalendarz_dni: {rap['kalendarz_dni'] or 'bez zmian (migracja już była)'}")
        con.close()
        return
    if a.tryb == "cele":
        alerty.naglowek_run("cele", a.dry_run)
        alerty.drukuj_cele(alerty.cele(con))
        con.close()
        return
    if a.tryb in ("wyniki", "wieczor"):
        alerty.naglowek_run(a.tryb, a.dry_run)
        (alerty.dzienne if a.tryb == "wyniki" else alerty.wieczorne)(con, rynek, a.dry_run)
        rynek.zatwierdz()
        _ok(con, a)
        con.close()
        return
    w = ksiega.wycena(con, rynek)
    s = None
    if a.tryb in ("wycena", "alerty", "wszystko"):
        portfel.aktualizuj_shadow(con, rynek)
        s = portfel.stan(con, rynek, w, zapisz=not a.dry_run)   # licznik „poza pasmem od” (A4)
    rynek.zatwierdz()
    if a.tryb in ("wycena", "wszystko") and not a.dry_run:
        ksiega.zapisz_historie(con, w)
    if a.json:
        print(json.dumps(w, ensure_ascii=False, indent=1, default=str))
    else:
        drukuj(w, a.dry_run, "wycena" if a.tryb == "stan" else a.tryb)
    if a.tryb in ("alerty", "wszystko") or (a.tryb == "wycena" and not a.bez_alertow):
        print("\n[alerty]")
        alerty.godzinowe(con, rynek, w, a.dry_run, s)
        rynek.zatwierdz()
    if a.tryb == "wszystko":
        print("\n[wyniki]")
        alerty.dzienne(con, rynek, a.dry_run)
        print("\n[wieczor]")
        alerty.wieczorne(con, rynek, a.dry_run)
        rynek.zatwierdz()
    _ok(con, a)
    con.close()
    if a.porownaj:
        sys.exit(porownaj(w, a.porownaj))


if __name__ == "__main__":
    main()
