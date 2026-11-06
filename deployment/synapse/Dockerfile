# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
# Keep the Synapse release and upstream launcher/templates. Replace the broad
# Debian runtime with a supported musl wheel runtime for the configured features.
FROM python:3.13.18-alpine3.24@sha256:7984cf56ceaddebe4fefeb75d64000b9f45d8b77b11a09b24524da6cdc3af0c8 AS wheels
ADD --checksum=sha256:6ac48de0a40e60c9d57d808ea4416637df33f9710db8c44b82fd6ec68dc903f4 https://codeload.github.com/element-hq/synapse/tar.gz/refs/tags/v1.164.2 /synapse-source.tar.gz
RUN mkdir /synapse-source && tar -xzf /synapse-source.tar.gz --strip-components=1 -C /synapse-source
RUN apk add --no-cache build-base postgresql-dev
COPY deployment/security/build-requirements.lock /build-requirements.lock
COPY deployment/synapse/requirements.lock /requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /build-requirements.lock \
 && python -m pip wheel --no-cache-dir --require-hashes --no-build-isolation --only-binary=:all: --no-binary=psycopg2,zope-interface --wheel-dir=/wheels -r /requirements.lock \
 && sha256sum /wheels/*.whl > /wheels/SHA256SUMS

FROM golang@sha256:d200d2dd76b03a99b52651fd1325aff7dcd35422c35c01bac56571b0507f3deb AS gosu-builder
ENV GOTOOLCHAIN=local GOMAXPROCS=1 CGO_ENABLED=0
RUN apk add --no-cache git ca-certificates \
 && git clone --depth=1 --branch=1.20 https://github.com/tianon/gosu.git /src \
 && test "$(git -C /src rev-parse HEAD)" = 29b939aa5bd4dc588af765b8248f6e39150d2565
WORKDIR /src
COPY deployment/security/gosu.go.mod /src/go.mod
COPY deployment/security/gosu.go.sum /src/go.sum
RUN go mod download && go mod verify \
 && go build -mod=readonly -p=1 -trimpath -buildvcs=true -o /gosu .

FROM python:3.13.18-alpine3.24@sha256:7984cf56ceaddebe4fefeb75d64000b9f45d8b77b11a09b24524da6cdc3af0c8 AS runtime
RUN apk upgrade --no-cache \
 && apk add --no-cache libpq jemalloc \
 && addgroup -g 1000 synapse \
 && adduser -D -u 991 -G synapse synapse \
 && mkdir -p /data && chown 991:1000 /data
ADD --checksum=sha256:5dfd76936e795efd988d6bad4949b8d20c410d6dc9dd256da884111f668f3915 https://dl-cdn.alpinelinux.org/alpine/edge/main/x86_64/sqlite-libs-3.54.1-r0.apk /tmp/sqlite-libs-3.54.1-r0.apk
RUN apk add --no-cache --no-network /tmp/sqlite-libs-3.54.1-r0.apk && rm /tmp/sqlite-libs-3.54.1-r0.apk
COPY --from=wheels /wheels /wheels
RUN sha256sum -c /wheels/SHA256SUMS \
 && python -m pip install --no-cache-dir --no-index --no-deps /wheels/*.whl \
 && python -m pip uninstall --yes pip \
 && rm -rf /wheels
COPY --from=wheels /synapse-source/docker/start.py /start.py
COPY --from=wheels /synapse-source/docker/conf /conf
COPY --from=wheels /synapse-source/LICENSE-AGPL-3.0 /usr/share/doc/matrix-synapse/LICENSE-AGPL-3.0
COPY --from=wheels /synapse-source/LICENSE-COMMERCIAL /usr/share/doc/matrix-synapse/LICENSE-COMMERCIAL
COPY --from=gosu-builder /gosu /usr/sbin/gosu
COPY --from=gosu-builder /src/LICENSE /usr/share/doc/gosu/LICENSE
ENV PYTHONUNBUFFERED=1 SYNAPSE_VERSION_STRING=1.164.2
LABEL org.opencontainers.image.source="https://github.com/element-hq/synapse" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later OR LicenseRef-Element-Commercial"
EXPOSE 8008 8448 19090
USER 991:1000
ENTRYPOINT ["/start.py"]
