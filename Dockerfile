FROM python:3.10-slim
LABEL maintainer="vibte-pipeline"
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    VIBTE_WORKROOT=/var/vibte

# Optional: build megatools (megaput/megals) for MEGA uploads.
# Requires github.com/megous access at build time. Default off.
#   docker build --build-arg INSTALL_MEGATOOLS=true .
ARG INSTALL_MEGATOOLS=false

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fonts-freefont-ttf curl git \
    && rm -rf /var/lib/apt/lists/*

RUN if [ "$INSTALL_MEGATOOLS" = "true" ]; then \
    set -e; \
    apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config meson ninja-build libglib2.0-dev libssl-dev libcurl4-openssl-dev; \
    curl -fsSL https://github.com/megous/megatools/archive/refs/tags/1.11.1.tar.gz | tar xz; \
    cd megatools-1.11.1 && meson setup build && ninja -C build && ninja -C build install; \
    cd / && rm -rf /megatools-1.11.1; \
    apt-get purge -y build-essential pkg-config meson ninja-build libglib2.0-dev libssl-dev libcurl4-openssl-dev; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*; \
fi

WORKDIR /app
COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY scripts /app/scripts

RUN mkdir -p /var/vibte/work /var/vibte/hermes /var/vibte/registry /var/vibte/uploads /var/vibte/artifacts && \
    chmod +x /app/scripts/*.py

EXPOSE 8000
ENV NINEROUTER_URL=http://127.0.0.1:20128/v1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]