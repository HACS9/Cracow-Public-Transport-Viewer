#!/usr/bin/env python3
"""
KRK Transit — proxy serwer z parserem GTFS-RT + GTFS statyczny
Uruchomienie: py proxy.py
Potem otwórz: http://localhost:3000
"""

import socket
import threading
import urllib.request
import json
import os
import sys
import time
import zipfile
import csv
import io

PORT = int(os.environ.get("PORT", 3000))
ZTP_BASE  = "https://gtfs.ztp.krakow.pl/"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR  = os.path.join(SCRIPT_DIR, "gtfs_cache")
CACHE_MAX_AGE = 24 * 3600  # 24 godziny

CORS = (
    "Access-Control-Allow-Origin: *\r\n"
    "Access-Control-Allow-Methods: GET, OPTIONS\r\n"
)

try:
    from google.transit import gtfs_realtime_pb2
    HAS_GTFS = True
    print("[OK] gtfs-realtime-bindings załadowane")
except ImportError:
    HAS_GTFS = False
    print("[WARN] Brak gtfs-realtime-bindings — zainstaluj: py -m pip install gtfs-realtime-bindings")

# ── Globalne słowniki (ładowane przy starcie) ──
# trip_id → {"route_id": "...", "headsign": "..."}
TRIP_INFO: dict = {}
# route_id → short_name (np. "8", "173")
ROUTE_NAMES: dict = {}
# stop_id → {"name": "...", "lat": "...", "lon": "..."}
STOPS: dict = {}
# stop_id → [{"time": "HH:MM", "route": "...", "headsign": "..."}]
STOP_TIMES: dict = {}


# ════════════════════════════════════════════
# GTFS STATIC — pobieranie i cache
# ════════════════════════════════════════════

def cache_path(filename):
    return os.path.join(CACHE_DIR, filename)

def is_fresh(filepath):
    """Czy plik istnieje i ma mniej niż 24h?"""
    if not os.path.exists(filepath):
        return False
    age = time.time() - os.path.getmtime(filepath)
    return age < CACHE_MAX_AGE

def fetch_ztp(filename, timeout=15):
    url = ZTP_BASE + filename
    print(f"[ZTP] Pobieranie: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 KRKTransit/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    print(f"[ZTP] OK: {filename} — {len(data)/1024:.1f} KB")
    return data

def load_gtfs_static():
    """Pobiera GTFS_KRK_T.zip i GTFS_KRK_A.zip, parsuje trips.txt i routes.txt."""
    global TRIP_INFO, ROUTE_NAMES
    os.makedirs(CACHE_DIR, exist_ok=True)

    for zip_name in ["GTFS_KRK_T.zip", "GTFS_KRK_A.zip"]:
        zpath = cache_path(zip_name)
        if is_fresh(zpath):
            print(f"[CACHE] {zip_name} aktualny (< 24h)")
        else:
            try:
                data = fetch_ztp(zip_name, timeout=30)
                with open(zpath, "wb") as f:
                    f.write(data)
                print(f"[CACHE] Zapisano: {zpath}")
            except Exception as e:
                print(f"[BŁĄD] Nie można pobrać {zip_name}: {e}")
                continue

        # Parsuj ZIP
        try:
            with zipfile.ZipFile(zpath, "r") as z:
                # routes.txt → route_id → short_name
                if "routes.txt" in z.namelist():
                    with z.open("routes.txt") as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                        for row in reader:
                            rid  = row.get("route_id", "").strip()
                            name = row.get("route_short_name", "").strip()
                            if rid and name:
                                ROUTE_NAMES[rid] = name

                # trips.txt → trip_id → {route_id, headsign}
                if "trips.txt" in z.namelist():
                    with z.open("trips.txt") as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                        for row in reader:
                            tid      = row.get("trip_id", "").strip()
                            rid      = row.get("route_id", "").strip()
                            headsign = row.get("trip_headsign", "").strip()
                            if tid:
                                TRIP_INFO[tid] = {
                                    "route_id": rid,
                                    "headsign": headsign or None,
                                }

                # stops.txt → stop_id → {name, lat, lon}
                if "stops.txt" in z.namelist():
                    with z.open("stops.txt") as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                        for row in reader:
                            sid = row.get("stop_id", "").strip()
                            if sid:
                                STOPS[sid] = {
                                    "name": row.get("stop_name", "").strip(),
                                    "lat":  row.get("stop_lat",  ""),
                                    "lon":  row.get("stop_lon",  ""),
                                }
                    print(f"[GTFS] stops.txt: {len(STOPS)} przystanków")

                # stop_times.txt → stop_id → [{time, route, headsign}]
                # Dwa przejścia: najpierw max stop_sequence per trip → pomijamy ostatni przystanek (koniec kursu)
                if "stop_times.txt" in z.namelist():
                    print(f"[GTFS] Parsowanie stop_times.txt — przejście 1/2 (max sekwencje)…")
                    trip_max_seq = {}
                    with z.open("stop_times.txt") as f2:
                        reader = csv.DictReader(io.TextIOWrapper(f2, encoding="utf-8-sig"))
                        for row in reader:
                            tid = row.get("trip_id", "").strip()
                            try:
                                seq = int(row.get("stop_sequence", "0"))
                            except ValueError:
                                seq = 0
                            if tid and seq > trip_max_seq.get(tid, -1):
                                trip_max_seq[tid] = seq

                    print(f"[GTFS] Parsowanie stop_times.txt — przejście 2/2 (odjazdy)…")
                    count = 0
                    with z.open("stop_times.txt") as f2:
                        reader = csv.DictReader(io.TextIOWrapper(f2, encoding="utf-8-sig"))
                        for row in reader:
                            sid = row.get("stop_id", "").strip()
                            tid = row.get("trip_id", "").strip()
                            dep = row.get("departure_time", "").strip()
                            if not (sid and tid and dep):
                                continue
                            try:
                                seq = int(row.get("stop_sequence", "0"))
                            except ValueError:
                                seq = 0
                            if seq == trip_max_seq.get(tid, -1):
                                continue  # ostatni przystanek = koniec kursu, nie odjeżdża dalej
                            info     = TRIP_INFO.get(tid, {})
                            rid      = info.get("route_id", "")
                            route    = ROUTE_NAMES.get(rid, rid)
                            headsign = info.get("headsign") or ""
                            if sid not in STOP_TIMES:
                                STOP_TIMES[sid] = []
                            STOP_TIMES[sid].append({
                                "time":     dep[:5],  # "HH:MM" (może być >23, np. "25:30")
                                "route":    route,
                                "headsign": headsign,
                            })
                            count += 1
                    print(f"[GTFS] stop_times.txt: {count} odjazdów dla {len(STOP_TIMES)} przystanków")

        except Exception as e:
            print(f"[BŁĄD] Parsowanie {zip_name}: {e}")

    print(f"[GTFS] Załadowano: {len(ROUTE_NAMES)} linii, {len(TRIP_INFO)} kursów")


def enrich_vehicle(v):
    """Uzupełnia routeId i headsign z GTFS statycznego."""
    trip_id = v.get("tripId")
    if trip_id and trip_id in TRIP_INFO:
        info = TRIP_INFO[trip_id]
        rid  = info.get("route_id")
        if rid:
            v["routeId"]  = ROUTE_NAMES.get(rid, rid)  # short name lub route_id jako fallback
            v["headsign"] = v["headsign"] or info.get("headsign")
    return v


# ════════════════════════════════════════════
# GTFS-RT PARSERS
# ════════════════════════════════════════════

def parse_vehicle_positions(raw, vehicle_type):
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)
    vehicles = []
    for entity in feed.entity:
        try:
            if not entity.HasField("vehicle"):
                continue
            vp = entity.vehicle
            if not vp.HasField("position"):
                continue
            pos = vp.position
            v = {
                "vehicleId":    getattr(vp.vehicle, "id",    None) or entity.id,
                "vehicleLabel": getattr(vp.vehicle, "label", None) or getattr(vp.vehicle, "id", None),
                "routeId":      getattr(vp.trip, "route_id", None) or None,
                "tripId":       getattr(vp.trip, "trip_id",  None) or None,
                "headsign":     None,
                "lat":          round(pos.latitude,  6),
                "lng":          round(pos.longitude, 6),
                "speed":        round(pos.speed, 2) if pos.speed else None,
                "bearing":      round(pos.bearing, 1) if pos.bearing else None,
                "type":         vehicle_type,
                "delay":        None,
            }
            enrich_vehicle(v)
            vehicles.append(v)
        except Exception as e:
            print(f"[SKIP entity] {e}")
    return vehicles


def parse_trip_updates(raw):
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)
    delays = {}
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        trip_id    = getattr(tu.trip, "trip_id", None)
        vehicle_id = getattr(tu.vehicle, "id", None) if tu.HasField("vehicle") else None
        delay = None
        for stu in tu.stop_time_update:
            try:
                if stu.HasField("departure") and stu.departure.HasField("delay"):
                    delay = stu.departure.delay
                    break
                if stu.HasField("arrival") and stu.arrival.HasField("delay"):
                    delay = stu.arrival.delay
                    break
            except Exception:
                continue
        if trip_id:
            delays[trip_id] = delay
        if vehicle_id:
            delays["v|" + vehicle_id] = delay
    return delays


def parse_alerts(raw, vehicle_type):
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)
    alerts = []
    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue
        al = entity.alert
        header, desc = "", ""
        for t in al.header_text.translation:
            if t.text: header = t.text; break
        for t in al.description_text.translation:
            if t.text: desc = t.text; break
        routes = [ie.route_id for ie in al.informed_entity if ie.route_id]
        # Zamień route_id na short_name
        routes = [ROUTE_NAMES.get(r, r) for r in routes]
        if header or desc:
            alerts.append({"header": header, "desc": desc, "routes": routes, "type": vehicle_type})
    return alerts


# ── Cache ostatniego dobrego feedu ──
last_good_feed: dict = {}
last_good_count: int = 0


def build_feed():
    global last_good_feed, last_good_count
    vehicles, delays, alerts, errors = [], {}, [], []

    for filename, vtype in [("VehiclePositions_T.pb", "tram"), ("VehiclePositions_A.pb", "bus")]:
        try:
            raw = fetch_ztp(filename)
            if len(raw) < 100:
                print(f"[SKIP] {filename}: zbyt mały plik ({len(raw)} B) — pomijam")
                continue
            vv  = parse_vehicle_positions(raw, vtype)
            vehicles.extend(vv)
            print(f"[PARSE] {filename}: {len(vv)} pojazdów")
        except Exception as e:
            print(f"[BŁĄD] {filename}: {e}")
            errors.append(str(e))

    for filename in ["TripUpdates_T.pb", "TripUpdates_A.pb"]:
        try:
            raw = fetch_ztp(filename)
            if len(raw) < 100:
                continue
            d   = parse_trip_updates(raw)
            delays.update(d)
            print(f"[PARSE] {filename}: {len(d)} opóźnień")
        except Exception as e:
            print(f"[BŁĄD] {filename}: {e}")

    for filename, vtype in [("ServiceAlerts_T.pb", "tram"), ("ServiceAlerts_A.pb", "bus")]:
        try:
            raw = fetch_ztp(filename)
            if len(raw) < 10:
                continue
            aa  = parse_alerts(raw, vtype)
            alerts.extend(aa)
            print(f"[PARSE] {filename}: {len(aa)} alertów")
        except Exception as e:
            print(f"[BŁĄD] {filename}: {e}")

    # Wzbogać pojazdy o opóźnienia
    for v in vehicles:
        tid = v.get("tripId")
        vid = v.get("vehicleId")
        if tid and tid in delays and delays[tid] is not None:
            v["delay"] = delays[tid]
        elif vid and ("v|" + vid) in delays and delays["v|" + vid] is not None:
            v["delay"] = delays["v|" + vid]

    new_feed = {"vehicles": vehicles, "alerts": alerts, "errors": errors, "count": len(vehicles)}

    # Jeśli nowy feed ma mniej niż 60% poprzedniego — ZTP zwróciło niekompletne dane
    if last_good_count > 50 and len(vehicles) < last_good_count * 0.6:
        print(f"[WARN] Niekompletny feed ({len(vehicles)} vs poprzedni {last_good_count}) — używam ostatniego dobrego")
        return last_good_feed

    # Zapisz jako ostatni dobry feed
    last_good_feed  = new_feed
    last_good_count = len(vehicles)
    return new_feed


# ════════════════════════════════════════════
# HTTP SERVER (raw socket)
# ════════════════════════════════════════════

def send_response(conn, status, content_type, body: bytes):
    header = (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n"
        f"{CORS}"
        f"\r\n"
    )
    conn.sendall(header.encode("utf-8") + body)

def send_error(conn, code, message):
    body = json.dumps({"error": message}).encode("utf-8")
    send_response(conn, f"{code} Error", "application/json", body)

def handle(conn, addr):
    try:
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = conn.recv(4096)
            if not chunk:
                return
            raw += chunk

        first_line = raw.split(b"\r\n")[0].decode("utf-8", errors="replace")
        parts = first_line.split(" ")
        if len(parts) < 2:
            return

        method = parts[0]
        path   = parts[1].split("?")[0]
        print(f"[HTTP] {addr[0]}  {method} {path}")

        if method == "OPTIONS":
            conn.sendall(f"HTTP/1.1 204 No Content\r\n{CORS}Content-Length: 0\r\nConnection: close\r\n\r\n".encode())
            return
        if method != "GET":
            send_error(conn, 405, "Method not allowed")
            return

        if path in ("/", "/index.html"):
            html_path = os.path.join(SCRIPT_DIR, "krakow-tracker.html")
            if not os.path.exists(html_path):
                send_error(conn, 404, "Brak krakow-tracker.html obok proxy.py")
                return
            with open(html_path, "rb") as f:
                body = f.read()
            send_response(conn, "200 OK", "text/html; charset=utf-8", body)
            return

        if path == "/feed":
            if not HAS_GTFS:
                send_error(conn, 503, "Brak gtfs-realtime-bindings")
                return
            try:
                data = build_feed()
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                send_response(conn, "200 OK", "application/json; charset=utf-8", body)
            except Exception as e:
                print(f"[BŁĄD] build_feed: {e}")
                send_error(conn, 500, str(e))
            return

        if path == "/stops":
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(parts[1]).query).get("q", [""])[0].lower()
            results = [
                {"stop_id": sid, "name": info["name"]}
                for sid, info in STOPS.items()
                if q in info["name"].lower()
            ][:20]
            body = json.dumps(results, ensure_ascii=False).encode("utf-8")
            send_response(conn, "200 OK", "application/json; charset=utf-8", body)
            return

        if path == "/stop_times":
            from urllib.parse import urlparse, parse_qs
            stop_id = parse_qs(urlparse(parts[1]).query).get("stop_id", [""])[0]
            deps = sorted(STOP_TIMES.get(stop_id, []), key=lambda x: x["time"])
            body = json.dumps({"departures": deps}, ensure_ascii=False).encode("utf-8")
            send_response(conn, "200 OK", "application/json; charset=utf-8", body)
            return

        send_error(conn, 404, f"Nie znaleziono: {path}")

    except Exception as e:
        print(f"[WYJĄTEK] {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main():
    # Załaduj GTFS statyczny przed uruchomieniem serwera
    print("[START] Ładowanie GTFS statycznego…")
    load_gtfs_static()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(32)

    print()
    print("╔══════════════════════════════════════════╗")
    print("║      KRK Transit — Proxy serwer          ║")
    print("╠══════════════════════════════════════════╣")
    print(f"║  Tracker:  http://localhost:{PORT}           ║")
    print(f"║  Feed:     http://localhost:{PORT}/feed      ║")
    print("╚══════════════════════════════════════════╝")
    print()
    print("Zatrzymaj serwer: Ctrl+C")
    print()

    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=handle, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\nSerwer zatrzymany.")
    finally:
        srv.close()
        sys.exit(0)


if __name__ == "__main__":
    main()
