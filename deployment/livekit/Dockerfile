# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
# Same LiveKit release; narrowly update vulnerable Go x/net and x/crypto modules.
FROM golang@sha256:d200d2dd76b03a99b52651fd1325aff7dcd35422c35c01bac56571b0507f3deb AS source
ENV GOTOOLCHAIN=local GOMAXPROCS=2 GOMEMLIMIT=1536MiB CGO_ENABLED=0
ADD --checksum=sha256:c8d9b7ba9d66c8b5f314a438c0c2493815e7e70085e37706b888cab3c4ab8f8d https://codeload.github.com/livekit/livekit/tar.gz/refs/tags/v1.13.11 /livekit.tar.gz
WORKDIR /src
RUN tar -xzf /livekit.tar.gz --strip-components=1 -C /src
COPY deployment/livekit/go.mod deployment/livekit/go.sum /src/
RUN go mod download && go mod verify

FROM scratch AS module-lock
COPY --from=source /src/go.mod /go.mod
COPY --from=source /src/go.sum /go.sum

FROM source AS builder
RUN go build -mod=readonly -p=2 -trimpath -buildvcs=false -o /livekit-server ./cmd/server \
 && go version -m /livekit-server > /livekit-build-info.txt

FROM alpine:3.24.3@sha256:c6b073f8e9e58d881f6ffba6ebf3b9ebd60b9e7d6e8337018c0e68bb5c356873 AS runtime
RUN apk upgrade --no-cache
COPY --from=builder /livekit-server /livekit-server
COPY --from=builder /livekit-build-info.txt /usr/share/doc/livekit/build-info.txt
COPY --from=builder /src/go.mod /src/go.sum /src/LICENSE /src/NOTICE /usr/share/doc/livekit/
USER 65532:65532
ENTRYPOINT ["/livekit-server"]
