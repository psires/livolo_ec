# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.13-slim-bookworm
FROM python:3.10-slim-bookworm@sha256:5be5aaecb962a41567bc8041f1e9e3432cb085b037fe057f9a562630b2e4b370 AS nordic-helper
WORKDIR /helper
COPY host/docker/requirements-nordic.txt host/docker/requirements-nordic-build.txt host/docker/build_nordic.py ./
COPY tools/container_sources.py ./container_sources.py
RUN python -m pip install --no-cache-dir --no-deps -r requirements-nordic-build.txt \
    && python -m pip wheel --no-cache-dir --no-deps --no-build-isolation --wheel-dir /wheels -r requirements-nordic.txt \
    && python -m pip install --no-cache-dir --no-index --no-deps --find-links=/wheels -r requirements-nordic.txt \
    && python build_nordic.py \
    && python -I -m nordicsemi dfu usb-serial --help > /dev/null

FROM ${PYTHON_IMAGE} AS builder
ARG RELEASE_VERSION=0.14.0
ARG FIRMWARE_COMPAT_VERSION=0.13
WORKDIR /src
COPY VERSION ./VERSION
COPY host/pyproject.toml ./host/pyproject.toml
COPY host/PACKAGE.md ./host/PACKAGE.md
COPY host/LICENSE ./host/LICENSE
COPY host/docker/requirements-firmware.txt ./host/docker/requirements-firmware.txt
COPY host/livolod ./host/livolod
RUN python -c 'import os, pathlib, tomllib; from host.livolod import __version__, FIRMWARE_COMPAT_VERSION; assert __version__ == os.environ["RELEASE_VERSION"] == tomllib.loads(pathlib.Path("host/pyproject.toml").read_text())["project"]["version"]; assert FIRMWARE_COMPAT_VERSION == os.environ["FIRMWARE_COMPAT_VERSION"] == pathlib.Path("VERSION").read_text().strip()' \
    && python -m pip wheel --no-cache-dir --no-deps --wheel-dir /wheels ./host \
    && python -m pip download --no-cache-dir --only-binary=:all: \
       --dest /wheels -r host/docker/requirements-firmware.txt paho-mqtt==2.1.0 \
    && python -m pip download --no-cache-dir --no-deps --no-binary=:all: \
       --dest /sources universal-silabs-flasher==0.0.31 \
    && python -c 'import hashlib, pathlib; p = pathlib.Path("/sources/universal_silabs_flasher-0.0.31.tar.gz"); assert hashlib.sha256(p.read_bytes()).hexdigest() == "7f7a4a744273277430cbff19a1c7522372b434ab147adcbc821395566c3a6a14"'

FROM ${PYTHON_IMAGE} AS runtime
ARG RELEASE_VERSION=0.14.0
ARG FIRMWARE_COMPAT_VERSION=0.13
LABEL org.opencontainers.image.title="Livolo EC daemon" \
      org.opencontainers.image.description="Livolo EC UART to MQTT daemon" \
      org.opencontainers.image.licenses="BSD-2-Clause" \
      org.opencontainers.image.version="${RELEASE_VERSION}" \
      net.sfires.livolo.daemon-version="${RELEASE_VERSION}" \
      net.sfires.livolo.firmware-compat-version="${FIRMWARE_COMPAT_VERSION}"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY --from=builder /sources /usr/share/livolo-ec/sources
COPY --from=nordic-helper /sources/nordic /usr/share/livolo-ec/sources/nordic
COPY --from=nordic-helper /usr/local /opt/nrfutil
RUN echo /opt/nrfutil/lib > /etc/ld.so.conf.d/livolo-nrfutil.conf \
    && ldconfig \
    && /opt/nrfutil/bin/python3.10 -I -m nordicsemi dfu usb-serial --help > /dev/null
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
    LIVOLO_NORDIC_SMOKE=1 python /tmp/livolod-smoke.py
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/local/bin/livolod"]
CMD ["--config", "/etc/livolod/config.json"]
