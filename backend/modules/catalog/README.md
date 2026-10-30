# catalog module

Product/item identity, barcode lookup + aliasing, and storage locations.

## Layout
- `api/` — FastAPI routers (`items`, `locations`, `barcodes`); registered via `manifest.py`.
- `services/` — business logic (`items`, `barcodes`, `barcode_providers`).
- `schemas/` — pydantic request/response models.

## Boundaries
- Owns: items, categories, barcode aliases, inventory locations.
- ORM models currently live in the shared `app.models` registry during the
  module migration; they will move under `models/` here once
  the cross-domain model import lines are untangled.
- Other modules consume catalog only through its services/schemas, never by
  importing internals directly.
