import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Loader,
  Modal,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { useLocations } from "@/api/hooks";
import { getApiErrorMessage } from "@/api/errors";
import { ROUTES } from "@/routes/paths";

import {
  useCompleteHandoff,
  useHandoffSession,
  useUpdateHandoffItem,
  type HandoffItem,
} from "../hooks/handoff-hooks";

const STATE_COLOR: Record<string, string> = {
  pending: "gray",
  opened: "blue",
  added: "green",
  substituted: "teal",
  unavailable: "red",
  skipped: "yellow",
  purchased: "green",
};

/** Guided search-handoff shopping session (ADR 0019 D3): each row deep-links
 * into the retailer's own site; the user marks progress as they build their
 * basket there, then confirms what was actually bought. */
export function HandoffSessionPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const session = useHandoffSession(sessionId);
  const updateItem = useUpdateHandoffItem(sessionId ?? "");
  const complete = useCompleteHandoff(sessionId ?? "");
  const locations = useLocations();

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [locationId, setLocationId] = useState<string | null>(null);
  const [itemLocations, setItemLocations] = useState<Record<string, string | null>>({});
  const [substituteFor, setSubstituteFor] = useState<HandoffItem | null>(null);
  const [subName, setSubName] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Return-focus prompt (household testing feedback): most recently opened
  // search link, and whether its "welcome back" bar has been dismissed since.
  const [lastOpenedId, setLastOpenedId] = useState<string | null>(null);
  const [promptDismissed, setPromptDismissed] = useState(true);

  const items = useMemo(() => session.data?.items ?? [], [session.data]);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const boughtStates = new Set(["added", "substituted", "purchased"]);

  useEffect(() => {
    let lastFire = 0;
    const wake = () => {
      const now = Date.now();
      if (now - lastFire < 300) return;
      lastFire = now;
      if (document.visibilityState === "hidden") return;
      const hasOpened = itemsRef.current.some((i) => i.state === "opened");
      if (hasOpened) setPromptDismissed(false);
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") wake();
    };
    window.addEventListener("focus", wake);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", wake);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  const openConfirm = () => {
    const initial: Record<string, boolean> = {};
    const initialLocations: Record<string, string | null> = {};
    for (const item of items) {
      initial[item.id] = boughtStates.has(item.state);
      initialLocations[item.id] = item.suggested_location_id ?? locationId;
    }
    setChecked(initial);
    setItemLocations(initialLocations);
    setConfirmOpen(true);
  };

  const confirm = async () => {
    setError(null);
    try {
      const overrides: Record<string, string> = {};
      for (const item of items) {
        if (!checked[item.id]) continue;
        const chosen = itemLocations[item.id];
        if (chosen && chosen !== locationId) overrides[item.id] = chosen;
      }
      const result = await complete.mutateAsync({
        purchased_item_ids: items.filter((i) => checked[i.id]).map((i) => i.id),
        location_id: locationId,
        ...(Object.keys(overrides).length > 0 ? { item_locations: overrides } : {}),
      });
      setConfirmOpen(false);
      navigate(ROUTES.shoppingList, {
        state: { handoffDone: { retailer: result.retailer_label, count: result.purchased_count ?? 0 } },
      });
    } catch (err) {
      setError(getApiErrorMessage(err, "Could not record the shopping trip"));
    }
  };

  if (session.isLoading) return <Loader />;
  if (session.isError || !session.data) {
    return <Alert color="red">This shopping session could not be loaded.</Alert>;
  }

  const data = session.data;
  const doneCount = items.filter((i) => i.state !== "pending" && i.state !== "opened").length;

  const mark = (item: HandoffItem, state: string) => updateItem.mutate({ itemId: item.id, state });

  const promptItem = lastOpenedId ? items.find((i) => i.id === lastOpenedId) : undefined;
  const showPrompt = !!promptItem && promptItem.state === "opened" && !promptDismissed;

  const row = (item: HandoffItem) => (
    <Card key={item.id} withBorder padding="sm" radius="md">
      <Group justify="space-between" wrap="nowrap" align="flex-start">
        <div style={{ minWidth: 0 }}>
          <Group gap="xs" wrap="nowrap">
            <Text fw={500} td={item.state === "skipped" ? "line-through" : undefined}>
              {item.name}
            </Text>
            <Text span size="sm" c="dimmed">
              {String(item.quantity)} {item.unit}
            </Text>
            <Badge size="xs" variant="light" color={STATE_COLOR[item.state] ?? "gray"}>
              {item.state}
            </Badge>
          </Group>
          {item.external_name ? (
            <Text size="xs" c="dimmed">
              ↳ {item.external_name}
            </Text>
          ) : null}
          {item.search_url ? (
            <Anchor
              href={item.search_url}
              target="_blank"
              rel="noopener noreferrer"
              size="sm"
              onClick={() => {
                if (item.state === "pending") mark(item, "opened");
                setLastOpenedId(item.id);
              }}
            >
              Search “{item.search_query}” at {data.retailer_label} →
            </Anchor>
          ) : null}
        </div>
        <Group gap={4} wrap="nowrap" style={{ flexShrink: 0 }}>
          <Button size="compact-xs" variant={item.state === "added" ? "filled" : "light"} color="green" onClick={() => mark(item, "added")}>
            Added
          </Button>
          <Button
            size="compact-xs"
            variant={item.state === "substituted" ? "filled" : "light"}
            color="teal"
            onClick={() => {
              setSubName(item.external_name ?? "");
              setSubstituteFor(item);
            }}
          >
            Substitute
          </Button>
          <Button
            size="compact-xs"
            variant={item.state === "unavailable" ? "filled" : "light"}
            color="red"
            onClick={() => mark(item, "unavailable")}
          >
            Out
          </Button>
          <Button
            size="compact-xs"
            variant={item.state === "skipped" ? "filled" : "light"}
            color="gray"
            onClick={() => mark(item, "skipped")}
          >
            Skip
          </Button>
        </Group>
      </Group>
    </Card>
  );

  return (
    <Stack>
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2}>Shopping at {data.retailer_label}</Title>
          <Text c="dimmed" size="sm">
            Open each search in a new tab, add products to your {data.retailer_label} basket there, and mark
            progress here. Nothing is sent to the store — this list is yours.
          </Text>
        </div>
        <Badge size="lg" variant="light">
          {doneCount}/{items.length} handled
        </Badge>
      </Group>

      {data.status !== "active" ? (
        <Alert color="blue">This session is {data.status} — start a new one from the shopping list.</Alert>
      ) : null}

      {showPrompt && promptItem ? (
        <Alert color="blue" withCloseButton onClose={() => setPromptDismissed(true)} title="Welcome back">
          <Group justify="space-between" wrap="nowrap" gap="xs">
            <Text size="sm">Did you add {promptItem.name} to your basket?</Text>
            <Group gap={4} wrap="nowrap" style={{ flexShrink: 0 }}>
              <Button size="compact-xs" color="green" onClick={() => mark(promptItem, "added")}>
                Added
              </Button>
              <Button size="compact-xs" color="red" onClick={() => mark(promptItem, "unavailable")}>
                Out
              </Button>
              <Button
                size="compact-xs"
                color="teal"
                onClick={() => {
                  setSubName(promptItem.external_name ?? "");
                  setSubstituteFor(promptItem);
                }}
              >
                Substitute…
              </Button>
              <Button size="compact-xs" color="gray" onClick={() => mark(promptItem, "skipped")}>
                Skip
              </Button>
            </Group>
          </Group>
        </Alert>
      ) : null}

      <Stack gap="xs">{items.map(row)}</Stack>

      {data.status === "active" ? (
        <Group justify="flex-end">
          <Button size="md" onClick={openConfirm} disabled={items.length === 0}>
            Finish shopping
          </Button>
        </Group>
      ) : null}

      {/* Substitute dialog: capture what actually went in the basket — this is
          what teaches the product map (ADR 0019 D4). */}
      <Modal
        opened={substituteFor !== null}
        onClose={() => setSubstituteFor(null)}
        title={`Substitute for ${substituteFor?.name ?? ""}`}
      >
        <Stack>
          <TextInput
            label="What did you pick instead?"
            placeholder="e.g. Store-brand creamy peanut butter"
            value={subName}
            onChange={(e) => setSubName(e.currentTarget.value)}
            data-autofocus
          />
          <Group justify="flex-end">
            <Button
              onClick={() => {
                if (substituteFor) {
                  updateItem.mutate({ itemId: substituteFor.id, state: "substituted", external_name: subName.trim() });
                }
                setSubstituteFor(null);
              }}
              disabled={!subName.trim()}
            >
              Save substitute
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* The 30-second confirmation that replaces the purchase-history API
          nobody offers (ADR 0019 D5). */}
      <Modal opened={confirmOpen} onClose={() => setConfirmOpen(false)} title="What made it home?">
        <Stack>
          <Text size="sm" c="dimmed">
            Checked items are marked purchased on the list and recorded as a purchase. Unchecked items stay on
            the list for next time.
          </Text>
          <Select
            label="Default location"
            placeholder="Skip inventory"
            clearable
            data={(locations.data ?? []).map((l) => ({ value: l.id, label: l.name }))}
            value={locationId}
            onChange={setLocationId}
          />
          {items.map((item) => (
            <Group key={item.id} justify="space-between" wrap="nowrap" align="center" gap="xs">
              <Checkbox
                label={`${item.name}${item.external_name ? ` (${item.external_name})` : ""}`}
                checked={checked[item.id] ?? false}
                onChange={(e) => setChecked({ ...checked, [item.id]: e.currentTarget.checked })}
              />
              {checked[item.id] ? (
                <Select
                  aria-label={`Location for ${item.name}`}
                  size="xs"
                  w={150}
                  placeholder="Use default"
                  clearable
                  data={(locations.data ?? []).map((l) => ({ value: l.id, label: l.name }))}
                  value={itemLocations[item.id] ?? null}
                  onChange={(v) => setItemLocations({ ...itemLocations, [item.id]: v })}
                  style={{ flexShrink: 0 }}
                />
              ) : null}
            </Group>
          ))}
          {error ? <Alert color="red">{error}</Alert> : null}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirmOpen(false)}>
              Keep shopping
            </Button>
            <Button onClick={confirm} loading={complete.isPending}>
              Record trip
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
