FROM node@sha256:8a32c8cf649a49915a66e81e7a609223c708d0adabf288ee3f4077d765542f85

ENV NODE_ENV=production NODE_OPTIONS=--no-experimental-sqlite
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund \
    && apk upgrade --no-cache \
    && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
       /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack
COPY server.mjs ./
COPY room-ids.mjs ./
COPY room-access.mjs ./
RUN mkdir -p /var/lib/export-audit \
    && chown node:node /var/lib/export-audit \
    && chmod 700 /var/lib/export-audit
USER node
EXPOSE 8090
CMD ["node", "server.mjs"]
