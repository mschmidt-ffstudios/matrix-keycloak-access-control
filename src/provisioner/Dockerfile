# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
# The Compose build supplies the patched MAS runtime as a named context.
# Copy the pinned official Node binary; no package manager or unrelated base tools.
FROM node@sha256:54f84b4cf8432b21aaffd5d3ce8d41e12cea0d10d33c09d2f3c9879d49d6b446 AS node-runtime
RUN mkdir -p /runtime/app /runtime/data /runtime/run/mas-bootstrap /runtime/run/orgbot-session \
 && chown -R 1000:1000 /runtime/data /runtime/run \
 && chmod 0700 /runtime/run/mas-bootstrap /runtime/run/orgbot-session
FROM mas_runtime AS runtime
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/LICENSE /usr/share/licenses/node/LICENSE
COPY --from=node-runtime /runtime/ /
WORKDIR /app
COPY src/provisioner/index.mjs ./index.mjs
COPY src/provisioner/admin-session.mjs ./admin-session.mjs
COPY src/provisioner/matrix-access.mjs ./matrix-access.mjs
COPY src/provisioner/service-session.mjs ./service-session.mjs
USER 1000:1000
ENV NODE_OPTIONS=--no-experimental-sqlite
ENTRYPOINT []
CMD ["node", "index.mjs"]
