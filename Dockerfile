# GRABIT · körbar överallt där Docker finns
#
# Byggd för att flytta bort från Render utan att röra appkoden. Samma image
# kör på en gratis Oracle-maskin, på en Hetzner-server eller lokalt.
#
#   docker compose up -d --build
#
# Basen är slim, inte alpine: pandas och numpy har färdiga hjul för glibc men
# måste kompileras från källkod på musl, vilket tar tjugo minuter i stället för
# en. python:3.12 för att matcha det Render körde.
FROM python:3.12-slim

# curl behövs för HEALTHCHECK nedan. tzdata så loggarna visar svensk tid.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Stockholm \
    DATA_DIR=/data

WORKDIR /app

# Beroendena först: så länge requirements_api.txt är oförändrad återanvänds
# lagret och ombyggen tar sekunder i stället för minuter.
COPY requirements_api.txt ./
RUN pip install --no-cache-dir -r requirements_api.txt

COPY . .

# DATA_DIR är allt som måste överleva en omstart: push-prenumeranter,
# desk-state, larm, besöksräknare. Monteras som volym i compose-filen.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# Render hade healthCheckPath /api/health — samma kontroll här, så en hängd
# app startas om av Docker i stället för att stå och tiga.
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# En arbetare. Appen håller state i minnet (desk, kvoter, cache) och startar
# bakgrundstrådar vid uppstart — flera arbetare skulle ge varje process en egen
# kopia av staten och dubbla alla larm.
CMD ["uvicorn", "grabit_entry:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
