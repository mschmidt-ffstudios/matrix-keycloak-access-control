# Matrix Authentication Service security image

The Linux `amd64` image keeps the pinned MAS 1.28.0 distribution and replaces
its `zlib1g` files with Debian's official `1:1.3.dfsg+really1.3.2-4` package.
Debian lists this version as fixed for both reported zlib issues.
[CVE-2026-76844](https://security-tracker.debian.org/tracker/CVE-2026-76844) ·
[CVE-2026-85091](https://security-tracker.debian.org/tracker/CVE-2026-85091) ·
[Debian package checksum](https://packages.debian.org/sid/amd64/zlib1g/download)

The recipe checks the download SHA-256, package identity, and compatibility
with the libc6 version in the pinned Debian 13 runtime. It removes the old
library, installs the fixed SONAME link, and verifies the files against the
package's own checksum list. The image keeps the package's actual control
record, checksums, and notices under their usual paths. The package extractor
and filesystem tool are build stages. The final image keeps MAS's original
entrypoint and runtime user. Compose uses this same runtime for the
Provisioner and uses the MAS image for configuration generation.

Some scanner feeds track the Debian 13 repository's version separately from
the fixed package imported here. Keep the raw scan and check the installed
package version and file evidence when reviewing either zlib advisory.
