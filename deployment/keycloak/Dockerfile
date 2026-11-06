# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e

FROM quay.io/keycloak/keycloak:26.8.1@sha256:3f3c5f8e03c614774b4bd9173b88391fefa4153bfc010d8012d59ad825377332 AS vendor

FROM registry.access.redhat.com/ubi9/ubi:9.8@sha256:8f989cc9270da6abec0144c22846ed1e6e92e462dc7768ce0981c4ac1d574733 AS sqlite-build
ARG TARGETARCH
USER 0
RUN test "$TARGETARCH" = amd64 && dnf -y install gcc binutils rpm-build make openssl python3 && dnf clean all
WORKDIR /work
COPY deployment/keycloak/sqlite/sqlite-libs.spec /work/rpmbuild/SPECS/sqlite-libs.spec
COPY deployment/keycloak/sqlite/test_sqlite_candidate.py deployment/keycloak/sqlite/nss-probe.c /checks/
COPY --from=vendor /usr/lib64/libsqlite3.so.0.8.6 /baseline/libsqlite3.so.0
RUN mkdir -p /out /work/rpmbuild/SOURCES \
 && curl --fail --location --proto '=https' --tlsv1.2 \
      https://www.sqlite.org/2026/sqlite-autoconf-3540100.tar.gz \
      -o /work/rpmbuild/SOURCES/sqlite-autoconf-3540100.tar.gz \
 && test "$(openssl dgst -sha3-256 /work/rpmbuild/SOURCES/sqlite-autoconf-3540100.tar.gz | awk '{print $NF}')" = b6ced585c750916b1052667239a12c25a7ff34e7f06e1e6e01ecb0427657d9c4 \
 && rpmbuild -bb --define '_topdir /work/rpmbuild' /work/rpmbuild/SPECS/sqlite-libs.spec \
 && cp /work/rpmbuild/RPMS/x86_64/sqlite-libs-3.54.1-1.demo1.el9.x86_64.rpm /out/ \
 && gcc -O2 -fstack-protector-strong -Wl,-z,relro,-z,now /checks/nss-probe.c -ldl -o /out/sqlite-nss-probe

# Install the library and its RPM metadata in the vendor root together.
FROM sqlite-build AS sqlite-rootfs
COPY --from=vendor / /rootfs/
# RHSA-2026:88459 supplies the fixed system CA bundle used by RHEL OpenJDK.
RUN test "$(rpm --root /rootfs -q --queryformat '%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' ca-certificates)" \
      = 0:2026.2.92_v9.0.319-90.3.el9_8.noarch \
 && rpm --root /rootfs -Uvh --noscripts --notriggers /out/sqlite-libs-3.54.1-1.demo1.el9.x86_64.rpm \
 && rpm --root /rootfs -V sqlite-libs \
 && python3 /checks/test_sqlite_candidate.py --baseline /baseline/libsqlite3.so.0 \
      --candidate /rootfs/usr/lib64/libsqlite3.so.0.8.6 --rpm-root /rootfs --output /out/sqlite-tests-installed.json \
 && test ! -e /rootfs/usr/bin/sqlite3

FROM vendor AS runtime
COPY --from=sqlite-rootfs /rootfs/ /

# Keep the distribution path referenced by Quarkus. The embedded Maven metadata
# and the checked download identify the replacement as Guava 33.7.2-jre.
ADD --checksum=sha256:b530942257fb935f8b2cfaa5f8eb5bd59c555fd8e8d01b8ce98912e077ea606c --chown=1000:0 --chmod=0644 https://repo1.maven.org/maven2/com/google/guava/guava/33.7.2-jre/guava-33.7.2-jre.jar /opt/keycloak/lib/lib/main/com.google.guava.guava-33.6.0-jre.jar
ADD --checksum=sha256:cbfc3906b19b8f55dd7cfd6dfe0aa4532e834250d7f080bd8d211a3e246b59cb --chown=1000:0 --chmod=0644 https://repo1.maven.org/maven2/com/google/guava/failureaccess/1.0.3/failureaccess-1.0.3.jar /opt/keycloak/providers/failureaccess-1.0.3.jar

ENV KC_DB=postgres \
    KC_HTTP_RELATIVE_PATH=/auth \
    KC_HEALTH_ENABLED=true \
    KC_METRICS_ENABLED=false
USER 1000
RUN --network=none \
    --mount=type=bind,source=deployment/keycloak/trust/VerifyDefaultTrust.java,target=/tmp/VerifyDefaultTrust.java \
    java -Xms16m -Xmx128m /tmp/VerifyDefaultTrust.java
RUN --mount=from=sqlite-build,source=/out/sqlite-nss-probe,target=/tmp/sqlite-nss-probe \
    /tmp/sqlite-nss-probe && rm -rf /tmp/keycloak-sqlite-nss \
 && JAVA_OPTS_KC_HEAP="-Xms64m -Xmx384m" \
    JAVA_OPTS_APPEND="-XX:MaxMetaspaceSize=192m -XX:MaxDirectMemorySize=64m -XX:ActiveProcessorCount=1" \
    /opt/keycloak/bin/kc.sh build --db=postgres --health-enabled=true
