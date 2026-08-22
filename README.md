# KRK Transit — QNAP TS-230 Deployment

## Files needed on the QNAP

```
/share/Container/krktracker/
├── Dockerfile
├── proxy.py
├── krakow-tracker.html
└── gtfs_cache/
    ├── GTFS_KRK_T.zip
    └── GTFS_KRK_A.zip
```

The GTFS ZIPs are baked into the image at build time — container starts instantly, no downloading.

---

## Step-by-step deployment

### 1. Copy files to QNAP via SCP (from your PC)

```bash
scp Dockerfile proxy.py krakow-tracker.html admin@QNAP-IP:/share/Container/krktracker/
scp -r gtfs_cache/ admin@QNAP-IP:/share/Container/krktracker/
```

### 2. SSH into the QNAP

```bash
ssh admin@QNAP-IP
```

### 3. Build and run

```bash
cd /share/Container/krktracker

sudo docker build -t krktracker .

sudo docker run -d \
  --name krktracker \
  --restart unless-stopped \
  -p 6070:3000 \
  krktracker
```

### 4. Open the tracker

```
http://QNAP-IP:6070
```

---

## Why no volume mount?

The GTFS ZIPs are baked into the image. The proxy caches them for 24h — after that
it re-downloads fresh ones from ZTP Kraków automatically and keeps them in the container.
This is fine since containers are rarely rebuilt. If you want the refreshed cache to survive
rebuilds, add a volume back:

```bash
sudo docker run -d \
  --name krktracker \
  --restart unless-stopped \
  -p 6070:3000 \
  -v /share/Container/krktracker/gtfs_cache:/app/gtfs_cache \
  krktracker
```

---

## Updating files without rebuild

HTML changes — copy and done (served live):
```bash
sudo docker cp ./krakow-tracker.html krktracker:/app/krakow-tracker.html
```

Python changes — copy then restart:
```bash
sudo docker cp ./proxy.py krktracker:/app/proxy.py
sudo docker restart krktracker
```

Full rebuild needed only for: Dockerfile changes, new GTFS ZIPs baked in.

---

## Useful commands

```bash
sudo docker logs krktracker -f                        # live logs
sudo docker exec krktracker ls -lh /app/gtfs_cache    # check GTFS files
sudo docker ps                                         # verify running
sudo docker stop krktracker && sudo docker rm krktracker  # stop before rebuild
```

---

## First start

Because GTFS is baked in, the server is ready in a few seconds. Logs will show:

```
[CACHE] GTFS_KRK_T.zip aktualny (< 24h)
[CACHE] GTFS_KRK_A.zip aktualny (< 24h)
[GTFS] stops.txt: 3421 przystanków
[GTFS] Załadowano: X linii, Y kursów
╔══════════════════════════════════════════╗
║      KRK Transit — Proxy serwer          ║
╠══════════════════════════════════════════╣
║  Tracker:  http://localhost:3000          ║
```
