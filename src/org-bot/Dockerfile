# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
# Same application and hash-locked E2EE SDK; supported minimal musl runtime.
FROM python:3.12.16-alpine3.24@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8 AS wheels
COPY deployment/security/build-requirements.lock /build-requirements.lock
COPY src/org-bot/requirements.lock /requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /build-requirements.lock \
 && python -m pip wheel --no-cache-dir --require-hashes --no-build-isolation --only-binary=:all: --no-binary=atomicwrites --wheel-dir=/wheels -r /requirements.lock \
 && sha256sum /wheels/*.whl > /wheels/SHA256SUMS
FROM python:3.12.16-alpine3.24@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ORG_BOT_STORE_PATH=/var/lib/orgbot
RUN apk upgrade --no-cache
ADD --checksum=sha256:5dfd76936e795efd988d6bad4949b8d20c410d6dc9dd256da884111f668f3915 https://dl-cdn.alpinelinux.org/alpine/edge/main/x86_64/sqlite-libs-3.54.1-r0.apk /tmp/sqlite-libs-3.54.1-r0.apk
RUN apk add --no-cache --no-network /tmp/sqlite-libs-3.54.1-r0.apk && rm /tmp/sqlite-libs-3.54.1-r0.apk
WORKDIR /app
COPY --from=wheels /wheels /wheels
COPY src/org-bot/requirements.lock /app/requirements.lock
RUN sha256sum -c /wheels/SHA256SUMS \
 && python -m pip install --no-cache-dir --disable-pip-version-check --no-index --no-deps /wheels/*.whl \
 && python -m pip uninstall --yes pip \
 && rm -rf /wheels
COPY src/org-bot/orgbot.py /app/orgbot.py
RUN addgroup -g 1000 orgbot \
 && adduser -D -u 1000 -G orgbot orgbot \
 && mkdir -p /var/lib/orgbot \
 && chown -R 1000:1000 /app /var/lib/orgbot
USER 1000:1000
EXPOSE 8090
CMD ["python", "/app/orgbot.py"]
