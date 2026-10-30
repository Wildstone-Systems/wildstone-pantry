import { Alert, Button, Group, Stack, Text } from "@mantine/core";

export type ContainerCandidate = {
  lot_id: string;
  quantity_remaining: string | number;
  unit: string;
  expiration_date: string | null;
  status: string;
};

export type LocationCandidate = {
  location_id: string;
  location_name: string;
  quantity: string;
};

type Props = {
  itemName: string;
  containers: ContainerCandidate[];
  locations: LocationCandidate[];
  busy?: boolean;
  onPickContainer: (lotId: string) => void;
  onPickLocation: (locationId: string) => void;
  onCancel: () => void;
};

function amount(candidate: ContainerCandidate): string {
  const qty = Math.round(Number(candidate.quantity_remaining) * 100) / 100;
  return `${qty} ${candidate.unit} left`;
}

/**
 * The disambiguation prompt (ADR 0023 D3).
 *
 * Which container someone just emptied is a fact only they have — the empty tub
 * carries the *product* barcode, so the gesture can never disambiguate itself.
 * Guessing FEFO would mislabel a real container's expiry and invent a phantom
 * open one, so this asks. It is deliberately not skippable and has no "always
 * pick the oldest" escape: the drift it prevents accumulates fastest on exactly
 * the items used often enough to have two open at once.
 *
 * Container and location ambiguity share one prompt rather than firing two
 * modals back to back (D13) — picking a container implies its location.
 */
export function ScanPrompt({
  itemName,
  containers,
  locations,
  busy,
  onPickContainer,
  onPickLocation,
  onCancel,
}: Props) {
  const askingContainer = containers.length > 0;

  return (
    <Alert
      color="blue"
      variant="light"
      title={askingContainer ? `Which ${itemName} did you finish?` : `Where is the ${itemName}?`}
    >
      <Stack gap="xs">
        <Text size="sm">
          {askingContainer
            ? "More than one is open. Pick the one you just emptied — the oldest is listed first."
            : "It's in more than one place — pick where to take it from."}
        </Text>
        <Group gap="xs">
          {askingContainer
            ? containers.map((candidate, index) => (
                <Button
                  key={candidate.lot_id}
                  size="sm"
                  variant={index === 0 ? "filled" : "light"}
                  loading={busy}
                  onClick={() => onPickContainer(candidate.lot_id)}
                >
                  {amount(candidate)}
                  {candidate.expiration_date ? ` · best by ${candidate.expiration_date}` : ""}
                </Button>
              ))
            : locations.map((candidate) => (
                <Button
                  key={candidate.location_id}
                  size="sm"
                  variant="light"
                  loading={busy}
                  onClick={() => onPickLocation(candidate.location_id)}
                >
                  {candidate.location_name} ({Number(candidate.quantity)})
                </Button>
              ))}
          <Button size="sm" variant="subtle" color="gray" onClick={onCancel}>
            Cancel
          </Button>
        </Group>
      </Stack>
    </Alert>
  );
}
