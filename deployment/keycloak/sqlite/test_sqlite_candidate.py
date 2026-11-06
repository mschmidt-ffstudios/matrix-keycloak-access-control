#!/usr/bin/env python3
"""Exercise the fixed library and compare public C ABI with the vendor build."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import re
import subprocess

# These vendor-exported tracing/assertion globals are absent from the public
# sqlite3.h API. They may disappear only with an exact allowlist AND a separate
# check that no shipped ELF consumer references or names them for dlsym.
UNDOCUMENTED_DEBUG_EXPORTS = {
    "sqlite3_fts3_may_be_corrupt",
    "sqlite3_fts5_may_be_corrupt",
    "sqlite3_unsupported_selecttrace",
}


def exports(path):
    output = subprocess.check_output(["nm", "-D", "--defined-only", str(path)], universal_newlines=True)
    return {line.split()[-1].split("@")[0] for line in output.splitlines()
            if line.split() and line.split()[-1].startswith("sqlite3_")}


def load(path):
    lib = ctypes.CDLL(str(path))
    lib.sqlite3_libversion.restype = ctypes.c_char_p
    lib.sqlite3_sourceid.restype = ctypes.c_char_p
    lib.sqlite3_compileoption_used.argtypes = [ctypes.c_char_p]
    lib.sqlite3_compileoption_used.restype = ctypes.c_int
    lib.sqlite3_open.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    lib.sqlite3_open.restype = ctypes.c_int
    lib.sqlite3_exec.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p)]
    lib.sqlite3_exec.restype = ctypes.c_int
    lib.sqlite3_prepare_v2.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    lib.sqlite3_prepare_v2.restype = ctypes.c_int
    lib.sqlite3_step.argtypes = [ctypes.c_void_p]
    lib.sqlite3_step.restype = ctypes.c_int
    lib.sqlite3_column_int64.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.sqlite3_column_int64.restype = ctypes.c_longlong
    lib.sqlite3_finalize.argtypes = [ctypes.c_void_p]
    lib.sqlite3_close.argtypes = [ctypes.c_void_p]
    return lib


def execute(lib, db, sql):
    error = ctypes.c_char_p()
    rc = lib.sqlite3_exec(db, sql.encode(), None, None, ctypes.byref(error))
    if rc:
        raise AssertionError("SQLite SQL failed: {} {}".format(rc, error.value))


def scalar(lib, db, sql):
    statement = ctypes.c_void_p()
    assert lib.sqlite3_prepare_v2(db, sql.encode(), -1, ctypes.byref(statement), None) == 0
    try:
        assert lib.sqlite3_step(statement) == 100
        return lib.sqlite3_column_int64(statement, 0)
    finally:
        lib.sqlite3_finalize(statement)


def installed_rpm_identity(root):
    query = "%{NAME}\n%{VERSION}\n%{RELEASE}\n%{ARCH}\n%{SOURCERPM}\n"
    values = subprocess.check_output(["rpm", "--root", str(root), "-q", "--queryformat", query, "sqlite-libs"],
                                     universal_newlines=True).splitlines()
    expected = ["sqlite-libs", "3.54.1", "1.demo1.el9", "x86_64", "sqlite-3.54.1-1.demo1.el9.src.rpm"]
    if values != expected:
        raise ValueError("Installed SQLite binary/source RPM identity differs from the reviewed package")
    return dict(zip(["binary_name", "version", "release", "arch", "source_rpm"], values), source_name="sqlite")


def main():
    if not __debug__:
        raise RuntimeError("SQLite compatibility checks require Python assertions")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rpm-root", type=Path, help="Read and verify the installed RPM database after the package transaction")
    args = parser.parse_args()
    baseline_hash = hashlib.sha256(args.baseline.read_bytes()).hexdigest()
    assert baseline_hash == "007322505ff5177c820251b31612a9e4c2e6e4c78a5ad62df81d92ba329d65ff"
    original, candidate = exports(args.baseline), exports(args.candidate)
    removed = original - candidate
    missing = sorted(removed - UNDOCUMENTED_DEBUG_EXPORTS)
    assert not missing, "Unexpected SQLite ABI exports disappeared: {}".format(missing)
    assert removed == UNDOCUMENTED_DEBUG_EXPORTS, "Reviewed SQLite ABI exception set changed"
    elf = subprocess.check_output(["readelf", "-d", str(args.candidate)], universal_newlines=True)
    assert re.search(r"\(SONAME\).*\[libsqlite3\.so\.0\]", elf)
    program_headers = subprocess.check_output(["readelf", "-W", "-l", str(args.candidate)], universal_newlines=True)
    stack = next(line for line in program_headers.splitlines() if "GNU_STACK" in line)
    undefined = subprocess.check_output(["nm", "-D", "--undefined-only", str(args.candidate)], universal_newlines=True)
    versions = subprocess.check_output(["readelf", "--version-info", str(args.candidate)], universal_newlines=True)
    needed_glibc = sorted(set(re.findall(r"GLIBC_([0-9.]+)", versions)), key=lambda value: tuple(map(int, value.split("."))))
    fortify = sorted(set(re.findall(r"(__[a-zA-Z0-9_]+_chk)@", undefined)) - {"__stack_chk"})
    hardening = {"gnu_relro": "GNU_RELRO" in program_headers,
                 "bind_now": "BIND_NOW" in elf or bool(re.search(r"FLAGS_1.*NOW", elf)),
                 "non_executable_stack": "RWE" not in stack,
                 "stack_protector": "__stack_chk_fail" in undefined,
                 "fortify_chk_imports": fortify,
                 "max_glibc_required": needed_glibc[-1]}
    assert all(hardening[field] for field in ("gnu_relro", "bind_now", "non_executable_stack", "stack_protector"))
    assert fortify, "Fortified libc calls disappeared"
    assert tuple(map(int, needed_glibc[-1].split("."))) <= (2, 34)
    lib = load(args.candidate.resolve())
    assert lib.sqlite3_libversion() == b"3.54.1"
    for feature in ("ENABLE_FTS5", "ENABLE_FTS3", "ENABLE_FTS4", "ENABLE_RTREE", "ENABLE_SESSION",
                    "ENABLE_PREUPDATE_HOOK", "ENABLE_UNLOCK_NOTIFY", "ENABLE_COLUMN_METADATA",
                    "ENABLE_DBSTAT_VTAB", "SECURE_DELETE", "THREADSAFE=1", "TEMP_STORE=1"):
        assert lib.sqlite3_compileoption_used(feature.encode()), feature
    results = {}
    for detail in ("full", "columns", "none"):
        db = ctypes.c_void_p()
        assert lib.sqlite3_open(b":memory:", ctypes.byref(db)) == 0
        try:
            # SQLite upstream fts5prefix2.test regression from e1c9934f9f11...
            execute(lib, db, "CREATE VIRTUAL TABLE t1 USING fts5(x, detail={});".format(detail))
            execute(lib, db, "WITH RECURSIVE c(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM c WHERE x<55) INSERT INTO t1(rowid,x) SELECT x,'ab' FROM c;")
            execute(lib, db, "INSERT INTO t1(rowid,x) VALUES(-9223372036854775808,'abc');")
            count = scalar(lib, db, "SELECT count(*) FROM t1('a*')")
            assert count == 56, (detail, count)
            assert scalar(lib, db, "SELECT json_extract('{\"v\":7}', '$.v')") == 7
            results[detail] = count
        finally:
            assert lib.sqlite3_close(db) == 0
    report = {"sqlite_version": lib.sqlite3_libversion().decode(), "source_id": lib.sqlite3_sourceid().decode(),
              "library_sha256": hashlib.sha256(args.candidate.read_bytes()).hexdigest(),
              "baseline_library_sha256": baseline_hash,
              "baseline_public_exports": len(original), "candidate_public_exports": len(candidate),
              "missing_public_exports": missing, "soname": "libsqlite3.so.0", "fts5_negative_rowid_regression": results,
              "removed_undocumented_debug_exports": sorted(removed),
              "hardening": hardening,
              "abi_scope": "All original sqlite3_* dynamic exports retained except the three explicitly listed undocumented debug/tracing globals; require separate whole-image consumer analysis and NSS testing.",
              "regression_source": "https://github.com/sqlite/sqlite/commit/e1c9934f9f11a499029abb020ea715a08970da80",
              "json_function": True, "cli_installed": False}
    if args.rpm_root:
        report["rpm_package"] = installed_rpm_identity(args.rpm_root)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
