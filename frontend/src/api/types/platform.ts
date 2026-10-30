/** Platform/cross-cutting response types (health, entitlement). */

export type HealthResponse = {
  status: string;
  database: string;
};

/** An admin login account (gates the protected admin surface). */
export type Account = {
  id: string;
  email: string;
  role: string;
  is_active: boolean;
  created_at: string;
};

export type AuthTokenResponse = {
  token: string;
  expires_at: string;
  user_id: string;
  role: string;
};

/** A shopping (buy) list entry — auto (from restock threshold) or manual. ADR 0005. */
export type ShoppingListEntry = {
  id: string;
  item_id: string | null;
  name: string;
  quantity: string | number;
  unit: string;
  source: "auto" | "manual";
  status: "pending" | "purchased";
  note: string | null;
  /** Snapshot of the chosen vendor/product offer, if resolved (ADR 0013). */
  chosen_offer: OfferSnapshot | null;
  on_hand: string | number | null;
  created_at: string;
  updated_at: string;
};

/** A purchasable offer from an integration search (ADR 0013). */
export type Offer = {
  provider: string;
  name: string;
  vendor: string | null;
  vendor_sku: string | null;
  upc: string | null;
  brand: string | null;
  size: string | null;
  price: number | null;
  image_url: string | null;
};

export type OfferSnapshot = Offer & { chosen_at?: string };

/** A household person (attribution target; no login). ADR 0004. */
export type HouseholdProfile = {
  id: string;
  display_name: string;
  account_id: string | null;
  daily_calorie_target: string | number | null;
  has_pin: boolean;
  is_active: boolean;
  created_at: string;
};
