#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

int main(void) {
  void *sqlite = dlopen("libsqlite3.so.0", RTLD_NOW | RTLD_LOCAL);
  if (!sqlite) { fprintf(stderr, "SQLite dlopen failed: %s\n", dlerror()); return 1; }
  const char *(*version)(void) = dlsym(sqlite, "sqlite3_libversion");
  if (!version || strcmp(version(), "3.54.1")) { fputs("Wrong SQLite version\n", stderr); return 2; }
  void *nss = dlopen("libnss3.so", RTLD_NOW | RTLD_LOCAL);
  if (!nss) { fprintf(stderr, "NSS dlopen failed: %s\n", dlerror()); return 3; }
  int (*init)(const char *) = dlsym(nss, "NSS_InitReadWrite");
  int (*shutdown)(void) = dlsym(nss, "NSS_Shutdown");
  if (!init || !shutdown) { fputs("Missing NSS API\n", stderr); return 4; }
  if (mkdir("/tmp/keycloak-sqlite-nss", 0700)) { perror("mkdir"); return 5; }
  if (init("sql:/tmp/keycloak-sqlite-nss")) { fputs("NSS SQLite database initialization failed\n", stderr); return 6; }
  if (shutdown()) { fputs("NSS shutdown failed\n", stderr); return 7; }
  struct stat cert, key;
  if (stat("/tmp/keycloak-sqlite-nss/cert9.db", &cert) || stat("/tmp/keycloak-sqlite-nss/key4.db", &key) || cert.st_size <= 0 || key.st_size <= 0) {
    fputs("NSS database files missing\n", stderr); return 8;
  }
  printf("{\"sqlite_version\":\"%s\",\"nss_readwrite_init\":true,\"nss_shutdown\":true,\"cert9_db\":true,\"key4_db\":true}\n", version());
  return 0;
}
