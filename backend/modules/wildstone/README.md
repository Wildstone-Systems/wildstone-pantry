# wildstone — Wildstone Home Lab integration

Integration contract `pantry-1` for the Wildstone Home Lab.

## Exposes
- `GET  /api/integrations/wildstone/v1/capabilities` — contract `pantry-1`
- `GET  /api/integrations/wildstone/v1/query/{capability}` — read-only agent key is enough
- `POST /api/integrations/wildstone/v1/act/{capability}` — needs the `write` scope
- Admin: `/api/integrations/wildstone/connection` (GET/PUT/DELETE, `POST …/test`)
  and `/api/integrations/wildstone/members`

## Meal plan (#33)
- query `pantry.meal_plan` — `{start?, days?}` (default: this week from Sunday)
  or `{date}` for one day. Entries carry `for` (the linked member, or null for
  the whole household) and `source` (`manual`/`agent`).
- act `pantry.meal_plan.add` — one meal: `{date, meal_type, recipe | recipe_id
  | item_id | title, servings?, member? | profile_id?, note?}`. An uncertain
  recipe name answers with candidates instead of guessing.
- act `pantry.meal_plan.week` — `{meals: [...]}`: all of them or none.
- act `pantry.meal_plan.remove` — `{entry_id}`, repeat-safe.
- All three acts are proposed `domestic`: a plan reserves no stock and buys
  nothing, and anyone can change it afterwards.

## Emits (to Keystone's household event intake)
`pantry.meal_logged`, `pantry.purchase_recorded`, `pantry.recipe_cooked`,
`pantry.stock_low`, `pantry.item_expiring`, `pantry.item_spoiled`,
`pantry.shopping_list_changed`, `pantry.timer_finished`,
`pantry.meal_plan_changed` (`{upcoming_meals}` once a burst of edits settles).

## Boundaries
- Owns only its four `wildstone_*` tables.
- Reads other modules only through their services (`inventory.services.feed`,
  `planning.services.*`, `shopping.services.shopping_list`,
  `mealplan.services`).
- `domain/envelopes.py` is pure — no DB, no HTTP.
