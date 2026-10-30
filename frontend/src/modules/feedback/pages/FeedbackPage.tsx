import { Button, Card, Group, SegmentedControl, Stack, Text, Title } from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { useFeedback } from "@/api/hooks";
import { useAppState } from "@/state/AppStateProvider";

export function FeedbackPage() {
  const queryClient = useQueryClient();
  const { setStatus } = useAppState();

  const [statusFilter, setStatusFilter] = useState<"open" | "resolved" | "all">("open");
  const feedbackQuery = useFeedback(50, statusFilter === "all" ? null : statusFilter);
  const feedback = feedbackQuery.data ?? [];

  const resolveMutation = useMutation({
    mutationFn: async (feedbackId: string) => {
      await api.patch(`/api/feedback/${feedbackId}`, { status: "resolved" });
    },
    onSuccess: async () => {
      setStatus("Feedback marked resolved");
      await queryClient.invalidateQueries({ queryKey: ["feedback"] });
    },
    onError: (error) => {
      setStatus(getApiErrorMessage(error, "Resolve feedback failed"));
    },
  });

  return (
    <Stack>
      <Group justify="space-between" align="baseline">
        <Title order={2}>Feedback Inbox</Title>
        <Group gap="sm">
          <SegmentedControl
            value={statusFilter}
            onChange={(value) => setStatusFilter((value as "open" | "resolved" | "all") || "open")}
            data={[
              { label: "Open", value: "open" },
              { label: "Resolved", value: "resolved" },
              { label: "All", value: "all" },
            ]}
            aria-label="Filter feedback by status"
          />
          <Button variant="default" onClick={() => feedbackQuery.refetch()} loading={feedbackQuery.isFetching}>
            Refresh
          </Button>
        </Group>
      </Group>

      <Text size="sm" c="dimmed">
        Use the <strong>Feedback</strong> button in the lower-right corner to submit UI/function issues from any page.
      </Text>

      <Stack gap="sm">
        {feedback.map((row) => (
          <Card key={row.id} withBorder>
            <Group justify="space-between" align="flex-start" wrap="wrap">
              <Text fw={800}>
                {row.category.toUpperCase()} | {row.status}
              </Text>
              <Text size="sm" c="dimmed">
                {new Date(row.created_at).toLocaleString()}
              </Text>
            </Group>
            <Text size="sm" c="dimmed" mt={4}>
              <code>{row.route ?? "-"}</code> | <code>{row.id}</code>
            </Text>
            <pre style={{ margin: "8px 0 0 0", whiteSpace: "pre-wrap", fontSize: 13 }}>{row.message}</pre>
            {row.status !== "resolved" ? (
              <Group mt="sm">
                <Button
                  onClick={() => resolveMutation.mutate(row.id)}
                  loading={resolveMutation.isPending}
                  variant="default"
                >
                  Mark Resolved
                </Button>
              </Group>
            ) : null}
          </Card>
        ))}
        {feedback.length === 0 ? (
          <Text size="sm" c="dimmed">
            No feedback yet.
          </Text>
        ) : null}
      </Stack>
    </Stack>
  );
}
