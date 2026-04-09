# 🚋 Cracow Public Transport Viewer

**Twoje okno na krakowską komunikację miejską na żywo**  
Tramwaje, autobusy i alerty ZTP Kraków w jednym przyjemnym interfejsie.

[Krakowski Transport Publiczny Live]<img width="1616" height="1245" alt="cptv" src="https://github.com/user-attachments/assets/f2328323-5090-4abe-80ac-fbc390f4b508" />


---

## Co to jest?

Prosty, szybki i ładny tracker komunikacji miejskiej w Krakowie.  
Działa lokalnie – pobierasz dane prosto z oficjalnych feedów GTFS i GTFS-RT ZTP Kraków i wyświetlasz je na mapie + w przejrzystym panelu bocznym.

Zero chmurki, zero rejestracji, zero śledzenia. Tylko Ty i Kraków na żywo.

### Główne funkcje:
- Pojazdy na żywo na mapie (tramwaje + autobusy)
- Opóźnienia w czasie rzeczywistym
- Alerty serwisowe
- Wyszukiwarka linii i kierunków
- Rozkład jazdy dla wybranego przystanku
- Ładny, ciemny interfejs (bo w Krakowie wieczorem też jeździmy)
- Działa całkowicie offline po pobraniu (poza danymi z ZTP)

---

## Jak uruchomić?

### 1. Szybki start (zalecany)

```bash
git clone https://github.com/TWOJA_NAZWA/krk-transit.git
cd krk-transit

# Zainstaluj zależności (tylko jedna biblioteka)
pip install gtfs-realtime-bindings

# Uruchom proxy
python proxy.py


Otwórz w przeglądarce: http://localhost:3000
Gotowe!
```

2. Wymagania

Python 3.8+
gtfs-realtime-bindings (do parsowania protobufów)


Struktura projektu

proxy.py – lekki serwer proxy + parser GTFS-RT + GTFS statyczny
krakow-tracker.html – cały frontend (Leaflet + czysty JS)
gtfs_cache/ – automatycznie pobierane i cachowane pliki GTFS (24h)


Cechy techniczne

Cache GTFS na 24 godziny (szybkie starty)
Ochrona przed niekompletnymi feedami od ZTP
Inteligentne pomijanie ostatniego przystanku w rozkładzie
Wbudowany fallback na dane demo (gdy ZTP nie odpowiada)
Pełne wsparcie CORS + prosty serwer HTTP na socketach


Dla deweloperów
Chcesz dorzucić coś swojego?

Frontend jest w jednym pliku HTML + JS (łatwo hackować)
Wszystkie dane pobierasz z endpointu /feed
Dodatkowe endpointy: /stops?q=... i /stop_times?stop_id=...


TODO / Pomysły na przyszłość

 Piękniejsze markery z numerami linii
 Tryb „najbliższe odjazdy” przy kliknięciu w mapę
 Eksport do KML / GPX
 Powiadomienia o opóźnieniach wybranej linii
 Wsparcie dla nocnych linii


Autor
Zrobione z miłości do Krakowa i dobrych trackerów komunikacji miejskiej ❤️
Jeśli Ci się podoba — daj gwiazdkę ⭐

Licencja
MIT – rób co chcesz, tylko nie mów że to Twój projekt jak wrzucisz na TikToka bez zmian ;)

Made in Kraków • Jeżdżę, więc wiem jak to wygląda z okna 18-tki




---

### EN - Cracow Public Transport Viewer

**Real-time Kraków public transport tracker**

Trams, buses and service alerts from ZTP Kraków in one clean interface.

### Features
- Live vehicles on the map
- Real-time delays
- Service alerts
- Stop timetable
- Clean dark UI
- Works locally – no tracking, no accounts

### Quick start

```bash
pip install gtfs-realtime-bindings
python proxy.py
