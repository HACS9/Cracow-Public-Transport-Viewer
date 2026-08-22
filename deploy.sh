#!/bin/sh
# KRK Transit — deploy script for QNAP TS-230 (ARM64)
# Run this on the QNAP via SSH

PROJECT="krktracker"
HOST_PORT="6070"
CONTAINER_PORT="3000"

# Stop and remove old container if it exists
sudo docker stop "$PROJECT" 2>/dev/null && sudo docker rm "$PROJECT" 2>/dev/null
echo "Old container removed (if existed)"

# Build image (GTFS ZIPs are baked in — no volume needed)
sudo docker build -t "$PROJECT" /share/Container/"$PROJECT"/

# Run container
sudo docker run -d \
  --name "$PROJECT" \
  --restart unless-stopped \
  -p "${HOST_PORT}:${CONTAINER_PORT}" \
  "$PROJECT"

echo ""
echo "✓ krktracker running at http://QNAP-IP:${HOST_PORT}"
echo ""
echo "Useful commands:"
echo "  sudo docker logs $PROJECT -f"
echo "  sudo docker exec $PROJECT ls /app/gtfs_cache"
