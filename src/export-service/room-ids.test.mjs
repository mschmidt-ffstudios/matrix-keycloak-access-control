import test from "node:test";
import assert from "node:assert/strict";
import { isMatrixRoomId } from "./room-ids.mjs";

test("accepts server-qualified Matrix room IDs", () => {
  assert.equal(isMatrixRoomId("!opaque-id:matrix.example.org"), true);
});

test("accepts Synapse room IDs returned without a server suffix", () => {
  assert.equal(isMatrixRoomId("!opaque-id"), true);
});

test("rejects malformed room IDs and non-string values", () => {
  for (const value of ["opaque-id", "!", "!id:", "!has space", null, 12]) {
    assert.equal(isMatrixRoomId(value), false, String(value));
  }
});
