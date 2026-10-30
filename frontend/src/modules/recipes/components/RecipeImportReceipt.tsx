import { Alert, Stack, Text } from "@mantine/core";
import { ClassifierVerificationNotice } from "@/components/ClassifierVerificationNotice";
import type { Recipe } from "../hooks/recipes-hooks";

/** The saved receipt records a confirmation action, never certified accuracy. */
export function RecipeImportReceipt({ recipe }: { recipe: Recipe }) {
  const receipt = recipe.import_review;
  if (!receipt) return null;
  const sourcePoints = Array.from(recipe.source_text ?? "");
  return <Stack gap="xs">
    <ClassifierVerificationNotice />
    <Alert color="yellow" title={<Text fw={600} c="var(--mantine-color-text)">Imported recipe review history</Text>}>
      <Text size="sm">{receipt.stale
        ? "This recipe or its catalog context has changed since confirmation. Check the source again before relying on it."
        : "The imported values were explicitly confirmed. Confirmation does not guarantee correct identification, quantities or cooking instructions."}</Text>
      {(receipt.lines ?? []).filter((line) => line.warnings.length > 0).map((line) => <Stack gap={2} mt="xs" key={line.source_line_id}>
        <Text size="xs" fw={600}>{line.field_path ?? `Source line ${(line.line_index ?? 0) + 1}`}</Text>
        {!receipt.stale && line.start != null && line.end != null && <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>{sourcePoints.slice(line.start, line.end).join("")}</Text>}
        {line.warnings.map((warning, index) => <Text size="sm" key={index}>{warning}</Text>)}
      </Stack>)}
    </Alert>
  </Stack>;
}
