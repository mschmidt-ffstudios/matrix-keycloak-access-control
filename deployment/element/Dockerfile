# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
FROM ghcr.io/pnpm/pnpm:12.12.1@sha256:150c9372f3a6d02039f11dc7d1f6070896567038968c97bbb224e3b03ee3cbcc AS pnpm
FROM node:24-bullseye@sha256:25f3016fcdae6b5d65bd9bcb4064b7e4198ec8d49493fa40d73f9b463d04fb15 AS builder
COPY --from=pnpm /opt/pnpm /opt/pnpm
RUN ln -s /opt/pnpm/pnpm /usr/local/bin/pnpm \
 && apt-get update && apt-get install --yes --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /build
ADD --checksum=sha256:f6d45cdcbe4af1d51ea5c6019d29d2c7371ddc06bc9b9095c623ca2fe4a45190 https://codeload.github.com/element-hq/element-web/tar.gz/refs/tags/v1.12.33 /build/element.tar.gz
RUN tar -xzf element.tar.gz && rm element.tar.gz
COPY deployment/element/apply-contact-policy.py /project/deployment/element/apply-contact-policy.py
COPY src/element-policy/ /project/src/element-policy/
RUN python3 /project/deployment/element/apply-contact-policy.py /build/element-web-1.12.33
WORKDIR /build/element-web-1.12.33
RUN pnpm install --frozen-lockfile
RUN VERSION=v1.12.33-ffs-policy1 NODE_OPTIONS=--max-old-space-size=6144 NX_DAEMON=false pnpm --dir apps/web build
RUN tar --exclude=node_modules --exclude=webapp --exclude=.git -czf /build/foldfence-source.tar.gz .
FROM vectorim/element-web:v1.12.33@sha256:b0aa4b5a4fc1afaaff62e2dbd0c92d57e2b20724f3ccb2fef53aae949088c3d2
USER root
RUN apk upgrade --no-cache
USER nginx
COPY --from=builder /build/element-web-1.12.33/apps/web/webapp/ /app/
COPY --from=builder /build/foldfence-source.tar.gz /app/foldfence-source.tar.gz
