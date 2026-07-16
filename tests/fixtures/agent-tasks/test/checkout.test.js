import assert from "node:assert/strict";
import test from "node:test";
import { checkoutTotal } from "../src/checkout.js";

test("totals line items and shipping", () => {
  assert.equal(checkoutTotal([{ price: 10, quantity: 2 }]), 25);
});

test("supports custom shipping", () => {
  assert.equal(checkoutTotal([{ price: 10, quantity: 1 }], 2), 12);
});
