import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Modal,
  Progress,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { useState } from "react";
import { ChevronLeft, ChevronRight, Pencil, Trash2 } from "lucide-react";

import { getApiErrorMessage } from "@/api/errors";
import { QuantityStepper } from "@/components/QuantityStepper";
import { useHouseholdProfiles } from "@/api/hooks";
import { useAppState } from "@/state/AppStateProvider";

import {
  shiftDay,
  todayLocal,
  useCalorieRollup,
  useCalorieSummary,
  useDeletePlate,
  useEditPlate,
  type CalorieEntry,
} from "../hooks/meals-hooks";

/** "Today" / "Yesterday" / "Sat, Aug 2" — a bare ISO date reads poorly as a heading. */
function dayLabel(day: string): string {
  const today = todayLocal();
  if (day === today) return "Today";
  if (day === shiftDay(today, -1)) return "Yesterday";
  if (day === shiftDay(today, 1)) return "Tomorrow";
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

const UNIT_OPTIONS = ["each", "slice", "serving", "g", "kg", "oz", "cup"].map((u) => ({ value: u, label: u }));

function n(v: string | number | null | undefined): number | null {
  if (v == null) return null;
  const x = Number(v);
  return Number.isNaN(x) ? null : x;
}

// "2 serving" reads oddly — pluralize the common units so entries read like a
// normal calorie tracker ("2 servings", "150 g", "3 each").
function pluralUnit(qty: number, unit: string): string {
  if (qty === 1 || unit === "g" || unit === "kg" || unit === "oz" || unit === "cup") return unit;
  return `${unit}s`;
}

function formatAmount(qty: number, unit: string, weightGrams: number | null): string {
  const base = `${qty % 1 === 0 ? qty : qty.toFixed(2)} ${pluralUnit(qty, unit)}`;
  return weightGrams != null ? `${base} (${Math.round(weightGrams)}g)` : base;
}

export function CaloriesPage() {
  const { activeProfileId } = useAppState();
  const { data: profiles } = useHouseholdProfiles();
  const [day, setDay] = useState(todayLocal());
  const rollup = useCalorieRollup(activeProfileId, day);
  const summary = useCalorieSummary(day);
  const isToday = day === todayLocal();
  const deletePlate = useDeletePlate();
  const editPlate = useEditPlate();

  const [pendingDelete, setPendingDelete] = useState<CalorieEntry | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const [pendingEdit, setPendingEdit] = useState<CalorieEntry | null>(null);
  const [editQuantity, setEditQuantity] = useState<number | string>(1);
  const [editUnit, setEditUnit] = useState("each");
  const [editWeight, setEditWeight] = useState<number | string>("");
  const [editError, setEditError] = useState<string | null>(null);

  const handleDelete = async () => {
    if (!pendingDelete) return;
    try {
      await deletePlate.mutateAsync(pendingDelete.event_id);
      setPendingDelete(null);
    } catch (err) {
      setDeleteError(getApiErrorMessage(err, "Could not delete that entry"));
    }
  };

  const openEdit = (entry: CalorieEntry) => {
    setEditError(null);
    setEditQuantity(n(entry.quantity) ?? 1);
    setEditUnit(entry.unit);
    setEditWeight(n(entry.weight_grams) ?? "");
    setPendingEdit(entry);
  };

  const handleEdit = async () => {
    if (!pendingEdit) return;
    try {
      await editPlate.mutateAsync({
        eventId: pendingEdit.event_id,
        quantity: Number(editQuantity) || 1,
        unit: editUnit,
        weight_grams: editWeight === "" ? null : Number(editWeight),
      });
      setPendingEdit(null);
    } catch (err) {
      setEditError(getApiErrorMessage(err, "Could not save that change"));
    }
  };

  const activeProfile = (profiles ?? []).find((p) => p.id === activeProfileId);
  const total = n(rollup.data?.total_calories) ?? 0;
  const target = n(rollup.data?.target ?? null);
  const pct = target && target > 0 ? Math.min(100, Math.round((total / target) * 100)) : null;
  const totalProtein = n(rollup.data?.total_protein_g ?? null);
  const totalFat = n(rollup.data?.total_fat_g ?? null);
  const totalCarbs = n(rollup.data?.total_carbs_g ?? null);

  // Same colors as the protein/fat/carbs pills below, sized by each macro's
  // share of calories (protein/carbs 4 kcal/g, fat 9 kcal/g) — not grams,
  // since the bar itself represents calories toward the target. Segments sum
  // to `pct`, so the bar's total fill still reads as "% of target" at a
  // glance; falls back to a plain bar when there's no macro data to split.
  const proteinKcal = (totalProtein ?? 0) * 4;
  const fatKcal = (totalFat ?? 0) * 9;
  const carbsKcal = (totalCarbs ?? 0) * 4;
  const macroKcalTotal = proteinKcal + fatKcal + carbsKcal;
  const macroPct = (kcal: number) => (pct != null && macroKcalTotal > 0 ? (kcal / macroKcalTotal) * pct : 0);

  return (
    <Stack>
      <Title order={2}>Calories</Title>

      {!activeProfileId ? (
        <Alert color="yellow">Pick a person with the profile selector (top-right) to see their intake.</Alert>
      ) : (
        <Card withBorder padding="md">
          <Group justify="space-between" wrap="wrap" gap="sm">
            <Group gap={6} wrap="nowrap">
              <ActionIcon
                variant="light"
                size="lg"
                aria-label="Previous day"
                onClick={() => setDay((d) => shiftDay(d, -1))}
              >
                <ChevronLeft size={18} />
              </ActionIcon>
              <ActionIcon
                variant="light"
                size="lg"
                aria-label="Next day"
                // Nothing can be eaten in the future; stopping at today keeps
                // you from paging into a run of guaranteed-empty days.
                disabled={isToday}
                onClick={() => setDay((d) => shiftDay(d, 1))}
              >
                <ChevronRight size={18} />
              </ActionIcon>
              <TextInput
                type="date"
                size="xs"
                w={150}
                aria-label="Date"
                value={day}
                max={todayLocal()}
                onChange={(e) => setDay(e.currentTarget.value || todayLocal())}
              />
              {!isToday && (
                <Button variant="subtle" size="compact-xs" onClick={() => setDay(todayLocal())}>
                  Today
                </Button>
              )}
            </Group>
            <Text>
              <b>{total.toFixed(0)}</b> kcal{target != null ? ` / ${target.toFixed(0)}` : ""}
            </Text>
          </Group>
          <Title order={4} mt={4}>
            {activeProfile?.display_name ?? "Intake"} — {dayLabel(day)}
          </Title>
          {pct != null &&
            (macroKcalTotal > 0 ? (
              <Progress.Root mt="sm" size="lg">
                <Tooltip label={`Protein: ${(totalProtein ?? 0).toFixed(0)}g`}>
                  <Progress.Section value={macroPct(proteinKcal)} color="teal" />
                </Tooltip>
                <Tooltip label={`Fat: ${(totalFat ?? 0).toFixed(0)}g`}>
                  <Progress.Section value={macroPct(fatKcal)} color="orange" />
                </Tooltip>
                <Tooltip label={`Carbs: ${(totalCarbs ?? 0).toFixed(0)}g`}>
                  <Progress.Section value={macroPct(carbsKcal)} color="yellow" />
                </Tooltip>
              </Progress.Root>
            ) : (
              <Progress mt="sm" value={pct} color={pct >= 100 ? "red" : "green"} />
            ))}
          {target == null ? (
            <Text size="xs" c="dimmed" mt="xs">
              No daily target set — an admin can set one on the Household page.
            </Text>
          ) : null}
          {(totalProtein != null || totalFat != null || totalCarbs != null) && (
            <Group gap="xs" mt="sm">
              {totalProtein != null && (
                <Badge variant="light" color="teal">
                  {totalProtein.toFixed(0)}g protein
                </Badge>
              )}
              {totalFat != null && (
                <Badge variant="light" color="orange">
                  {totalFat.toFixed(0)}g fat
                </Badge>
              )}
              {totalCarbs != null && (
                <Badge variant="light" color="yellow">
                  {totalCarbs.toFixed(0)}g carbs
                </Badge>
              )}
            </Group>
          )}

          <Table.ScrollContainer minWidth={560} mt="md">
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Item</Table.Th>
                <Table.Th>Amount</Table.Th>
                <Table.Th>Calories</Table.Th>
                <Table.Th>Protein</Table.Th>
                <Table.Th>Fat</Table.Th>
                <Table.Th>Carbs</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(rollup.data?.entries ?? []).length === 0 ? (
                <Table.Tr>
                  <Table.Td colSpan={7}>
                    <Text c="dimmed">
                      {isToday ? "Nothing logged yet today." : `Nothing logged on ${dayLabel(day)}.`}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ) : (
                rollup.data?.entries.map((e) => {
                  const qty = n(e.quantity) ?? 0;
                  const protein = n(e.protein_g);
                  const fat = n(e.fat_g);
                  const carbs = n(e.carbs_g);
                  return (
                    <Table.Tr key={e.event_id}>
                      <Table.Td>{e.item_name}</Table.Td>
                      <Table.Td>{formatAmount(qty, e.unit, n(e.weight_grams))}</Table.Td>
                      <Table.Td>{e.calories == null ? "—" : Number(e.calories).toFixed(0)}</Table.Td>
                      <Table.Td>{protein == null ? "—" : `${protein.toFixed(0)}g`}</Table.Td>
                      <Table.Td>{fat == null ? "—" : `${fat.toFixed(0)}g`}</Table.Td>
                      <Table.Td>{carbs == null ? "—" : `${carbs.toFixed(0)}g`}</Table.Td>
                      <Table.Td>
                        <Group gap={4} wrap="nowrap">
                          <Tooltip label="Edit this entry">
                            <ActionIcon variant="subtle" aria-label={`Edit ${e.item_name}`} onClick={() => openEdit(e)}>
                              <Pencil size={16} />
                            </ActionIcon>
                          </Tooltip>
                          <Tooltip label="Delete this entry">
                            <ActionIcon
                              variant="subtle"
                              color="red"
                              aria-label={`Delete ${e.item_name}`}
                              onClick={() => {
                                setDeleteError(null);
                                setPendingDelete(e);
                              }}
                            >
                              <Trash2 size={16} />
                            </ActionIcon>
                          </Tooltip>
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  );
                })
              )}
            </Table.Tbody>
          </Table>
          </Table.ScrollContainer>
          {(rollup.data?.entries ?? []).some((e) => e.calories == null) && (
            <Text size="xs" c="dimmed" mt="xs">
              Entries showing "—" have no nutrition data for their unit — edit the item's serving size or
              per-100g nutrition in Catalog, or log it by weight (g) instead.
            </Text>
          )}
        </Card>
      )}

      <Card withBorder padding="md">
        <Title order={4} mb="sm">
          Household — {dayLabel(day)}
        </Title>
        <Group>
          {(summary.data?.profiles ?? []).length === 0 ? (
            <Text c="dimmed">No one logged a meal {isToday ? "today" : `on ${dayLabel(day)}`}.</Text>
          ) : (
            summary.data?.profiles.map((p) => (
              <Badge key={p.profile_id} size="lg" variant="light">
                {p.display_name}: {Number(p.total_calories).toFixed(0)}
                {p.target != null ? `/${Number(p.target).toFixed(0)}` : ""} kcal
              </Badge>
            ))
          )}
        </Group>
      </Card>

      <Modal opened={pendingDelete !== null} onClose={() => setPendingDelete(null)} title="Delete this entry?">
        <Stack>
          <Text size="sm">
            <b>{pendingDelete?.item_name}</b> will be removed from today's log. If it decremented your pantry, that
            amount is restored.
          </Text>
          {deleteError && (
            <Text size="xs" c="red">
              {deleteError}
            </Text>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setPendingDelete(null)}>
              Cancel
            </Button>
            <Button color="red" loading={deletePlate.isPending} onClick={handleDelete}>
              Delete
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal opened={pendingEdit !== null} onClose={() => setPendingEdit(null)} title={`Edit ${pendingEdit?.item_name ?? ""}`}>
        <Stack>
          <Group align="flex-end" gap="md" wrap="wrap">
            <QuantityStepper label="Servings / qty" min={0} value={editQuantity} onChange={setEditQuantity} />
            <Select
              label="Unit"
              data={UNIT_OPTIONS}
              value={editUnit}
              onChange={(v) => setEditUnit(v || "each")}
              w={130}
            />
          </Group>
          <QuantityStepper
            label="Plated weight (g)"
            ariaLabel="plated weight"
            min={0}
            step={10}
            value={editWeight}
            onChange={setEditWeight}
            inputWidth={72}
          />
          {editError && (
            <Text size="xs" c="red">
              {editError}
            </Text>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setPendingEdit(null)}>
              Cancel
            </Button>
            <Button loading={editPlate.isPending} onClick={handleEdit}>
              Save
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
