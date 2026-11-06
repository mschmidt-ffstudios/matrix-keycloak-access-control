const MATRIX_ROOM_ID = /^![A-Za-z0-9._=-]+(?::[A-Za-z0-9.:-]+)?$/;

export function isMatrixRoomId(value) {
  return typeof value === "string" && MATRIX_ROOM_ID.test(value);
}
