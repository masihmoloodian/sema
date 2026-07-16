export function checkoutTotal(items, shipping = 5) {
  const subtotal = items.reduce(
    (sum, item) => sum + item.price * item.quantity,
    0,
  );
  return subtotal + shipping;
}
