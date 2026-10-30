import {
  Accordion,
  ActionIcon,
  Alert,
  Anchor,
  Button,
  Card,
  Code,
  CopyButton,
  Group,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, apiBaseUrl } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";

type AgentKey = {
  id: string;
  name: string;
  token_prefix: string;
  scopes: string;
  revoked: boolean;
  created_at: string;
  last_used_at: string | null;
};

function CopyIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function Snippet({ label, code }: { label: string; code: string }) {
  return (
    <Stack gap={4}>
      <Group justify="space-between" align="center" gap="xs">
        <Text size="sm" fw={500}>
          {label}
        </Text>
        <CopyButton value={code}>
          {({ copied, copy }) => (
            <Tooltip label={copied ? "Copied" : "Copy"} withArrow>
              <ActionIcon
                variant="subtle"
                color={copied ? "teal" : "gray"}
                onClick={copy}
                aria-label={`Copy ${label}`}
              >
                <CopyIcon />
              </ActionIcon>
            </Tooltip>
          )}
        </CopyButton>
      </Group>
      <Code block style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
        {code}
      </Code>
    </Stack>
  );
}

function ConnectAgentGuide({ newSecret }: { newSecret: string | null }) {
  // The backend's URL, not necessarily the UI origin (VITE_API_BASE_URL).
  const baseUrl = apiBaseUrl();
  // Freshly minted secret goes straight into the runnable snippets (it is
  // already visible on this page right now). The briefing keeps a placeholder:
  // it's meant to be pasted into an agent chat, where the key must not appear.
  const keyValue = newSecret ?? "pk_your_key";

  const briefing = [
    "PantryOS agent access",
    "=====================",
    `- API: REST (FastAPI). OpenAPI docs: ${baseUrl}/docs (spec: ${baseUrl}/openapi.json)`,
    "- Auth: send the agent key as an X-Agent-Key header on every request.",
    "  The key is stored outside this chat — read it from the path/secret I give you.",
    "- Keys are scoped: read-only keys get 403 on any mutation.",
    `- Start here: GET ${baseUrl}/api/agent/summary — one call returning counts of`,
    "  expiring/expired items, low stock, pending shopping entries, and locations.",
    "- Other useful endpoints: /api/shopping-list, /api/agent/ingest-product-url,",
    "  /api/inventory, /api/recipes, /api/insights/* (see /docs for schemas).",
    "- Preferred integration: the PantryOS MCP server (backend/mcp_server/ in the",
    "  repo) exposes typed tools (household_summary, add_to_shopping_list,",
    "  ingest_product_url, whats_expiring, cook_recipe, ...). Run it in its own",
    "  venv with PANTRYOS_BASE_URL + PANTRYOS_AGENT_KEY set; stdio by default, or",
    "  PANTRYOS_MCP_TRANSPORT=http for a remote endpoint (bind it to a private/",
    "  tailnet interface only — the HTTP endpoint has no auth of its own).",
  ].join("\n");

  const curl = `curl -H "X-Agent-Key: ${keyValue}" ${baseUrl}/api/agent/summary`;

  // The interpreter must be the MCP venv's python — a bare "python" resolves
  // from the MCP client's own PATH (desktop/service clients especially) and
  // won't have the mcp deps installed.
  const mcpConfig = JSON.stringify(
    {
      mcpServers: {
        pantryos: {
          command: "/path/to/pantryos/.venv-mcp/bin/python",
          args: ["-m", "mcp_server.server"],
          env: { PANTRYOS_BASE_URL: baseUrl, PANTRYOS_AGENT_KEY: keyValue },
        },
      },
    },
    null,
    2,
  );

  return (
    <Accordion variant="contained">
      <Accordion.Item value="connect">
        <Accordion.Control>How to connect an agent</Accordion.Control>
        <Accordion.Panel>
          <Stack gap="md">
            <Text size="sm" c="dimmed">
              Mint a key above, store it securely on the agent&apos;s machine (e.g. a{" "}
              <Code>chmod 600</Code> file or an encrypted secret store — never in chat logs), then
              use the snippets below.
              {newSecret
                ? " Your newly minted key is already filled into the test command and MCP config."
                : " The snippets use a pk_your_key placeholder — mint a key to fill it in automatically."}{" "}
              Interactive API docs live at{" "}
              <Anchor href={`${baseUrl}/docs`} target="_blank" rel="noreferrer">
                {baseUrl}/docs
              </Anchor>
              . After the agent&apos;s first successful call, the key&apos;s{" "}
              <strong>Last used</strong> column below flips from &quot;never&quot;.
            </Text>

            <Snippet label="Briefing to paste to your agent (key kept out of chat)" code={briefing} />
            <Snippet label="Quick test (terminal)" code={curl} />
            <Snippet label="MCP client config (recommended for agents like Hermes)" code={mcpConfig} />

            <Text size="sm" c="dimmed">
              The MCP server needs its own virtualenv:{" "}
              <Code>python -m venv .venv-mcp &amp;&amp; .venv-mcp/bin/pip install -r
              backend/mcp_server/requirements.txt</Code>, then replace{" "}
              <Code>/path/to/pantryos/.venv-mcp/bin/python</Code> in the config with that venv&apos;s
              real interpreter path. See <Code>backend/mcp_server/README.md</Code> for the remote
              (HTTP) transport.
            </Text>
          </Stack>
        </Accordion.Panel>
      </Accordion.Item>
    </Accordion>
  );
}

export function AgentKeysPage() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState("read,write");
  // The freshly minted key (id + secret) — id lets us stop showing the secret
  // if that key is revoked while it is still on screen.
  const [newKey, setNewKey] = useState<{ id: string; secret: string } | null>(null);
  const [error, setError] = useState("");
  const newSecret = newKey?.secret ?? null;

  const keysQuery = useQuery({
    queryKey: ["agentKeys"],
    queryFn: async () => {
      const res = await api.get<{ keys: AgentKey[] }>("/api/agent-keys");
      return res.data.keys;
    },
  });

  const keys = keysQuery.data ?? [];
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["agentKeys"] });

  const createMutation = useMutation({
    mutationFn: async () => {
      const res = await api.post<{ key: { id: string }; secret: string }>("/api/agent-keys", {
        name: name.trim(),
        scopes,
      });
      return res.data;
    },
    onSuccess: async (data) => {
      setNewKey({ id: data.key.id, secret: data.secret });
      setName("");
      setError("");
      await invalidate();
    },
    onError: (e) => setError(getApiErrorMessage(e, "Could not create key")),
  });

  const revokeMutation = useMutation({
    mutationFn: async (id: string) => {
      await api.delete(`/api/agent-keys/${id}`);
      return id;
    },
    onSuccess: (id) => {
      // Don't keep presenting a revoked credential in the alert/snippets.
      setNewKey((current) => (current?.id === id ? null : current));
      return invalidate();
    },
    onError: (e) => setError(getApiErrorMessage(e, "Could not revoke key")),
  });

  return (
    <Stack>
      <Title order={2}>Agent Keys</Title>
      <Text c="dimmed" size="sm">
        Long-lived credentials for an autonomous agent (e.g. Hermes). Send the key as an{" "}
        <Code>X-Agent-Key</Code> header on any API request. Keys don&apos;t expire — revoke them here.
      </Text>

      {error ? (
        <Alert color="red" title="Error">
          {error}
        </Alert>
      ) : null}

      <Card withBorder>
        <Title order={3} mb="xs">
          Create a key
        </Title>
        <Group align="flex-end">
          <TextInput
            label="Name"
            placeholder="e.g. Hermes agent"
            value={name}
            onChange={(e) => setName(e.currentTarget.value)}
            style={{ flex: 1 }}
          />
          <Select
            label="Access"
            data={[
              { value: "read,write", label: "Read + write" },
              { value: "read", label: "Read only" },
            ]}
            value={scopes}
            onChange={(v) => setScopes(v ?? "read,write")}
            allowDeselect={false}
            w={160}
          />
          <Button
            disabled={!name.trim()}
            loading={createMutation.isPending}
            onClick={() => createMutation.mutate()}
          >
            Create
          </Button>
        </Group>

        {newSecret ? (
          <Alert color="green" title="Copy this now — it won't be shown again" mt="md">
            <Group>
              <Code style={{ wordBreak: "break-all" }}>{newSecret}</Code>
              <CopyButton value={newSecret}>
                {({ copied, copy }) => (
                  <Button size="xs" variant="light" color={copied ? "teal" : "blue"} onClick={copy}>
                    {copied ? "Copied" : "Copy"}
                  </Button>
                )}
              </CopyButton>
            </Group>
          </Alert>
        ) : null}
      </Card>

      <ConnectAgentGuide newSecret={newSecret} />

      <Card withBorder>
        <Title order={3} mb="xs">
          Keys
        </Title>
        <Table.ScrollContainer minWidth={720}>
        <Table striped highlightOnHover withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th>Prefix</Table.Th>
              <Table.Th>Scopes</Table.Th>
              <Table.Th>Last used</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Actions</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {keys.map((k) => (
              <Table.Tr key={k.id}>
                <Table.Td>{k.name}</Table.Td>
                <Table.Td>
                  <Code>{k.token_prefix}…</Code>
                </Table.Td>
                <Table.Td>{k.scopes}</Table.Td>
                <Table.Td>{k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "never"}</Table.Td>
                <Table.Td>{k.revoked ? "revoked" : "active"}</Table.Td>
                <Table.Td>
                  {k.revoked ? null : (
                    <Button
                      size="compact-xs"
                      color="red"
                      variant="light"
                      onClick={() => revokeMutation.mutate(k.id)}
                    >
                      Revoke
                    </Button>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
            {keys.length === 0 ? (
              <Table.Tr>
                <Table.Td colSpan={6}>
                  <Text size="sm" c="dimmed">
                    No agent keys yet.
                  </Text>
                </Table.Td>
              </Table.Tr>
            ) : null}
          </Table.Tbody>
        </Table>
        </Table.ScrollContainer>
      </Card>
    </Stack>
  );
}
