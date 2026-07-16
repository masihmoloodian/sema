import assert from "node:assert/strict";
import test from "node:test";
import { greeting } from "../src/greeting.js";

test("exports the greeting", () => {
  assert.equal(greeting(), "hello");
});
