FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir gtfs-realtime-bindings protobuf

# Copy app files
COPY proxy.py .
COPY krakow-tracker.html .

# Bake in pre-downloaded GTFS cache so container starts instantly
# (no downloading from ZTP on first boot — cache is valid for 24h from file mtime)
RUN mkdir -p /app/gtfs_cache
COPY gtfs_cache/GTFS_KRK_T.zip /app/gtfs_cache/GTFS_KRK_T.zip
COPY gtfs_cache/GTFS_KRK_A.zip /app/gtfs_cache/GTFS_KRK_A.zip

EXPOSE 3000

CMD ["python", "proxy.py"]
