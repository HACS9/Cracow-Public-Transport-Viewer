"""Pulpit (nowy układ, makieta „Wallet — nowy układ”): jeden ekran bez przewijania na komputerze.

Siatka 3 kolumn (5 : 8 : 5) × 3 rzędy, maks. szerokość treści 2200 px:
  [Całość | A · B | wykres] / [struktura + pasma | pozycje | alerty, 7 dni, strefa wejścia] / [następna wpłata]
Interakcje: przełączniki zmieniają kafel na miejscu; „Rozdziel” i wykres otwierają panel z prawej;
linki „→” prowadzą na podstrony; nadmiar w kaflach → „+N →” (bez przewijania wewnątrz kafli).
Telefon: jedna kolumna, dolny pasek zakładek (szablon bazowy).
"""
from __future__ import annotations

import datetime as dt

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse

from . import db, portfel, store
from .rynek import Rynek

STREFA_MAX = 6
ALERTY_MAX = 4
POZYCJE_MAX = 14
NAZWY_GRUP = {"kotwica": "Kotwica · VWCE", "teza": "Teza · IWVL, IWMO, ZPRV", "polska": "Polska", "sp": "Koszyk B"}
SKALA_PASKA = 70.0            # 0–70% szerokości paska struktury


def _pl(v, dec=0, znak=False) -> str:
    if not isinstance(v, (int, float)):          # None / brak danych (np. pusta baza)
        return "—"
    t = f"{v:+,.{dec}f}" if znak else f"{v:,.{dec}f}"
    return t.replace(",", " ").replace(".", ",").replace("-", "−")


def _pct(v, dec=2, znak=True) -> str:
    return "—" if not isinstance(v, (int, float)) else _pl(v, dec, znak) + "%"


def _strefa(con) -> tuple[list[dict], list[dict], int]:
    """Kandydaci TAK/watch: w strefie (TAK przed watch, potem odległość) i najbliżej strefy.
    Kurs: ostatni z tabeli kursy (jak lista kandydatów), wspólna funkcja portfel.odleglosc_strefy."""
    w, blisko = [], []
    rows = db.wiersze(con, "SELECT * FROM kandydaci WHERE status IN ('tak','watch') "
                           "AND (wejscie_od IS NOT NULL OR wejscie_do IS NOT NULL)")
    for k in portfel.odleglosc_strefy(con, rows):
        if k["cena"] is None or k["w_strefie"] is None:
            continue
        k["kurs"], k["waluta"] = k["cena"], k["waluta_k"] or k.get("waluta")
        if k["w_strefie"]:
            w.append(k)
        elif k["odl"] is not None:
            blisko.append(k)
    w.sort(key=lambda k: (k["status"] != "tak", k["ticker"]))
    blisko.sort(key=lambda k: abs(k["odl"]))
    return w[:STREFA_MAX], blisko[:2], max(0, len(w) - STREFA_MAX)


def _kalendarz(con, dni: int = 7) -> list[dict]:
    dzis = dt.date.today()
    ev = db.wiersze(con, "SELECT * FROM wydarzenia WHERE zrobione=0 AND data>=? AND data<=? ORDER BY data, id",
                    (dzis.isoformat(), (dzis + dt.timedelta(days=dni)).isoformat()))
    dni_: dict[str, list] = {}
    for e in ev:
        dni_.setdefault(e["data"][:10], []).append(e)
    return [{"data": d, "etykieta": f"{d[8:10]}.{d[5:7]}", "wpisy": v} for d, v in dni_.items()]


def _status_workera(con) -> tuple[bool, str]:
    try:
        from . import zdrowie
        p = zdrowie.problemy(con)
        return (not p), ("; ".join(p.values()) if p else "")
    except Exception as e:  # noqa
        return False, str(e)


def _status_ibkr(con) -> dict | None:
    """Pasek pulpitu: ostatni import IBKR (ibkr_auto.status); błąd odczytu nie psuje pulpitu."""
    try:
        from . import ibkr_auto
        return ibkr_auto.status(con)
    except Exception as e:  # noqa
        return {"tekst": "IBKR: status niedostępny", "title": str(e), "problem": True}


def dane_pulpitu(con) -> dict:
    rynek = Rynek(con, online=False)
    s = portfel.stan(con, rynek)
    w = s["w"]
    pozycje = sorted(w["calosc"]["pozycje"], key=lambda p: -(p["wartosc"] or 0))
    ts = max((p["kurs_ts"] or "" for p in pozycje), default="")
    alerty = sorted([a for a in s["alerty"] if a["poziom"] in ("krytyczny", "ostrzezenie")],
                    key=lambda a: a["poziom"] != "krytyczny")
    info = [a for a in s["alerty"] if a["poziom"] == "info"]
    strefa, blisko, strefa_wiecej = _strefa(con)
    ok, problem = _status_workera(con)
    for x in s["struktura"]:
        x["nazwa"] = NAZWY_GRUP.get(x["grupa"], x["grupa"])
        x["szer"] = min(100.0, x["udzial"] / SKALA_PASKA * 100)
        x["kreska"] = min(100.0, x["granica"] / SKALA_PASKA * 100)
    return {"s": s, "w": w, "pozycje": pozycje, "kursy_ts": ts[11:16] if len(ts) >= 16 else "—",
            "kursy_data": ts[:10], "alerty": alerty[:ALERTY_MAX], "alerty_wiecej": max(0, len(alerty) - ALERTY_MAX),
            "alerty_n": {"kryt": sum(a["poziom"] == "krytyczny" for a in alerty),
                         "ostr": sum(a["poziom"] == "ostrzezenie" for a in alerty), "info": len(info)},
            "strefa": strefa, "blisko": blisko, "strefa_wiecej": strefa_wiecej, "kalendarz": _kalendarz(con), "zalegle": len(portfel.zalegle(con)),
            "koszyki": [k for k in s["koszyki"] if k["grupa"] != "sp"], "worker_ok": ok, "worker_problem": problem, "ibkr": _status_ibkr(con),
            # gotówka na koncie = wpłata już zaksięgowana → domyślnie rozdzielamy tylko ją (kwota nowej wpłaty 0)
            "kwota": 0.0 if s["gotowka"]["A"] >= 100 else portfel._f(db.ustawienia(con).get("wplata_miesieczna"), 0)}


PULPIT = r"""
{% extends "base" %}{% block mainclass %}wide{% endblock %}
{% block body %}
<style>
 .pl{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,8fr) minmax(0,5fr);grid-template-rows:auto minmax(0,1fr) 72px;
   gap:20px;height:calc(100dvh - var(--hdr,64px) - 48px);min-height:560px}
 .pl>.c-cal{grid-column:1;grid-row:1} .pl>.c-kon{grid-column:2;grid-row:1;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;min-height:0}
 .pl>.c-wyk{grid-column:3;grid-row:1;padding:14px 18px}
 .kaf{gap:6px!important}
 .c-prawa section>*{flex-shrink:0}
 .kaf .vrow{height:52px;display:flex;align-items:flex-end;gap:14px;flex-wrap:nowrap}
 .kaf .vrow .big{line-height:1}
 .kaf .dzis{font-size:16px;padding-bottom:3px}
 .kaf .wyn{font-size:15px} .kaf .meta{font-size:13px}
 .pl section{margin:0;border-radius:10px;padding:18px 22px;display:flex;flex-direction:column;gap:10px;min-height:0;overflow:hidden}
 .pl h2{margin:0;font-size:16px;font-weight:600;color:var(--tx)}
 .hd{display:flex;align-items:baseline;justify-content:space-between;gap:10px}
 .hd a{font-size:13px;text-decoration:none}
 .panel .hd{align-items:center}
 .pl td{white-space:nowrap}
 .pl td.wrap{white-space:normal}
 .kal span.op{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
 .c-prawa>section{flex:none} .c-prawa>section.c-stf{flex:1 1 auto}
 .c-prawa>section.c-al{max-height:44%} .c-prawa>section.c-kal{max-height:30%}
 .lab{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim)}
 .big{font-family:"IBM Plex Mono",monospace;font-weight:500;line-height:1.05}
 .up{color:var(--up)} .dn{color:var(--down)} .mut{color:var(--dim)}
 .tk{font-weight:600}
 .konto{font-size:11px;padding:1px 6px;border:1px solid var(--btn-line);border-radius:8px;color:var(--dim);margin-left:4px}
 .pill{font-size:12px;padding:2px 8px;border-radius:10px;white-space:nowrap}
 .pill.bad{background:var(--crit-bg);color:var(--crit-tx)} .pill.ok{background:var(--ok-bg);color:var(--up)}
 .st-tak{font-size:11px;padding:1px 7px;border-radius:8px;background:var(--ink-bg);color:var(--ink-tx);margin-left:4px}
 .st-watch{font-size:11px;padding:1px 7px;border-radius:8px;border:1px solid var(--btn-line);margin-left:4px}
 .al{font-size:14px;padding:8px 12px;border-radius:6px}
 .al.krytyczny{background:var(--crit-bg);color:var(--crit-tx)} .al.ostrzezenie{background:var(--w-bg);color:var(--w-tx)}
 .bar{position:relative;height:8px;border-radius:4px;background:var(--track)}
 .bar i{display:block;height:8px;border-radius:4px;background:var(--up)} .bar i.bad{background:var(--bar-bad)}
 .bar b{position:absolute;top:-3px;width:2px;height:14px;background:var(--tx)}
 .pl table{font-size:14px} .pl th,.pl td{padding:7px 6px;border-bottom:1px solid var(--line)}
 .pl th{font-size:12px;letter-spacing:.06em;text-transform:uppercase;font-weight:500;color:var(--dim)}
 .pl td.r,.pl th.r{text-align:right}
 .chips{display:flex;gap:4px;font-size:13px}
 .chips button{padding:4px 11px;border-radius:12px;border:1px solid var(--btn-line);background:var(--panel);color:var(--tx);font-size:13px;min-height:0}
 .chips button.on{background:var(--ink-bg);color:var(--ink-tx);border-color:var(--ink-bg)}
 .kal{display:grid;grid-template-columns:56px minmax(0,1fr);gap:7px 12px;font-size:14px}
 .foot{margin-top:auto;font-size:13px;color:var(--dim);display:flex;justify-content:space-between;gap:10px}
 .chartbox{flex:1;min-height:0;border-radius:6px;cursor:pointer;display:block;text-decoration:none;color:inherit}
 .chartbox .chart-wrap{height:100%} .chartbox .chart-wrap svg{width:100%;height:100%}
 .wpl{flex-direction:row!important;align-items:center;gap:16px!important;padding:0 22px!important;font-size:14px}
 .wpl input[type=text]{width:130px}
 .pasek-st{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
 .ibkr-st{color:inherit;text-decoration:none} .ibkr-st:hover{text-decoration:underline} .ibkr-st.bad{color:var(--down)}
 .ibkr-mob{display:none;font-size:13px;color:var(--down);text-decoration:none;white-space:nowrap}
 .btn-acc{background:var(--up);border-color:var(--up);color:#fff}
 .btn-acc:hover{background:var(--up);opacity:.9}
 .hid{display:none!important}
 /* panel z prawej */
 .scrim{position:fixed;inset:0;background:rgba(23,25,29,.28);z-index:60}
 .panel{position:fixed;top:0;right:0;height:100vh;width:min(600px,100vw);background:var(--panel);border-left:1px solid var(--line);
   z-index:61;padding:28px 32px;box-sizing:border-box;display:flex;flex-direction:column;gap:16px;overflow:auto}
 .panel.szeroki{width:min(1100px,100vw)}
 .panel h2{margin:0;font-size:20px;color:var(--tx)}
 .panel .x{width:40px;height:40px;padding:0;display:flex;align-items:center;justify-content:center}
 .toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);background:var(--ink-bg);color:var(--ink-tx);
   padding:10px 16px;border-radius:8px;z-index:70;font-size:14px}
 @media (max-width:1599px){
   .hide-md{display:none}
   .pl{grid-template-rows:auto minmax(0,1fr) 72px;gap:16px}
   .c-str{gap:8px!important} .c-str table td{padding:5px 6px} .c-str .bar{height:6px} .c-str .bar i{height:6px}
   .pl table{font-size:13px} .al{font-size:13px;padding:6px 10px}
   .c-prawa>section.c-al{max-height:32%} .c-prawa>section.c-kal{max-height:32%}
   .c-al .al{white-space:nowrap;overflow:hidden;text-overflow:ellipsis} .c-prawa section{gap:8px!important}
   .kaf .vrow{height:44px} .kaf .vrow .big[style*="42px"]{font-size:34px!important} .kaf .vrow .big[style*="32px"]{font-size:26px!important}
   .pl section{padding:16px 18px}
 }
 @media (max-width:1199px){
   .c-prawa,.c-kon{display:contents!important}
   .pl{grid-template-columns:minmax(0,1fr) minmax(0,1fr);grid-template-rows:none;height:auto;min-height:0}
   .pl>*{grid-column:auto!important;grid-row:auto!important}
   .pl .c-poz,.pl .c-wpl{grid-column:1 / -1!important}
   .pl section{overflow:visible}
 }
 @media (max-width:720px){
   .pl{grid-template-columns:minmax(0,1fr);gap:12px}
   .pl .c-cal{order:1}.pl .c-kon>section{order:2}.pl .c-wyk{order:3;height:210px}.pl .c-al{order:4}.pl .c-str{order:6}
   .pl .c-kal{order:5}.pl .c-stf{order:7}.pl .c-poz{order:8}.pl .c-wpl{order:9}
   .pl section{padding:14px 16px}
   .hide-mob{display:none}
   .ibkr-mob{display:inline-flex;align-items:center}
   .wpl{flex-wrap:wrap;padding:14px 16px!important}
   .kaf .vrow{height:auto}
   .pl table{display:table;white-space:normal}
 }

 .kaf .jl,.kaf .wyn{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 @media (max-width:720px){.kaf .jl,.kaf .wyn{white-space:normal}}
 [data-tip]{cursor:help;border-bottom:1px dotted var(--btn-line)} .tipc{display:none}
 #tipbox{position:fixed;z-index:90;background:var(--panel);color:var(--tx);border:1px solid var(--line);border-radius:8px;
   box-shadow:0 6px 24px rgba(0,0,0,.16);padding:10px 12px;font-size:13px;line-height:1.55;width:max-content;min-width:290px;max-width:min(460px,calc(100vw - 16px));pointer-events:none;text-align:left;white-space:normal}
 #tipbox .tr{display:flex;justify-content:space-between;gap:12px} #tipbox .mono{white-space:nowrap} #tipbox .dz{white-space:nowrap} #tipbox b{font-weight:600}
</style>

<div class="pl">
  {% macro zk(v) %}<span class="mono {{ 'up' if (v or 0) >= 0 else 'dn' }}">{{ zlz(v) }}</span>{% endmacro %}
  {% macro zn(v) %}<span class="mono {{ 'up' if (v or 0) >= 0 else 'dn' }}">{{ zlz(v) }} zł</span>{% endmacro %}
  {% macro kafel(klasa, etykieta, x, rozmiar, linie, dop='', tipy=()) %}
  <section class="kaf {{ klasa }}">
    <div class="lab">{{ etykieta }}</div>
    <div class="vrow"><span class="big" style="font-size:{{ rozmiar }}px"{% if x.kotwica and x.kotwica.opis %} title="{{ x.kotwica.opis }}"{% endif %}>{{ x.wartosc|zl }} zł</span>
      {% if not x.zmiana_netto %}<span class="mono mut dzis" title="brak zmiany dziś">—</span>{% else %}<span class="mono dzis {{ 'up' if x.zmiana_netto_pct>=0 else 'dn' }}" title="dziś netto {{ zlz(x.zmiana_netto) }} zł (notowania + kurs waluty)">{{ pct(x.zmiana_netto_pct) }}</span>{% endif %}</div>
    <div class="wyn">wynik <span class="mono {{ 'up' if x.wynik>=0 else 'dn' }}">{{ zlz(x.wynik) }} zł · {{ pct(x.wynik_pct) }}</span>{{ dop }}</div>
    {% for l in linie %}<div class="mut meta jl" title="{{ tipy[loop.index0] if tipy|length > loop.index0 and tipy[loop.index0] else l|striptags }}">{{ l }}</div>{% endfor %}
  </section>
  {% endmacro %}
  {# skąd kapitał konta: same przeniesienia → „(z A)”, przeniesienia + wpłaty → „(w tym z A X zł)”, same wpłaty → nic #}
  {% macro zrodlo_wplat(x) %}{% if (x.przeniesione or 0) > 0.5 %}{% if (x.wplaty or 0)|abs > 0.5 %} (w tym z A {{ x.przeniesione|zl }} zł){% else %} (z A){% endif %}{% endif %}{% endmacro %}
  {% macro bm_sym(sh, dom) %}{{ ((sh.etf if sh else none) or dom).split('.')[0] }}{% endmacro %}
  {% macro bm_pct(sh) %}{{ pct(sh.wynik_pct, 1) if sh and sh.wynik_pct is not none else '—' }}{% endmacro %}
  {% macro bm_tip(sh, dom, k) %}{{ bm_sym(sh, dom) }} przy tych samych wpłatach: wynik {{ bm_pct(sh) }}{% if sh and sh.wartosc %} (wartość {{ sh.wartosc|zl }} zł przy wpłaconych {{ sh.wplacono|zl }} zł){% endif %} — wpłaty i wypłaty konta {{ k }} w tych samych dniach (przeniesienie A→B po wartości z IBKR) zainwestowane w {{ bm_sym(sh, dom) }} po zamknięciu dnia. Porównaj z wynikiem konta {{ k }}.{% endmacro %}
  {% macro xirr(v) %}{{ pct(v, 1) if v is number else (v[:8] if v.startswith('od ') else v) }}{% endmacro %}
  {% macro twr(z) %}{% set t = s.stopy[z].poczatek.twr if s.stopy and s.stopy[z] else none %}{{ pct(t, 1) if t is not none else '—' }}{% endmacro %}
  {{ kafel('c-cal', 'Całość · A + B', w.calosc, 42,
     ['wpłacono ' ~ (w.calosc.wplacono|zl) ~ ' zł · gotówka ' ~ (w.calosc.gotowka_wartosc|zl) ~ ' zł',
      'XIRR ' ~ xirr(s.xirr.calosc) ~ ' · TWR ' ~ twr('calosc')]) }}
  <div class="c-kon">
    {{ kafel('', 'A · główny', w.A, 32,
       ['wpłacono ' ~ (w.A.wplacono|zl) ~ ' zł · gotówka ' ~ (w.A.gotowka_wartosc|zl) ~ ' zł',
        'XIRR ' ~ xirr(s.xirr.A) ~ ' · TWR ' ~ twr('A'),
        bm_sym(s.shadow_a, 'VWCE') ~ ' (te same wpłaty) ' ~ bm_pct(s.shadow_a)], '',
       ['', '', bm_tip(s.shadow_a, 'VWCE', 'A')]) }}
    {{ kafel('', 'B · eksperyment', w.B, 32,
       ['wpłacono ' ~ (w.B.wplacono|zl) ~ ' zł' ~ zrodlo_wplat(w.B) ~ ' · gotówka ' ~ (w.B.gotowka_wartosc|zl) ~ ' zł',
        'XIRR ' ~ xirr(s.xirr.B) ~ ' · TWR ' ~ twr('B'),
        bm_sym(s.shadow, 'IWMO') ~ ' (te same wpłaty) ' ~ bm_pct(s.shadow)], '',
       ['', '', bm_tip(s.shadow, 'IWMO', 'B')]) }}
  </div>

  <section class="c-wyk">
    <div class="hd"><div class="lab" title="Ruch wyłącznie z rynku: punkt dnia = dzisiejsza wartość × indeks TWR dnia / indeks dziś. Wpłaty nie robią skoku.">Wartość · bez wpłat · 30 dni</div><a href="#" data-panel="wykres">Powiększ ↗</a></div>
    <a class="chartbox" href="#" data-panel="wykres" aria-label="Otwórz wykres w panelu" id="mini">
      {% if mini.points %}{{ chart_svg(mini, 'mini', '') }}{% else %}<div class="mut" style="font-size:13px">Brak historii wyceny.</div>{% endif %}
    </a>
  </section>

  <section class="c-str" style="grid-column:1;grid-row:2">
    <div class="hd"><h2>Struktura (bez gotówki)</h2><a href="/zasady">Zasady S1–S4 →</a></div>
    <div style="display:flex;flex-direction:column;gap:12px;font-size:14px">
      {% for x in s.struktura %}
      <div style="display:flex;flex-direction:column;gap:5px">
        <div style="display:flex;justify-content:space-between;gap:8px"><span>{{ x.nazwa }}</span>
          <span><span class="mono {{ '' if x.ok else 'dn' }}">{{ pct(x.udzial, 1, False) }}</span> <span class="mut" style="font-size:13px">{{ 'min' if x.typ=='min' else 'max' }} <span class="mono">{{ pct(x.granica, 0, False) }}</span></span></span></div>
        <div class="bar"><i class="{{ '' if x.ok else 'bad' }}" style="width:{{ x.szer }}%"></i><b style="left:{{ x.kreska }}%"></b></div>
      </div>{% endfor %}
    </div>
    <div style="height:1px;background:var(--track)"></div>
    <div class="hd"><h2>Koszyki A vs pasma</h2><span class="lab">udział · cel · pasmo</span></div>
    <table><tbody>
      {% for k in koszyki %}<tr><td class="tk">{{ k.kod }}</td>
        <td class="r mono {{ '' if k.stan=='w paśmie' or not k.cel else 'dn' }}">{{ pct(k.udzial, 1, False) }}</td>
        <td class="r mono mut">{{ k.cel|zl1 }} · {{ k.pasmo[0]|zl1 }}–{{ k.pasmo[1]|zl1 }}</td>
        <td class="r">{% if k.cel %}<span class="pill {{ 'ok' if k.stan=='w paśmie' else 'bad' }}">{{ k.stan|replace(' pasma','') }}</span>{% endif %}</td></tr>{% endfor %}
    </tbody></table>
    <div class="foot"><span>{% if s.gotowka.A >= 1 %}<span title="Udziały, pasma i limity liczone bez gotówki">Gotówka <span class="mono">{{ s.gotowka.A|zl }} zł</span> poza udziałami</span>{% elif s.limit_b.zamrozony %}Limit B zamrożony do <span class="mono">{{ s.limit_b.prog|zl }} zł</span> zainwestowanej całości{% else %}Limit B: wolne <span class="mono">{{ s.limit_b.wolny|zl }} zł</span>{% endif %}</span><a href="/plan#suwak">Plan · suwak celów →</a></div>
  </section>

  <section class="c-poz" style="grid-column:2;grid-row:2">
    <div class="hd"><h2>Pozycje</h2>
      <div class="chips" role="group" aria-label="Konto">
        <button type="button" class="on" data-konto="">A + B</button><button type="button" data-konto="A">A</button><button type="button" data-konto="B">B</button></div></div>
    <div data-fit style="flex:1;min-height:0;overflow:hidden">
    <table id="poz"><thead><tr><th>Instrument</th><th class="r hide-mob hide-md">Ilość</th><th class="r hide-mob">Kurs</th><th class="r">Wartość zł</th>
      <th class="r hide-mob">Udział</th><th class="r">Dziś</th><th class="r hide-mob hide-md">P&amp;L zł</th><th class="r">P&amp;L</th></tr></thead>
      <tbody>
      {% for p in pozycje %}<tr data-item data-konto="{{ p.konto }}" data-wartosc="{{ p.wartosc or 0 }}" data-dzien="{{ p.zmiana_dzien or 0 }}">
        <td><span class="tk">{{ p.symbol }}</span><span class="konto">{{ p.konto }}</span></td>
        <td class="r mono hide-mob hide-md">{{ p.ilosc|zlg }}</td>
        <td class="r mono hide-mob">{% if p.kurs_ibkr %}<span title="cena IBKR (markPrice z raportu × ruch wg Yahoo) · Yahoo {{ p.kurs|zl2 }} {{ waluta(p.waluta) }}">{{ p.kurs_ibkr|zl2 }} {{ waluta(p.waluta_ibkr) }}</span>{% else %}{{ p.kurs|zl2 }} {{ waluta(p.waluta) }}{% endif %}</td>
        <td class="r mono">{{ p.wartosc|zl }}</td>
        <td class="r mono hide-mob">{{ pct(p.udzial, 1, False) }}</td>
        {% if p.zmiana_netto is none or (p.zmiana_dzien == 0 and not p.zmiana_netto) %}<td class="r mono mut">—</td>{% else %}<td class="r mono {{ 'up' if (p.zmiana_netto_pct or 0)>=0 else 'dn' }}{{ ' mut' if p.zmiana_dzien == 0 else '' }}" {% if p.zmiana_dzien == 0 %}title="brak sesji dziś — zmiana tylko z kursu waluty"{% endif %}>{{ pct(p.zmiana_netto_pct) }}</td>{% endif %}
        <td class="r mono hide-mob hide-md {{ 'up' if (p.pnl or 0)>=0 else 'dn' }}">{{ zlz(p.pnl) }}</td>
        <td class="r mono {{ 'up' if (p.pnl_pct or 0)>=0 else 'dn' }}"><span {% if p.rozbicie %}data-tip tabindex="0"{% endif %}>{{ pct(p.pnl_pct, 1) }}{% if p.rozbicie %}<div class="tipc">
          <div><b>{{ p.symbol }}</b> · wynik {{ zn(p.pnl) }} ({{ pct(p.pnl_pct, 1) }})</div>
          <div class="tr"><span>cena</span>{{ zn(p.rozbicie.cena) }}</div>
          {% if p.kurs_zakupu %}<div class="tr"><span>waluta ({{ p.waluta }}→PLN)</span>{{ zn(p.rozbicie.waluta) }}</div>{% endif %}
          <div class="tr"><span>prowizje</span>{{ zn(p.rozbicie.prowizje) }}</div>
          <div class="dz" style="margin-top:4px;border-top:1px solid var(--line);padding-top:4px">dziś {{ zn(p.dzis.cena + p.dzis.waluta) }}{% if p.kurs_zakupu %} <span class="mut">· cena</span> {{ zk(p.dzis.cena) }} <span class="mut">· waluta</span> {{ zk(p.dzis.waluta) }}{% endif %}</div>
          {% if p.kurs_zakupu %}<div class="mut">kurs zakupu {{ '%.4f'|format(p.kurs_zakupu)|replace('.', ',') }} (IBKR) · dziś {{ '%.4f'|format(p.kurs_dzis)|replace('.', ',') }}</div>{% endif %}
        </div>{% endif %}</span></td></tr>{% endfor %}
      <tr data-more class="hid"><td class="mut" colspan="3">pozostałe (<span data-n></span>)</td><td class="r mono mut" data-suma></td><td colspan="4" class="r"><a href="/etf">wszystkie →</a></td></tr>
      </tbody></table>
    </div>
    <div class="foot"><span>„Dziś” netto (notowania + kurs waluty) · „—” = brak zmiany</span><a href="/etf">Szczegóły pozycji →</a></div>
  </section>

  <div class="c-prawa" style="grid-column:3;grid-row:2;display:flex;flex-direction:column;gap:20px;min-height:0">
    <section class="c-al" data-fitlist>
      <div class="hd"><h2>Alerty</h2><span class="mut" style="font-size:13px">{{ alerty_n.kryt }} krytyczne · {{ alerty_n.ostr }} ostrzeżenia</span></div>
      {% for a in alerty %}<div class="al {{ a.poziom }}" data-item title="{{ a.tekst }}">{{ a.tekst }}</div>{% else %}<div class="mut" style="font-size:14px">Brak naruszeń zasad.</div>{% endfor %}
      <a href="/analiza#alerty" style="font-size:13px" data-more data-base="{{ alerty_wiecej }}" data-info="{{ alerty_n.info }}">+<span data-n>{{ alerty_wiecej }}</span> alertów, {{ alerty_n.info }} informacji →</a>
    </section>
    <section class="c-kal" data-fitlist>
      <div class="hd"><h2>Najbliższe 7 dni</h2><span>{% if zalegle %}<a href="/kalendarz" class="dn">zaległe {{ zalegle }}</a> · {% endif %}<a href="/kalendarz">Kalendarz →</a></span></div>
      <div class="kal">{% for d in kalendarz %}<span class="mono mut" data-item data-para="{{ loop.index }}">{{ d.etykieta }}</span>
        <span class="op" data-item data-para="{{ loop.index }}">{% for e in d.wpisy %}{% if e.ticker %}<span class="tk">{{ e.ticker }}</span> {% endif %}{{ e.opis }}{{ ' · ' if not loop.last }}{% endfor %}</span>{% else %}<span></span><span class="mut">Nic w kalendarzu.</span>{% endfor %}</div>
      <a href="/kalendarz" style="font-size:13px" data-more data-base="0" class="hid">+<span data-n></span> dni →</a>
    </section>
    <section class="c-stf" data-fitlist>
      <div class="hd"><h2>W strefie wejścia</h2><a href="/kandydaci">Kandydaci →</a></div>
      {% for k in strefa %}<div style="display:flex;justify-content:space-between;font-size:14px;padding:3px 0" data-item>
        <span><span class="tk">{{ k.ticker }}</span>{% if k.status=='tak' %}<span class="st-tak">TAK</span>{% else %}<span class="st-watch">watch</span>{% endif %}</span>
        <span class="mono">{{ k.kurs|zl2 }} {{ waluta(k.waluta) }} <span class="mut">· {{ k.wejscie_od|zl1 if k.wejscie_od is not none else '' }}–{{ k.wejscie_do|zl1 if k.wejscie_do is not none else '' }}</span></span></div>
      {% else %}<div class="mut" style="font-size:14px">Żaden kandydat nie jest w strefie.</div>{% endfor %}
      <a href="/kandydaci" style="font-size:13px" data-more data-base="{{ strefa_wiecej }}">+<span data-n>{{ strefa_wiecej }}</span> w strefie →</a>
      <div class="foot"><span>{% if blisko %}Najbliżej: {% for k in blisko %}<span class="tk" style="color:var(--tx)">{{ k.ticker }}</span> <span class="mono">{{ pct(k.odl, 1) }}</span>{{ ' · ' if not loop.last }}{% endfor %}{% endif %}</span></div>
    </section>
  </div>

  <section class="wpl c-wpl" style="grid-column:1 / span 3;grid-row:3">
    <span style="font-weight:600;font-size:16px">Następna wpłata · A3</span>
    <label for="kw" class="mut" title="Wpłata, której jeszcze nie ma na koncie. Wpłata już zaksięgowana jest w gotówce A.">nowa wpłata</label>
    <input id="kw" type="text" inputmode="decimal" value="{{ '%.0f'|format(kwota) }}" class="mono"> <span class="mut">zł</span>
    <label class="mut" style="display:flex;align-items:center;gap:6px;width:auto"><input id="kwg" type="checkbox" checked style="width:auto;min-height:0">{{ 'dolicz gotówkę A (' ~ (s.gotowka.A|zl) ~ ' zł)' if s.gotowka.A >= 1 else 'dolicz gotówkę A' }}</label>
    <button type="button" class="btn-acc" data-panel="wplata">Rozdziel</button>
    {% if ibkr and ibkr.problem %}<a href="/logs" class="ibkr-mob" title="{{ ibkr.tekst }} · {{ ibkr.title }}"><span class="dot bad"></span>IBKR</a>{% endif %}
    <span class="mut hide-mob pasek-st" style="margin-left:auto;font-size:13px">kursy z {{ kursy_data }} {{ kursy_ts }} ·
      {% if worker_ok %}<span class="dot ok"></span>worker OK{% else %}<span class="dot bad"></span><span title="{{ worker_problem }}">worker: problem</span>{% endif %}
      {% if ibkr %}· <a href="/logs" class="ibkr-st{{ ' bad' if ibkr.problem }}" title="{{ ibkr.title }}"><span class="dot {{ 'bad' if ibkr.problem else 'ok' }}"></span>{{ ibkr.tekst }}</a>{% endif %}
      · <a href="/settings">Ustawienia</a>
      · <button type="button" id="odswiez" style="padding:5px 10px;font-size:13px">Odśwież wycenę</button></span>
  </section>
</div>

<div class="scrim hid" id="scrim"></div>
<aside class="panel hid" id="p-wplata" aria-labelledby="pw-t" role="dialog">
  <div class="hd"><h2 id="pw-t">Rozdział wpłaty · A3</h2><button type="button" class="x" data-close aria-label="Zamknij panel">✕</button></div>
  <div id="pw-body" class="mut">Liczę…</div>
</aside>
<aside class="panel szeroki hid" id="p-wykres" aria-labelledby="pk-t" role="dialog">
  <div class="hd"><h2 id="pk-t">Wartość portfela vs wpłacono</h2><button type="button" class="x" data-close aria-label="Zamknij panel">✕</button></div>
  <div style="display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap">
    <div class="chips" role="group" aria-label="Okres" id="pk-dni">
      <button type="button" data-dni="7">7 dni</button><button type="button" data-dni="30">30 dni</button><button type="button" data-dni="90">3 mies.</button>
      <button type="button" data-dni="365">rok</button><button type="button" class="on" data-dni="100000">całość</button></div>
    <div class="chips" role="group" aria-label="Zakres" id="pk-zakres">
      <button type="button" class="on" data-zakres="calosc">Całość</button><button type="button" data-zakres="A">A</button><button type="button" data-zakres="B">B</button></div>
  </div>
  <div id="pk-body" class="mut">Wczytuję…</div>
  <div class="mut" style="font-size:13px">Pełna historia dzienna, miesięczna i roczna oraz eksport CSV: <a href="/history">Historia →</a></div>
</aside>

<script>
(function(){
  var scrim=document.getElementById('scrim'), otw=null;
  function zamknij(){ if(otw){otw.classList.add('hid');} scrim.classList.add('hid'); otw=null; }
  function otworz(id){ zamknij(); otw=document.getElementById(id); otw.classList.remove('hid'); scrim.classList.remove('hid'); }
  scrim.addEventListener('click',zamknij);
  document.querySelectorAll('[data-close]').forEach(function(b){b.addEventListener('click',zamknij)});
  document.addEventListener('keydown',function(e){ if(e.key==='Escape' && otw) zamknij(); });
  function zl(v,d){ if(v===null||v===undefined) return '—'; var n=Number(v), t=Math.abs(n).toFixed(d||0).split('.');
    t[0]=t[0].replace(/\B(?=(\d{3})+(?!\d))/g,' '); return (n<0?'−':'')+t.join(','); }
  function esc(t){ var d=document.createElement('div'); d.textContent=t==null?'':String(t); return d.innerHTML; }

  // --- panel wpłaty (A3)
  function rozdziel(){
    otworz('p-wplata'); var body=document.getElementById('pw-body'); body.textContent='Liczę…';
    var kw=parseFloat((document.getElementById('kw').value||'0').replace(/\s/g,'').replace(',','.'))||0;
    fetch('/api/wplata',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',
      body:JSON.stringify({kwota:kw,z_gotowka:document.getElementById('kwg').checked})})
    .then(function(r){return r.json()}).then(function(k){
      if(!k.przydzial){ body.textContent='Nie udało się policzyć: '+(k.detail||''); return; }
      var h=k.ostrzezenie?'<div class="al ostrzezenie">'+esc(k.ostrzezenie)+'</div>':'';
      h+='<div style="font-size:14px">Do rozdziału <span class="mono" style="color:var(--tx)">'+zl(k.do_rozdzialu)+' zł</span> (w tym gotówka <span class="mono">'+zl(k.gotowka)+' zł</span>) · '+esc((k.kroki||[]).join(' · '))+'</div>';
      h+='<table style="font-size:14px"><thead><tr><th>Koszyk</th><th class="r">Kwota zł</th><th>Instrument</th><th class="r">Udział przed → po</th><th></th></tr></thead><tbody>';
      var zlec=[];
      k.przydzial.forEach(function(p){
        var ins=(p.instrumenty||[]).map(function(i){ zlec.push(i.symbol+': '+i.sztuk+' szt. (~'+zl(i.kwota_waluta,2)+' '+i.waluta+')');
          return '<span class="tk">'+esc(i.symbol)+'</span> <span class="mono">'+zl(i.kwota_waluta,2)+' '+esc(i.waluta)+' ≈ '+i.sztuk+' szt.</span>'; }).join('<br>')||'—';
        h+='<tr><td class="tk">'+esc(p.kod)+'</td><td class="r mono">'+zl(p.kwota)+'</td><td>'+ins+'</td><td class="r mono">'+zl(p.udzial_przed,1)+'% → <b>'+zl(p.udzial_po,1)+'%</b></td>'+
           '<td><span class="pill '+(p.stan_po==='w paśmie'?'ok':'bad')+'">'+esc(p.stan_po)+'</span></td></tr>';
      });
      h+='</tbody></table><div style="font-size:13px">Udziały liczone od wartości zainwestowanej A · sztuki zaokrąglone w dół.</div>';
      h+='<div style="display:flex;gap:10px;margin-top:8px"><a class="btn btn-acc" href="/dziennik/nowy">Zapisz wpis w dzienniku</a><button type="button" id="kopiuj">Kopiuj zlecenia</button></div>';
      body.innerHTML=h;
      document.getElementById('kopiuj').addEventListener('click',function(){
        var t=zlec.join('\n'); (navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(function(){toast('Skopiowano zlecenia')},function(){toast('Nie udało się skopiować')});
      });
    }).catch(function(e){ body.textContent='Błąd: '+e; });
  }

  // --- panel wykresu
  var dni='100000', zakres='calosc';
  function wykres(){
    otworz('p-wykres'); var body=document.getElementById('pk-body'); body.textContent='Wczytuję…';
    fetch('/pulpit/wykres?dni='+dni+'&zakres='+zakres,{credentials:'same-origin'}).then(function(r){return r.text()})
      .then(function(t){ body.innerHTML=t; if(window.walletCharts) window.walletCharts(body); })
      .catch(function(e){ body.textContent='Błąd: '+e; });
  }
  function chips(id,attr,set){
    document.querySelectorAll('#'+id+' button').forEach(function(b){
      b.addEventListener('click',function(){
        document.querySelectorAll('#'+id+' button').forEach(function(x){x.classList.remove('on')});
        b.classList.add('on'); set(b.dataset[attr]); wykres();
      });
    });
  }
  chips('pk-dni','dni',function(v){dni=v}); chips('pk-zakres','zakres',function(v){zakres=v});

  document.querySelectorAll('[data-panel]').forEach(function(el){
    el.addEventListener('click',function(e){ e.preventDefault(); el.dataset.panel==='wplata'?rozdziel():wykres(); });
  });
  document.getElementById('kw').addEventListener('keydown',function(e){ if(e.key==='Enter'){ e.preventDefault(); rozdziel(); } });

  // --- podpowiedzi (rozbicie wyniku): najechanie / dotknięcie; pozycja stała, żeby kafle z overflow jej nie ucinały
  var tb=document.createElement('div'); tb.id='tipbox'; tb.className='hid'; document.body.appendChild(tb);
  function pokazTip(el){ var c=el.querySelector('.tipc'); if(!c) return; tb.innerHTML=c.innerHTML; tb.classList.remove('hid');
    var r=el.getBoundingClientRect(), w=tb.offsetWidth, h=tb.offsetHeight;
    var x=Math.min(Math.max(8, r.right-w), window.innerWidth-w-8), y=r.bottom+6;
    if(y+h>window.innerHeight-8) y=Math.max(8, r.top-h-6);
    tb.style.left=x+'px'; tb.style.top=y+'px'; }
  function ukryjTip(){ tb.classList.add('hid'); }
  document.querySelectorAll('[data-tip]').forEach(function(el){
    el.addEventListener('mouseenter',function(){ pokazTip(el); }); el.addEventListener('mouseleave',ukryjTip);
    el.addEventListener('focus',function(){ pokazTip(el); }); el.addEventListener('blur',ukryjTip);
    el.addEventListener('click',function(e){ e.stopPropagation(); pokazTip(el); });   // telefon: dotknięcie pokazuje, dotknięcie obok chowa
  });
  document.addEventListener('click',ukryjTip); window.addEventListener('scroll',ukryjTip,true);

  // --- toast + odśwież wycenę
  function toast(t){ var d=document.createElement('div'); d.className='toast'; d.textContent=t; document.body.appendChild(d);
    setTimeout(function(){ d.remove(); },4000); }
  var od=document.getElementById('odswiez');
  if(od) od.addEventListener('click',function(){
    var f=new FormData(); f.append('mode','portfolio'); f.append('dry','0'); od.disabled=true;
    fetch('/run',{method:'POST',body:f,credentials:'same-origin'}).then(function(){
      toast('Wycena uruchomiona — odświeżę pulpit za ok. 30 s'); setTimeout(function(){ location.reload(); },30000);
    }).catch(function(e){ toast('Błąd: '+e); od.disabled=false; });
  });

  // --- pozycje: filtr konta + dopasowanie do wysokości kafla (nadmiar → „pozostałe (N)”)
  var konto='';
  function dopasuj(){
    var box=document.querySelector('[data-fit]'); if(!box) return;
    var wiersze=[].slice.call(box.querySelectorAll('tr[data-item]')), more=box.querySelector('tr[data-more]');
    var widoczne=wiersze.filter(function(r){ return !konto || r.dataset.konto===konto; });
    wiersze.forEach(function(r){ r.classList.toggle('hid', widoczne.indexOf(r)<0); });
    more.classList.add('hid');
    var dopasowanie=window.matchMedia('(min-width:1200px)').matches, limit={{ poz_max }};
    var ukryte=[];
    function za_duzo(){ return dopasowanie && box.scrollHeight>box.clientHeight+1; }
    while(widoczne.length>1 && (za_duzo() || widoczne.length>limit)){
      if(!ukryte.length){ more.classList.remove('hid'); }
      var r=widoczne.pop(); r.classList.add('hid'); ukryte.push(r);
    }
    if(ukryte.length){
      more.querySelector('[data-n]').textContent=ukryte.length;
      var s=ukryte.reduce(function(a,r){return a+parseFloat(r.dataset.wartosc||0)},0);
      more.querySelector('[data-suma]').textContent=zl(s);
    }
  }
  document.querySelectorAll('.c-poz .chips button').forEach(function(b){
    b.addEventListener('click',function(){
      document.querySelectorAll('.c-poz .chips button').forEach(function(x){x.classList.remove('on')});
      b.classList.add('on'); konto=b.dataset.konto; dopasuj();
    });
  });
  function dopasujListy(){
    var tryb=window.matchMedia('(min-width:1200px)').matches;
    document.querySelectorAll('[data-fitlist]').forEach(function(sec){
      var it=[].slice.call(sec.querySelectorAll('[data-item]')), more=sec.querySelector('[data-more]');
      it.forEach(function(e){e.classList.remove('hid')});
      var baza=more?parseInt(more.dataset.base||'0',10):0, ukryte=0, para={};
      function pokazMore(n){ if(!more) return; var razem=baza+n; more.classList.toggle('hid', razem<=0 && !(more.dataset.info>0));
        var sp=more.querySelector('[data-n]'); if(sp) sp.textContent=razem; }
      pokazMore(0);
      if(!tryb) return;
      var i=it.length;
      while(i>0 && sec.scrollHeight>sec.clientHeight+1){
        var e=it[--i]; e.classList.add('hid');
        if(e.dataset.para){ if(!para[e.dataset.para]){para[e.dataset.para]=1; ukryte++;} } else ukryte++;
        pokazMore(ukryte);
      }
    });
  }
  function naglowek(){ var h=document.querySelector('body>header'); if(h) document.documentElement.style.setProperty('--hdr', h.offsetHeight+'px'); }
  var tMini=null;
  function mini(){
    var box=document.getElementById('mini'); if(!box || !box.querySelector('.chart-wrap')) return;
    var w=Math.round(box.clientWidth), h=Math.round(box.clientHeight); if(w<120||h<60) return;
    fetch('/pulpit/wykres_mini?w='+w+'&h='+h,{credentials:'same-origin'}).then(function(r){return r.text()})
      .then(function(t){ box.innerHTML=t; if(window.walletCharts) window.walletCharts(box); });
  }
  naglowek();
  window.addEventListener('resize',function(){ naglowek(); dopasuj(); dopasujListy(); clearTimeout(tMini); tMini=setTimeout(mini,250); });
  dopasuj(); dopasujListy(); mini();
})();
</script>
{% endblock %}
"""

WYKRES = r"""
{% if c.chart and c.chart.points %}
{{ chart_svg(c.chart, 'duzy' ~ zakres ~ dni, '') }}
<div class="legend"><span><i class="swatch" style="background:var(--acc)"></i>wartość</span><span><i class="swatch" style="background:var(--cost-line)"></i>wpłacono</span></div>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:16px">
  <div style="border:1px solid var(--line);border-radius:8px;padding:12px 14px"><div class="lab">Zmiana w okresie</div><div class="mono" style="font-size:20px;color:var(--tx)">{{ zlz(zmiana) }} zł · {{ pct(c.pct) }}</div></div>
  <div style="border:1px solid var(--line);border-radius:8px;padding:12px 14px"><div class="lab">Wpłaty w okresie</div><div class="mono" style="font-size:20px;color:var(--tx)">{{ wplaty|zl }} zł</div></div>
  <div style="border:1px solid var(--line);border-radius:8px;padding:12px 14px"><div class="lab">Wynik w okresie · TWR</div><div class="mono" style="font-size:20px;color:var(--tx)">{{ zlz(c.pl_delta) }} zł · {{ pct(twr, 2) }}</div></div>
  <div style="border:1px solid var(--line);border-radius:8px;padding:12px 14px"><div class="lab">Największy spadek (bez wpłat)</div><div class="mono" style="font-size:20px;color:var(--tx)">{{ pct(dd, 1) }}</div></div>
</div>
{% else %}<p class="mut">Brak historii wyceny dla tego zakresu.</p>{% endif %}
"""

WIECEJ = r"""
{% extends "base" %}{% block body %}
<section><h2>Więcej</h2>
  <div style="display:flex;flex-direction:column;gap:4px">
  {% for href,key in nav_wiecej %}<a href="{{ href }}" style="padding:12px 8px;border-bottom:1px solid var(--line);text-decoration:none;color:var(--tx)">{{ t('nav.' ~ key) }} →</a>{% endfor %}
  <a href="/kalendarz" style="padding:12px 8px;border-bottom:1px solid var(--line);text-decoration:none;color:var(--tx)">{{ t('nav.kal') }} →</a>
  <a href="/dziennik" style="padding:12px 8px;border-bottom:1px solid var(--line);text-decoration:none;color:var(--tx)">{{ t('nav.dz') }} →</a>
  <a href="/zasady" style="padding:12px 8px;text-decoration:none;color:var(--tx)">{{ t('nav.zas') }} →</a>
  </div>
</section>
{% endblock %}
"""


def mini_wykres(con, web, w: int = 520, h: int = 150, wycena: dict | None = None) -> dict:
    """P: mini-wykres Pulpitu „Wartość · bez wpłat · 30 dni” (całość A+B). Dziś = wartość całości jak w kaflu."""
    if wycena is None:
        from . import ksiega
        wycena = ksiega.wycena(con, Rynek(con, online=False))
    cal = wycena["calosc"]
    seria = portfel.seria_bez_wplat(portfel.seria_dzienna(con, "calosc"), cal.get("wartosc"), cal.get("wplacono"))
    c = web.line_chart(seria, w=w, h=h, left=54, xcount=max(2, w // 160))
    fmt = getattr(web, "_fmt_int", lambda v: f"{v:,.0f}".replace(",", " "))
    for pt, r in zip(c.get("points") or [], seria):
        pt.update(bw=1, saldo=fmt(r["saldo"]), wpl=fmt(r["wplacono"]))
    return c


def zarejestruj(app, env, render, auth, web):
    """web: moduł app.web (build/line_chart, daily_series, szablon HOME z makrem chart_svg)."""
    home = env.loader.mapping["home"]
    a, b = home.index("{% macro chart_svg"), home.index("{% endmacro %}") + len("{% endmacro %}")
    makro = home[a:b]
    env.loader.mapping.update({"pulpit2": PULPIT.replace("{% block body %}", makro + "\n{% block body %}", 1),
                               "pulpit_wykres": makro + WYKRES, "wiecej": WIECEJ,
                               "pulpit_mini": makro + "{% if c.points %}{{ chart_svg(c, 'mini', '') }}{% endif %}"})
    env.globals["pct"] = _pct
    env.globals["zlz"] = lambda v: _pl(v, 0, True)
    env.globals["waluta"] = lambda c: {"EUR": "€", "USD": "$", "PLN": "zł", "GBP": "£", "GBp": "p"}.get(c or "", c or "")
    env.filters.setdefault("zl", lambda v: _pl(v, 0))
    env.filters["zl1"] = lambda v: "" if v is None else (f"{round(v, 1):g}".replace(".", ","))
    env.filters["zl2"] = lambda v: _pl(v, 2)
    env.filters["zlg"] = lambda v: "" if v is None else f"{v:g}".replace(".", ",")

    @app.get("/", response_class=HTMLResponse)
    def pulpit_glowny(request: Request, _=Depends(auth)):
        con = store.con()
        try:
            d = dane_pulpitu(con)
            mini = mini_wykres(con, web, wycena=d["w"])
        finally:
            con.close()
        return render("pulpit2", title="Pulpit", page="home", msg=request.query_params.get("msg"), mini=mini,
                      poz_max=POZYCJE_MAX, **d)

    @app.get("/pulpit/wykres", response_class=HTMLResponse)
    def pulpit_wykres(dni: int = 100000, zakres: str = "calosc", _=Depends(auth)):
        if zakres not in ("calosc", "A", "B"):
            zakres = "calosc"
        dni = max(2, min(int(dni), 100000))
        seria = web.daily_series(dni, zakres)
        while len(seria) > 1 and not seria[0]["value_f"] and not seria[1]["value_f"]:
            seria = seria[1:]   # dni przed otwarciem konta (wartość 0) — zostaje jeden punkt startowy
        c = {"chart": web.line_chart(seria, w=1000, h=460, left=72, xcount=6), "pct": None, "pl_delta": None}
        vals = [r["value_f"] for r in seria]
        costs = [r["cost_f"] for r in seria]
        zmiana = wplaty = dd = twr = None
        if len(vals) > 1:
            wplaty = costs[-1] - costs[0]
            zmiana = vals[-1] - vals[0]
            c["pct"] = (vals[-1] / vals[0] - 1) * 100 if vals[0] else None
            c["pl_delta"] = (vals[-1] - costs[-1]) - (vals[0] - costs[0])
            # TWR: dzienne stopy bez wpłat (przepływ = zmiana kapitału wpłaconego), spadek od szczytu indeksu
            idx, szczyt, dd = 1.0, 1.0, 0.0
            for i in range(1, len(vals)):
                baza = vals[i - 1] + (costs[i] - costs[i - 1])   # wpłata na początku dnia (jak IBKR, portfel.twr_okres)
                if baza > 0:
                    idx *= vals[i] / baza
                szczyt = max(szczyt, idx)
                dd = min(dd, (idx / szczyt - 1) * 100)
            twr = (idx - 1) * 100
        return HTMLResponse(env.get_template("pulpit_wykres").render(c=c, zakres=zakres, dni=dni, zmiana=zmiana,
                                                                    wplaty=wplaty, dd=dd, twr=twr))

    @app.get("/pulpit/wykres_mini", response_class=HTMLResponse)
    def pulpit_wykres_mini(w: int = 520, h: int = 150, _=Depends(auth)):
        w, h = max(160, min(int(w), 2000)), max(80, min(int(h), 1200))
        con = store.con()
        try:
            c = mini_wykres(con, web, w, h)
        finally:
            con.close()
        return HTMLResponse(env.get_template("pulpit_mini").render(c=c))

    @app.get("/wiecej", response_class=HTMLResponse)
    def wiecej(request: Request, _=Depends(auth)):
        return render("wiecej", title="Więcej", page="wiecej")
