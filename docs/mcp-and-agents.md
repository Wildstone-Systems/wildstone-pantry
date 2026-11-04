# AI assistants (MCP)

PantryOS ships an [MCP](https://modelcontextprotocol.io) server so an AI
assistant (Claude Desktop, Home Assistant, other MCP clients) can work with
your kitchen:

- Check what's in stock and what's expiring
- Add inventory and manage the shopping list
- Add a product from a link
- Create, import and cook recipes step by step
- Plan meals and push missing ingredients to the shopping list

## How it works

- The MCP server is a small separate process that calls the PantryOS API.
- It authenticates with an **agent key** — create one on the **Agent Keys**
  page in the app.
- Read-only keys can't change anything.

## Setup

Full install, configuration and tool list:
[`backend/mcp_server/README.md`](../backend/mcp_server/README.md).
