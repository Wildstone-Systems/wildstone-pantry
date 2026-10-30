# wildstone — Wildstone Home Lab integration

Integration contract `pantry-1` for the Wildstone Home Lab.

## Exposes
- `GET  /api/integrations/wildstone/v1/capabilities` — contract `pantry-1`
- `GET  /api/integrations/wildstone/v1/query/{capability}` — read-only agent key is enough
- `POST /api/integrations/wildstone/v1/act/{capability}` — needs the `write` scope
- Admin: `/api/integrations/wildstone/connection` (GET/PUT/DELETE, `POST …/test`)
  and `/api/integrations/wildstone/members`

## Emits (to Keystone's household event intake)
`pantry.meal_logged`, `pantry.purchase_recorded`, `pantry.recipe_cooked`,
`pantry.stock_low`, `pantry.item_expiring`.

## Boundaries
- Owns only its four `wildstone_*` tables.
- Reads other modules only through their services (`inventory.services.feed`,
  `planning.services.*`, `shopping.services.shopping_list`).
- `domain/envelopes.py` is pure — no DB, no HTTP.
