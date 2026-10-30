# PantryOS MCP server

Exposes PantryOS as [MCP](https://modelcontextprotocol.io) tools so an agent
(Hermes, Claude Desktop, etc.) can act on your kitchen — add a product from a
link, manage the shopping list, check what's expiring, add inventory.

It's a thin adapter: it calls the PantryOS REST API over HTTP using an **agent
key**. PantryOS stays the source of truth.

## ⚠️ Use a separate virtualenv

The MCP SDK depends on a newer `starlette` than the FastAPI backend pins, so the
two **cannot share an environment**. Install this in its own venv:

```bash
python -m venv .venv-mcp && . .venv-mcp/bin/activate
pip install -r backend/mcp_server/requirements.txt
```

## Configure

Mint an agent key in the app (**Agent Keys** page, or `POST /api/agent-keys`),
then set:

| Variable | Required | Example |
|---|---|---|
| `PANTRYOS_BASE_URL` | yes | `http://localhost:8000` (or your Tailscale/tunnel URL) |
| `PANTRYOS_AGENT_KEY` | yes | `pk_…` |
| `PANTRYOS_LICENSE_KEY` | only if the deployment enforces a license | `…` |

## Run

```bash
PANTRYOS_BASE_URL=http://localhost:8000 \
PANTRYOS_AGENT_KEY=pk_your_key \
python -m mcp_server.server        # stdio transport (default)
```

For a **remote agent** (a Hermes instance elsewhere on your tailnet, or Home
Assistant's MCP client), run it as a long-lived HTTP endpoint instead:

```bash
PANTRYOS_MCP_TRANSPORT=http \
PANTRYOS_MCP_HOST=0.0.0.0 PANTRYOS_MCP_PORT=8765 \
PANTRYOS_BASE_URL=http://localhost:8000 \
PANTRYOS_AGENT_KEY=pk_your_key \
python -m mcp_server.server        # streamable-HTTP at http://host:8765/mcp
```

The HTTP endpoint itself is unauthenticated — anyone who can reach it can use
the tools with the server's agent key. Bind it to a private interface
(Tailscale IP, localhost behind a tunnel), never the open internet.

## Wire into an agent

Point your MCP client at the command. For a stdio client the config looks like:

```json
{
  "mcpServers": {
    "pantryos": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "env": {
        "PANTRYOS_BASE_URL": "http://localhost:8000",
        "PANTRYOS_AGENT_KEY": "pk_your_key"
      }
    }
  }
}
```

## Tools

`household_summary`, `ingest_product_url`, `add_to_shopping_list`,
`list_shopping_list`, `mark_shopping_item`, `add_inventory`, `list_inventory`,
`whats_expiring`, `list_locations`, `list_containers`, `lookup_barcode`,
`list_recipes`, `get_recipe`, `suggest_recipes`, `cook_recipe`,
`consumption_trends`, `restock_suggestions`, `list_purchases`.

Cook-along (ADR 0026 §6) — step by step, nothing leaves the pantry until
`finish_cooking`: `start_cooking`, `current_step`, `next_step`,
`previous_step`, `go_to_step`, `finish_cooking`, `stop_cooking`.

Your example — "here's a link, add it to the shopping list and inventory later":

```
ingest_product_url(url="https://store.example.com/olive-oil")
  → catalog item + shopping-list need (source=agent)
… you buy it → logging the receipt/purchase checks the need off and adds the lot.
```

## Networking

PantryOS is self-hosted. To reach it from wherever the agent runs, prefer a
private tunnel (Tailscale / Cloudflare Tunnel) over exposing the API to the
internet. The agent key + license key + TLS ride on top.

## Notes / limits

- The `ingest_product_url` fetch is SSRF-guarded server-side (non-http(s),
  loopback/private/link-local/metadata targets are refused on every redirect
  hop — ADR 0010).
- Agent-key scopes are enforced: a read-only key gets `403` on any mutating
  tool; mint a "Read + write" key for the full toolset.
