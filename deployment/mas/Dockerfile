# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
FROM ghcr.io/element-hq/matrix-authentication-service:1.28.0@sha256:494c67f08ee8344368f4ed336611dea02b26c4e058bf37a212d090d50b612659 AS upstream

FROM debian:trixie-slim@sha256:7378260c0a098d27ac206225db5e7d92b8c7c84ea7afccc86eeb513756bbd392 AS package
ARG TARGETARCH
ADD --checksum=sha256:8506ce550a81ac14b047608e0df121037d9cbb10c3cc28446f68cf8c1d9d31ce https://deb.debian.org/debian/pool/main/z/zlib/zlib1g_1.3.dfsg%2Breally1.3.2-4_amd64.deb /tmp/zlib1g.deb
COPY --from=upstream /var/lib/dpkg/status.d/libc6 /tmp/upstream-libc6
RUN test "$TARGETARCH" = amd64 \
 && test "$(dpkg-deb -f /tmp/zlib1g.deb Package)" = zlib1g \
 && test "$(dpkg-deb -f /tmp/zlib1g.deb Architecture)" = amd64 \
 && test "$(dpkg-deb -f /tmp/zlib1g.deb Version)" = '1:1.3.dfsg+really1.3.2-4' \
 && test "$(dpkg-deb -f /tmp/zlib1g.deb Depends)" = 'libc6 (>= 2.14)' \
 && dpkg --compare-versions "$(sed -n 's/^Version: //p' /tmp/upstream-libc6)" ge 2.14 \
 && dpkg-deb -x /tmp/zlib1g.deb /package \
 && dpkg-deb -e /tmp/zlib1g.deb /metadata

FROM busybox:1.37.0-uclibc@sha256:9aaccd1b59f428e39ab1cafebbb507eb5b0deb8cb3edbe7b77a323a83e7eda1c AS filesystem-tool

FROM upstream AS runtime
USER 0
# Remove the previous library and SONAME link before installing the fixed package payload.
RUN --mount=from=filesystem-tool,source=/bin/busybox,target=/tmp/busybox ["/tmp/busybox", "sh", "-ec", "lib=/usr/lib/x86_64-linux-gnu; test \"$(/tmp/busybox readlink \"$lib/libz.so.1\")\" = libz.so.1.3.1; /tmp/busybox rm \"$lib/libz.so.1\" \"$lib/libz.so.1.3.1\""]
COPY --from=package /package/usr/lib/x86_64-linux-gnu/ /usr/lib/x86_64-linux-gnu/
COPY --from=package /package/usr/share/doc/zlib1g/ /usr/share/doc/zlib1g/
COPY --from=package /metadata/control /var/lib/dpkg/status.d/zlib1g
COPY --from=package /metadata/md5sums /var/lib/dpkg/status.d/zlib1g.md5sums
WORKDIR /
RUN --mount=from=filesystem-tool,source=/bin/busybox,target=/tmp/busybox ["/tmp/busybox", "sh", "-ec", "test \"$(/tmp/busybox readlink /usr/lib/x86_64-linux-gnu/libz.so.1)\" = libz.so.1.3.2; test ! -e /usr/lib/x86_64-linux-gnu/libz.so.1.3.1; /tmp/busybox md5sum -c /var/lib/dpkg/status.d/zlib1g.md5sums"]
USER 65532
