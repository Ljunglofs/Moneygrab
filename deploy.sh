#!/usr/bin/env bash
# GRABIT · uppdatera servern till senaste main
#
#   ./deploy.sh
#
# Hämtar koden, bygger om imagen och startar om appen. Caddy rörs inte, så
# certifikatet påverkas inte. Gamla images rensas så disken inte växer.
set -euo pipefail
cd "$(dirname "$0")"

echo "== hämtar senaste main"
git fetch origin main
git reset --hard origin/main

echo "== bygger och startar"
docker compose up -d --build

echo "== väntar på att appen svarar"
for i in $(seq 1 30); do
  if docker compose exec -T grabit curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    echo "   uppe efter ${i}0 sekunder"
    docker image prune -f >/dev/null
    exit 0
  fi
  sleep 10
done

echo "!! appen svarade inte på fem minuter — loggen:"
docker compose logs --tail=50 grabit
exit 1
