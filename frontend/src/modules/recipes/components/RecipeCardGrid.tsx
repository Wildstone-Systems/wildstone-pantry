import { Badge, Button, Card, Chip, Group, Select, SimpleGrid, Stack, Text, TextInput, Title } from "@mantine/core";
import { Search, Shuffle } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import type { Recipe } from "../hooks/recipes-hooks";
import { RecipeThumb } from "./RecipeThumb";

export type RecipeFilter = "all" | "ready" | "almost" | "shop";
type RecipeSortKey = "best" | "name" | "fewest_missing";

/** How "cookable" a recipe is right now, from its match score + missing list. */
export function recipeBucket(score: number | null, missingCount: number, ingredientCount: number): RecipeFilter | null {
  if (ingredientCount === 0 || score === null) return null;
  if (missingCount === 0) return "ready";
  if (missingCount <= 2) return "almost";
  return "shop";
}

type Props = {
  recipes: Recipe[];
  scoreFor: (recipeId: string) => number | null;
  missingFor: (recipeId: string) => string[];
  onOpen: (recipe: Recipe) => void;
  onCookAlong: (recipe: Recipe) => void;
  /** Secondary actions (check, edit, cook now, delete) — same set as the table. */
  renderActions: (recipe: Recipe, ingredientCount: number) => ReactNode;
};

/** Photo-card view of the recipe box: a "Tonight's pick" up top, then a
 * filterable, searchable grid. Filters answer "what can I cook right now?". */
export function RecipeCardGrid({ recipes, scoreFor, missingFor, onOpen, onCookAlong, renderActions }: Props) {
  const [filter, setFilter] = useState<RecipeFilter>("all");
  const [sort, setSort] = useState<RecipeSortKey>("best");
  const [query, setQuery] = useState("");
  const [pickOffset, setPickOffset] = useState(0);

  const rows = useMemo(
    () =>
      recipes.map((recipe) => {
        const count = recipe.ingredient_count ?? 0;
        const score = scoreFor(recipe.id);
        const missing = missingFor(recipe.id);
        return { recipe, count, score, missing, bucket: recipeBucket(score, missing.length, count) };
      }),
    [recipes, scoreFor, missingFor]
  );

  const counts = useMemo(() => {
    const c = { all: rows.length, ready: 0, almost: 0, shop: 0 };
    rows.forEach((r) => {
      if (r.bucket) c[r.bucket] += 1;
    });
    return c;
  }, [rows]);

  // Pick candidates: everything you can make now; otherwise the closest ones.
  const pickCandidates = useMemo(() => {
    const ready = rows.filter((r) => r.bucket === "ready");
    if (ready.length) return ready;
    return rows
      .filter((r) => r.bucket !== null)
      .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
      .slice(0, 5);
  }, [rows]);
  const pick = pickCandidates.length ? pickCandidates[pickOffset % pickCandidates.length] : null;

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = rows.filter(
      (r) => (filter === "all" || r.bucket === filter) && (!q || r.recipe.name.toLowerCase().includes(q))
    );
    const byName = (a: (typeof rows)[number], b: (typeof rows)[number]) => a.recipe.name.localeCompare(b.recipe.name);
    return [...filtered].sort((a, b) => {
      if (sort === "name") return byName(a, b);
      if (sort === "fewest_missing") {
        // Unmatched recipes (no score) go last.
        const am = a.bucket ? a.missing.length : Infinity;
        const bm = b.bucket ? b.missing.length : Infinity;
        return am - bm || byName(a, b);
      }
      return (b.score ?? -1) - (a.score ?? -1) || byName(a, b);
    });
  }, [rows, filter, sort, query]);

  const statusBadge = (bucket: RecipeFilter | null, missingCount: number) => {
    if (bucket === "ready") return <Badge color="green">Can make now</Badge>;
    if (bucket === "almost") return <Badge color="yellow">Missing {missingCount}</Badge>;
    if (bucket === "shop") return <Badge color="gray">Missing {missingCount}</Badge>;
    return (
      <Badge color="gray" variant="light">
        No ingredients yet
      </Badge>
    );
  };

  const missingText = (bucket: RecipeFilter | null, missing: string[]) =>
    bucket === null
      ? "Add ingredients to see what you're missing"
      : missing.length
        ? `Need: ${missing.join(", ")}`
        : "You have everything";

  return (
    <Stack>
      {pick && (
        <Card withBorder padding={0} radius="lg" aria-label="Tonight's pick">
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing={0}>
            <div style={{ position: "relative", cursor: "pointer" }} onClick={() => onOpen(pick.recipe)}>
              <RecipeThumb src={pick.recipe.image_src} h={260} />
              <div style={{ position: "absolute", top: 12, left: 12 }}>
                {statusBadge(pick.bucket, pick.missing.length)}
              </div>
            </div>
            <Stack p="lg" gap="xs" justify="center">
              <Text size="xs" c="dimmed" tt="uppercase" fw={600}>
                Tonight&apos;s pick
              </Text>
              <Title order={3}>{pick.recipe.name}</Title>
              <Text size="sm" c="dimmed">
                {pick.count} ingredient{pick.count === 1 ? "" : "s"} · serves {pick.recipe.servings} ·{" "}
                {missingText(pick.bucket, pick.missing)}
              </Text>
              <Group mt="sm">
                <Button onClick={() => onCookAlong(pick.recipe)}>Start cooking</Button>
                {pickCandidates.length > 1 && (
                  <Button
                    variant="default"
                    leftSection={<Shuffle size={14} />}
                    onClick={() => setPickOffset((n) => n + 1)}
                  >
                    Shuffle
                  </Button>
                )}
                <Button variant="subtle" onClick={() => onOpen(pick.recipe)}>
                  Details
                </Button>
              </Group>
            </Stack>
          </SimpleGrid>
        </Card>
      )}

      <Group wrap="wrap" gap="sm">
        <TextInput
          placeholder="Search recipes…"
          aria-label="Search recipes"
          leftSection={<Search size={14} />}
          value={query}
          onChange={(e) => setQuery(e.currentTarget.value)}
          w={{ base: "100%", sm: 260 }}
        />
        <Chip.Group multiple={false} value={filter} onChange={(v) => setFilter(v as RecipeFilter)}>
          <Group gap="xs">
            <Chip value="all">All ({counts.all})</Chip>
            <Chip value="ready">Can make now ({counts.ready})</Chip>
            <Chip value="almost">Missing 1–2 ({counts.almost})</Chip>
            <Chip value="shop">Needs shopping ({counts.shop})</Chip>
          </Group>
        </Chip.Group>
        <Select
          aria-label="Sort recipes"
          value={sort}
          onChange={(v) => setSort((v as RecipeSortKey) ?? "best")}
          allowDeselect={false}
          w={170}
          data={[
            { value: "best", label: "Sort: Best match" },
            { value: "fewest_missing", label: "Sort: Fewest missing" },
            { value: "name", label: "Sort: Name" },
          ]}
        />
      </Group>

      {visible.length === 0 ? (
        <Text size="sm" c="dimmed">
          No recipes match this filter.
        </Text>
      ) : (
        <SimpleGrid cols={{ base: 1, xs: 2, md: 3, xl: 4 }} spacing="md">
          {visible.map(({ recipe, count, missing, bucket }) => (
            <Card
              key={recipe.id}
              withBorder
              radius="lg"
              padding="sm"
              style={{ cursor: "pointer" }}
              onClick={() => onOpen(recipe)}
              data-testid="recipe-card"
            >
              <Card.Section mb="sm" style={{ position: "relative" }}>
                <RecipeThumb src={recipe.image_src} h={170} />
                <div style={{ position: "absolute", top: 10, left: 10 }}>{statusBadge(bucket, missing.length)}</div>
              </Card.Section>
              <Text fw={600} lineClamp={1} title={recipe.name}>
                {recipe.name}
              </Text>
              <Text size="xs" c="dimmed" lineClamp={1} title={missing.join(", ") || undefined}>
                {missingText(bucket, missing)}
              </Text>
              <Group mt="sm" justify="space-between" wrap="wrap" gap="xs" onClick={(e) => e.stopPropagation()}>
                <Button size="sm" style={{ flexShrink: 0 }} disabled={count === 0} onClick={() => onCookAlong(recipe)}>
                  Cook along
                </Button>
                {renderActions(recipe, count)}
              </Group>
            </Card>
          ))}
        </SimpleGrid>
      )}
    </Stack>
  );
}
