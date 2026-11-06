Name: sqlite
Version: 3.54.1
Release: 1.demo1.el9
Summary: SQLite shared library update for the Matrix Keycloak demo
License: blessing
%global debug_package %{nil}
URL: https://www.sqlite.org/
Source0: https://www.sqlite.org/2026/sqlite-autoconf-3540100.tar.gz
BuildRequires: gcc, binutils, python3

%description
Source package for the SQLite shared-library security update.

%package -n sqlite-libs
Summary: SQLite shared library update for the Matrix Keycloak demo

%description -n sqlite-libs
Custom shared library built on UBI 9 that retains the existing
Keycloak SQLite SONAME and feature set. No command-line shell is installed.
This is not an official Red Hat package or a vendor-supported Keycloak update.

%prep
%setup -q -n sqlite-autoconf-3540100
test "$(openssl dgst -sha3-256 sqlite3.c | awk '{print $NF}')" = e2b869d3003b33c942b7dd7063604fbc53553e1b66609570df39b9b7b558ee81

%build
gcc -shared -fPIC -O2 -g0 -D_FORTIFY_SOURCE=2 -fstack-protector-strong \
 -Wl,-z,relro,-z,now -Wl,-soname,libsqlite3.so.0 \
 -DSQLITE_DISABLE_DIRSYNC=1 -DSQLITE_ENABLE_COLUMN_METADATA=1 \
 -DSQLITE_ENABLE_DBSTAT_VTAB=1 -DSQLITE_ENABLE_FTS3=1 \
 -DSQLITE_ENABLE_FTS3_PARENTHESIS=1 -DSQLITE_ENABLE_FTS4=1 \
 -DSQLITE_ENABLE_FTS5=1 -DSQLITE_ENABLE_JSON1=1 \
 -DSQLITE_ENABLE_PREUPDATE_HOOK=1 -DSQLITE_ENABLE_RTREE=1 \
 -DSQLITE_ENABLE_SESSION=1 -DSQLITE_ENABLE_UNLOCK_NOTIFY=1 \
 -DHAVE_ISNAN=1 -DSQLITE_SECURE_DELETE=1 -DSQLITE_TEMP_STORE=1 \
 -DSQLITE_THREADSAFE=1 sqlite3.c -lpthread -ldl -lm -o libsqlite3.so.0.8.6

%install
mkdir -p %{buildroot}%{_libdir}
install -m 0755 libsqlite3.so.0.8.6 %{buildroot}%{_libdir}/libsqlite3.so.0.8.6
ln -s libsqlite3.so.0.8.6 %{buildroot}%{_libdir}/libsqlite3.so.0

%check
python3 /checks/test_sqlite_candidate.py --baseline /baseline/libsqlite3.so.0 \
 --candidate "$PWD/libsqlite3.so.0.8.6" --output /out/sqlite-tests.json

%files -n sqlite-libs
%{_libdir}/libsqlite3.so.0
%{_libdir}/libsqlite3.so.0.8.6
