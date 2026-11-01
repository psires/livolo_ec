# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.13-slim-bookworm
FROM ${PYTHON_IMAGE} AS builder
ARG RELEASE_VERSION=0.12
WORKDIR /src
COPY VERSION ./VERSION
COPY host/pyproject.toml ./host/pyproject.toml
COPY host/LICENSE ./host/LICENSE
COPY host/docker/requirements-firmware.txt ./host/docker/requirements-firmware.txt
COPY host/livolod ./host/livolod
RUN python -c 'import pathlib, tomllib; from host.livolod import __version__; expected = pathlib.Path("VERSION").read_text().strip(); assert expected == __version__ == tomllib.loads(pathlib.Path("host/pyproject.toml").read_text())["project"]["version"]' \
    && test "$(cat VERSION)" = "$RELEASE_VERSION" \
    && python -m pip wheel --no-cache-dir --no-deps --wheel-dir /wheels ./host \
    && python -m pip download --no-cache-dir --only-binary=:all: \
       --dest /wheels -r host/docker/requirements-firmware.txt paho-mqtt==2.1.0 \
    && python -m pip download --no-cache-dir --no-deps --no-binary=:all: \
       --dest /sources universal-silabs-flasher==0.0.31 \
    && python -c 'import hashlib, pathlib; p = pathlib.Path("/sources/universal_silabs_flasher-0.0.31.tar.gz"); assert hashlib.sha256(p.read_bytes()).hexdigest() == "7f7a4a744273277430cbff19a1c7522372b434ab147adcbc821395566c3a6a14"'

FROM ${PYTHON_IMAGE} AS runtime
ARG RELEASE_VERSION=0.12
LABEL org.opencontainers.image.title="Livolo EC daemon" \
      org.opencontainers.image.description="Livolo EC UART to MQTT daemon" \
      org.opencontainers.image.licenses="BSD-2-Clause" \
      org.opencontainers.image.version="${RELEASE_VERSION}"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY --from=builder /sources /usr/share/livolo-ec/sources
RUN --mount=type=bind,from=builder,source=/wheels,target=/wheels \
    python -m pip install --no-cache-dir --no-index --find-links=/wheels \
       "livolo-ec-daemon[mqtt,flash]==${RELEASE_VERSION}" \
    && python -m universal_silabs_flasher --help > /dev/null \
    && groupadd --gid 10001 livolod \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /var/lib/livolod livolod \
    && mkdir -p /etc/livolod /var/lib/livolod \
    && chown 10001:10001 /var/lib/livolod \
    && chmod 0700 /var/lib/livolod
USER 10001:10001
WORKDIR /var/lib/livolod
RUN --mount=type=bind,source=host/docker/smoke.py,target=/tmp/livolod-smoke.py \
    python /tmp/livolod-smoke.py
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/local/bin/livolod"]
CMD ["--config", "/etc/livolod/config.json"]
