# Keep the official Nginx binary and Alpine v3.24.3 branch; refresh Alpine packages.
FROM nginx:1.30.6-alpine@sha256:0e52bd054126cdc040b1276c0af347aeb02a41b9cfe305d6094767ac30e97949

RUN set -eux; \
    apk upgrade --no-cache; \
    case "$(cat /etc/alpine-release)" in 3.24.*) ;; *) echo "Unexpected Alpine branch" >&2; exit 1 ;; esac; \
    assert_minimum() { \
      package="$1"; minimum="$2"; \
      installed="$(apk list -I | sed -n "s/^${package}-\\([^ ]*\\).*/\\1/p")"; \
      if [ -z "$installed" ]; then \
        echo "Required package is missing: $package" >&2; exit 1; \
      fi; \
      comparison="$(apk version -t "$installed" "$minimum")"; \
      if [ "$comparison" = "<" ]; then \
        echo "Package $package is too old: $installed (< $minimum)" >&2; exit 1; \
      fi; \
      printf '%s\n' "$package-$installed"; \
    }; \
    assert_minimum brotli-libs 1.2.1-r0; \
    assert_minimum libcurl 8.23.0-r0; \
    assert_minimum libexpat 2.10.0-r0
