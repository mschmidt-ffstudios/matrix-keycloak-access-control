# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
FROM golang@sha256:d200d2dd76b03a99b52651fd1325aff7dcd35422c35c01bac56571b0507f3deb AS gosu-builder
ENV GOTOOLCHAIN=local GOMAXPROCS=1 CGO_ENABLED=0
RUN apk add --no-cache git ca-certificates \
 && git clone --depth=1 --branch=1.20 https://github.com/tianon/gosu.git /src \
 && test "$(git -C /src rev-parse HEAD)" = 29b939aa5bd4dc588af765b8248f6e39150d2565
WORKDIR /src
COPY deployment/security/gosu.go.mod /src/go.mod
COPY deployment/security/gosu.go.sum /src/go.sum
RUN go mod download && go mod verify \
 && go build -mod=readonly -p=1 -trimpath -buildvcs=true -o /gosu . \
 && go version -m /gosu \
 && /gosu nobody id

FROM postgres@sha256:266d3c257594e195b309ae9d1805e54c788f1d7b375f56bf6c367aac6c917e7b AS runtime
USER root
RUN apk upgrade --no-cache
COPY --from=gosu-builder /gosu /usr/local/bin/gosu
COPY --from=gosu-builder /src/LICENSE /usr/share/doc/gosu/LICENSE
RUN chmod 0755 /usr/local/bin/gosu
