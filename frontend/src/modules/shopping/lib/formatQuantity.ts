/** Shopping quantities are counted in whole packages ("package" unit). Show
 * them in plain words: "1 package", "2 packages"; other units pass through. */
export function formatQuantity(quantity: number | string, unit: string | null | undefined): string {
  const qty = String(Number(quantity));
  const u = (unit ?? "").trim();
  if (u.toLowerCase() === "package") return `${qty} ${Number(quantity) === 1 ? "package" : "packages"}`;
  return u ? `${qty} ${u}` : qty;
}
