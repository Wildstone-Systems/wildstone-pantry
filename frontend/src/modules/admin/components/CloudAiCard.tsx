import { Badge, Checkbox, Text } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

import { CollapsibleCard } from "./CollapsibleCard";

type AiStatus = {
  providers: { key: string; label: string; configured: boolean; capabilities: string[] }[];
  active_vision_provider: string | null;
  cloud_consent: boolean;
};

/**
 * AI status + the cloud-AI privacy opt-in (ADR 0018 §9). Local providers need
 * no consent — nothing leaves the machine. Cloud AI stays off until the
 * household explicitly allows sending images for processing.
 */
export function CloudAiCard() {
  const queryClient = useQueryClient();

  const { data } = useQuery({
    queryKey: ["aiStatus"],
    queryFn: async () => (await api.get<AiStatus>("/api/ai/status")).data,
  });

  const consent = useMutation({
    mutationFn: async (allow: boolean) => (await api.post("/api/ai/consent", { allow })).data,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["aiStatus"] }),
  });

  const active = data?.providers.find((p) => p.key === data.active_vision_provider);

  return (
    <CollapsibleCard
      id="cloud_ai"
      title="AI assistance"
      status={
        active ? (
          <Badge color="green" variant="light">
            {active.label}
          </Badge>
        ) : (
          <Badge color="gray" variant="light">
            rules only
          </Badge>
        )
      }
    >
      <Text size="sm" c="dimmed">
        Receipt photos are read by deterministic rules first. Configure a local AI above for smarter
        reading with nothing leaving this machine — a configured local AI always wins over cloud.
      </Text>
      <Checkbox
        label="Allow PantryOS Connect cloud AI (sends receipt photos for processing; requires an active subscription; processed and discarded, never stored)"
        checked={data?.cloud_consent ?? false}
        disabled={consent.isPending}
        onChange={(e) => consent.mutate(e.currentTarget.checked)}
      />
    </CollapsibleCard>
  );
}
