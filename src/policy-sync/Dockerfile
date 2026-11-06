FROM node@sha256:8a32c8cf649a49915a66e81e7a609223c708d0adabf288ee3f4077d765542f85
RUN apk upgrade --no-cache \
    && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
       /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack
WORKDIR /app
ENV NODE_OPTIONS=--no-experimental-sqlite
COPY src/policy-sync/index.mjs ./index.mjs
COPY src/policy-sync/policy-model.mjs ./policy-model.mjs
COPY src/policy-sync/user-admission.mjs ./user-admission.mjs
COPY src/policy-sync/matrix-access.mjs ./matrix-access.mjs
RUN mkdir -p /data && chown node:node /data
USER node
CMD ["node", "index.mjs"]
