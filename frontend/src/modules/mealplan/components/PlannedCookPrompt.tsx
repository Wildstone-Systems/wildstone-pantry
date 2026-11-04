import { Alert, Button, Modal, Stack, Text } from "@mantine/core";
import { useState } from "react";

import { getApiErrorMessage } from "@/api/errors";

import { useMarkMealCooked, type MealPlanEntry } from "../hooks/mealplan-hooks";

function mealLabel(entry: MealPlanEntry): string {
  const [y, m, d] = entry.plan_date.split("-").map(Number);
  const weekday = new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: "long" });
  return `${weekday}'s ${entry.meal_type}`;
}

/**
 * After a cook that didn't start from the plan: "Was this Tuesday's dinner?"
 * (#32). Links only what the person picks; "No" leaves the plan untouched.
 */
export function PlannedCookPrompt({
  candidates,
  cookEventId,
  onDone,
}: {
  candidates: MealPlanEntry[];
  cookEventId: string;
  onDone: () => void;
}) {
  const markCooked = useMarkMealCooked();
  const [error, setError] = useState<string | null>(null);

  async function link(entry: MealPlanEntry) {
    setError(null);
    try {
      await markCooked.mutateAsync({ entryId: entry.id, cookEventId });
      onDone();
    } catch (err) {
      setError(getApiErrorMessage(err, "Could not update the meal plan"));
    }
  }

  const single = candidates.length === 1 ? candidates[0] : null;

  return (
    <Modal opened={candidates.length > 0} onClose={onDone} title="On the meal plan?">
      <Stack gap="sm">
        {error && (
          <Alert color="red" variant="light">
            {error}
          </Alert>
        )}
        <Text size="sm">
          {single ? `Was this ${mealLabel(single)}?` : "This is planned more than once today. Which meal was it?"}
        </Text>
        {candidates.map((entry) => (
          <Button key={entry.id} loading={markCooked.isPending} onClick={() => link(entry)}>
            {single ? `Yes, ${mealLabel(entry)}` : `${mealLabel(entry)} (×${entry.servings})`}
          </Button>
        ))}
        <Button variant="default" disabled={markCooked.isPending} onClick={onDone}>
          No, it wasn't planned
        </Button>
      </Stack>
    </Modal>
  );
}
