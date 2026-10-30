import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  FileButton,
  Group,
  Image,
  Modal,
  Select,
  Stack,
  Table,
  Text,
  Textarea,
  Title,
  Tooltip,
} from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { ChefHat, ListChecks, Pencil, PlayCircle, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router";

import { getApiErrorMessage } from "@/api/errors";
import { useLocations } from "@/api/hooks";
import { SortableTh, applySort, toggleSort, type SortState } from "@/components/table-sort";

import { IngredientMatchBanner } from "../components/IngredientMatchBanner";
import { ROUTES } from "@/routes/paths";
import { useAppState } from "@/state/AppStateProvider";
import { RecipeAvailabilityModal } from "../components/RecipeAvailabilityModal";
import { RecipeImportSearch } from "../components/RecipeImportSearch";
import type { Recipe, RecipeSuggestion, RecipeParseApiResponse } from "../hooks/recipes-hooks";
import { useAdHocDrafts } from "../hooks/ad-hoc-hooks";
import {
  useCookRecipe,
  useDeleteRecipe,
  useParseRecipeImage,
  useParseRecipeText,
  useRecipeSuggestions,
  useRecipes,
} from "../hooks/recipes-hooks";

/** Sentinel for the "everywhere" option — Mantine Select cannot hold a null value. */
const ALL_LOCATIONS = "__all__";

export function RecipesPage() {
  const navigate = useNavigate();
  const { setStatus } = useAppState();

  const recipesQuery = useRecipes();
  const adHocDrafts = useAdHocDrafts();
  const locationsQuery = useLocations();
  const recipes = recipesQuery.data ?? [];
  const [recipeSort, setRecipeSort] = useState<SortState>(null);
  const onRecipeSort = (key: string) => setRecipeSort((cur) => toggleSort(cur, key));
  const locations = locationsQuery.data ?? [];

  // Defaults to every location: a household's ingredients for one meal are
  // rarely all in the same place, so scoping to one makes everything look
  // unmakeable.
  const [scopeLocationId, setScopeLocationId] = useState<string>(ALL_LOCATIONS);
  const effectiveLocationId = scopeLocationId === ALL_LOCATIONS ? null : scopeLocationId;

  const suggestionsQuery = useRecipeSuggestions(effectiveLocationId);
  const suggestions: RecipeSuggestion[] = suggestionsQuery.data ?? [];

  const [importText, setImportText] = useState("");
  const [pendingDelete, setPendingDelete] = useState<Recipe | null>(null);
  const [availabilityRecipe, setAvailabilityRecipe] = useState<Recipe | null>(null);

  const parseText = useParseRecipeText();
  const parseImage = useParseRecipeImage();
  const deleteRecipe = useDeleteRecipe();
  const cookRecipe = useCookRecipe();

  const busy =
    parseText.isPending || parseImage.isPending || deleteRecipe.isPending || cookRecipe.isPending;

  const locationOptions = useMemo(
    () => [
      { value: ALL_LOCATIONS, label: "All locations" },
      ...locations.map((location) => ({ value: location.id, label: location.name })),
    ],
    [locations],
  );

  /**
   * Hand the parsed draft to the real editor rather than editing it inline.
   * The editor can add, remove and reorder ingredients — which is what you need
   * when OCR turns a method step into an ingredient.
   */
  const openDraftInEditor = (draft: RecipeParseApiResponse) => {
    navigate(ROUTES.recipesNew, { state: { draft } });
  };

  const handleParseText = async () => {
    try {
      const draft = await parseText.mutateAsync(importText);
      setStatus(`Parsed ${draft.ingredients.length} ingredients — review before saving`);
      openDraftInEditor({ ...draft, source: "text" });
    } catch (error) {
      setStatus(getApiErrorMessage(error, "Could not read that recipe text"));
    }
  };

  const handleParseFile = async (file: File | null) => {
    if (!file) return;
    try {
      const draft = await parseImage.mutateAsync(file);
      setStatus(`Parsed ${draft.ingredients.length} ingredients — review before saving`);
      openDraftInEditor({ ...draft, source: "image", source_filename: file.name });
    } catch (error) {
      setStatus(getApiErrorMessage(error, "Could not read that file"));
    }
  };

  const handleCook = async (recipe: Recipe) => {
    try {
      await cookRecipe.mutateAsync({
        recipeId: recipe.id,
        locationId: effectiveLocationId,
        servings: 1,
        leftoverServings: 0,
        note: null,
      });
      setStatus(
        effectiveLocationId
          ? `Cooked ${recipe.name}`
          : `Cooked ${recipe.name} — ingredients taken first-expiring-first`,
      );
    } catch (error) {
      setStatus(getApiErrorMessage(error, "Could not cook that recipe"));
    }
  };

  const handleDelete = async () => {
    if (!pendingDelete) return;
    try {
      await deleteRecipe.mutateAsync(pendingDelete.id);
      setStatus(`Deleted ${pendingDelete.name}`);
    } catch (error) {
      setStatus(getApiErrorMessage(error, "Could not delete that recipe"));
    } finally {
      setPendingDelete(null);
    }
  };

  const scoreFor = (recipeId: string) =>
    suggestions.find((row) => row.recipe_id === recipeId)?.available_score ?? null;

  const missingFor = (recipeId: string) =>
    suggestions.find((row) => row.recipe_id === recipeId)?.missing_items ?? [];

  // Sorting works on the DERIVED values (match score, missing count), not just
  // the raw record — "what can I actually cook tonight" is the useful ordering
  // and it lives in the suggestions, not on the recipe row.
  const sortedRecipes = useMemo(
    () =>
      applySort(recipes, recipeSort, {
        name: (r) => r.name.toLowerCase(),
        ingredient_count: (r) => r.ingredient_count ?? 0,
        score: (r) => scoreFor(r.id),
        missing: (r) => missingFor(r.id).length,
      }),
    // scoreFor/missingFor close over `suggestions`, so that's the real dep.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [recipes, suggestions, recipeSort],
  );

  // Five columns can't fit a phone, and this page was called out as especially
  // cramped — stack each recipe into a card instead. 62em, not the usual 48em
  // mobile cutoff: with the 240px navbar the content area between those two
  // widths is ~500px, which can't hold the table's fixed columns without
  // clipping or a sideways scroll — the cards read better than either.
  // matchMedia is falsy under test, so tests still exercise the table.
  const isMobile = useMediaQuery("(max-width: 62em)");

  const scoreBadge = (score: number | null) =>
    score === null ? (
      <Text size="sm" c="dimmed">
        —
      </Text>
    ) : (
      <Badge color={score === 1 ? "green" : score >= 0.5 ? "yellow" : "gray"}>{(score * 100).toFixed(0)}%</Badge>
    );

  const recipeActions = (recipe: Recipe, count: number) => (
    <Group gap="xs" wrap="nowrap">
      <Tooltip label="Check ingredients / match to stock">
        <ActionIcon
          variant="subtle"
          aria-label={`Check ingredients for ${recipe.name}`}
          disabled={count === 0}
          onClick={() => setAvailabilityRecipe(recipe)}
        >
          <ListChecks size={16} />
        </ActionIcon>
      </Tooltip>
      <Tooltip label="Edit">
        <ActionIcon
          variant="subtle"
          aria-label={`Edit ${recipe.name}`}
          onClick={() => navigate(`/recipes/${recipe.id}/edit`)}
        >
          <Pencil size={16} />
        </ActionIcon>
      </Tooltip>
      <Tooltip label={count === 0 ? "Add ingredients first" : "Cook now (log it as-is)"}>
        <ActionIcon
          variant="subtle"
          aria-label={`Cook ${recipe.name}`}
          disabled={busy || count === 0}
          onClick={() => handleCook(recipe)}
        >
          <ChefHat size={16} />
        </ActionIcon>
      </Tooltip>
      <Tooltip label={count === 0 ? "Add ingredients first" : "Cook along — scale, adjust, step through"}>
        <ActionIcon
          variant="subtle"
          aria-label={`Cook along with ${recipe.name}`}
          disabled={count === 0}
          onClick={() => navigate(`/recipes/${recipe.id}/cook`)}
        >
          <PlayCircle size={16} />
        </ActionIcon>
      </Tooltip>
      <Tooltip label="Delete">
        <ActionIcon
          variant="subtle"
          color="red"
          aria-label={`Delete ${recipe.name}`}
          disabled={busy}
          onClick={() => setPendingDelete(recipe)}
        >
          <Trash2 size={16} />
        </ActionIcon>
      </Tooltip>
    </Group>
  );

  return (
    <Stack>
      {/* Sits above the list: it explains why recipes look short of
          ingredients you actually own, and one answer fixes all of them. */}
      <IngredientMatchBanner />

      <Group justify="space-between" align="center" wrap="wrap">
        <Title order={2}>Recipes</Title>
        <Group wrap="wrap">
          <Select
            data={locationOptions}
            value={scopeLocationId}
            onChange={(value) => setScopeLocationId(value ?? ALL_LOCATIONS)}
            allowDeselect={false}
            aria-label="Match ingredients against"
            w={isMobile ? "100%" : 220}
          />
          <Button variant="default" onClick={() => navigate(ROUTES.cook)}>
            Cook something
          </Button>
          <Button onClick={() => navigate(ROUTES.recipesNew)}>New Recipe</Button>
        </Group>
      </Group>

      {(adHocDrafts.data ?? []).length > 0 && (
        <Alert color="blue" variant="light">
          <Group justify="space-between" wrap="wrap">
            <Text size="sm">
              You have {(adHocDrafts.data ?? []).length === 1 ? "a cook" : "cooks"} in progress —
              nothing has been deducted yet.
            </Text>
            <Button size="xs" onClick={() => navigate(ROUTES.cook)}>
              Resume
            </Button>
          </Group>
        </Alert>
      )}

      <Card withBorder>
        <Title order={3} mb="xs">
          Your Recipes
        </Title>

        {recipesQuery.isLoading ? (
          <Text size="sm" c="dimmed">
            Loading recipes…
          </Text>
        ) : recipes.length === 0 ? (
          <Text size="sm" c="dimmed">
            No recipes yet. Add one by hand, or import from a photo, a PDF or pasted text below.
          </Text>
        ) : (
          isMobile ? (
            <Stack gap="xs">
              {recipes.map((recipe) => {
                const score = scoreFor(recipe.id);
                const missing = missingFor(recipe.id);
                const count = recipe.ingredient_count ?? 0;
                return (
                  <Card
                    key={recipe.id}
                    withBorder
                    padding="sm"
                    style={{ cursor: "pointer" }}
                    onClick={() => navigate(`/recipes/${recipe.id}`)}
                  >
                    <Group justify="space-between" wrap="nowrap" align="flex-start">
                      {recipe.image_src && (
                        <Image
                          src={recipe.image_src}
                          alt=""
                          w={56}
                          h={56}
                          fit="cover"
                          radius="sm"
                          style={{ flexShrink: 0 }}
                        />
                      )}
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <Text fw={600} lineClamp={2}>
                          {recipe.name}
                        </Text>
                        <Text size="xs" c="dimmed">
                          {count === 0 ? "no ingredients yet" : `${count} ingredient${count === 1 ? "" : "s"}`}
                        </Text>
                      </div>
                      {scoreBadge(score)}
                    </Group>
                    <Text size="sm" mt={6} c={missing.length ? undefined : "dimmed"}>
                      Missing: {missing.length ? missing.join(", ") : "nothing"}
                    </Text>
                    <Group mt="sm" onClick={(e) => e.stopPropagation()}>
                      {recipeActions(recipe, count)}
                    </Group>
                  </Card>
                );
              })}
            </Stack>
          ) : (
            /* No ScrollContainer: the table fits the card at every width the
               table branch renders at (narrower falls back to the stacked
               cards above), so a sideways scroll would only ever hide the
               Actions column. Fixed layout so the sized columns hold; Recipe
               takes the remainder and Missing is capped at a slice of the
               width — both truncate with the full text in the tooltip. */
            <Table striped highlightOnHover withTableBorder style={{ tableLayout: "fixed" }}>
              <Table.Thead>
                <Table.Tr>
                  <SortableTh label="Recipe" sortKey="name" sort={recipeSort} onSort={onRecipeSort} />
                  <SortableTh
                    label="Ingredients"
                    sortKey="ingredient_count"
                    sort={recipeSort}
                    onSort={onRecipeSort}
                    w={100}
                  />
                  <SortableTh label="Can make now" sortKey="score" sort={recipeSort} onSort={onRecipeSort} w={120} />
                  <SortableTh label="Missing" sortKey="missing" sort={recipeSort} onSort={onRecipeSort} w="18%" />
                  {/* 5 ActionIcons (28px) + 4 xs gaps (8px) + md cell padding (16px
                      each side) need ~204px; 170px was sized for 4 icons and clipped
                      the 5th ("Cook along") past the Card's overflow:hidden edge. */}
                  <Table.Th w={210} style={{ whiteSpace: "nowrap" }}>
                    Actions
                  </Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {sortedRecipes.map((recipe) => {
                  const score = scoreFor(recipe.id);
                  const missing = missingFor(recipe.id);
                  const count = recipe.ingredient_count ?? 0;
                  return (
                    <Table.Tr
                      key={recipe.id}
                      style={{ cursor: "pointer" }}
                      onClick={() => navigate(`/recipes/${recipe.id}`)}
                    >
                      <Table.Td>
                        <Group gap="xs" wrap="nowrap">
                          {recipe.image_src && (
                            <Image
                              src={recipe.image_src}
                              alt=""
                              w={36}
                              h={36}
                              fit="cover"
                              radius="sm"
                              style={{ flexShrink: 0 }}
                            />
                          )}
                          <Text size="sm" truncate title={recipe.name}>
                            {recipe.name}
                          </Text>
                        </Group>
                      </Table.Td>
                      <Table.Td>
                        {count === 0 ? (
                          <Badge color="gray" variant="light">
                            none yet
                          </Badge>
                        ) : (
                          count
                        )}
                      </Table.Td>
                      <Table.Td>{scoreBadge(score)}</Table.Td>
                      <Table.Td>
                        <Text
                          size="sm"
                          truncate
                          c={missing.length ? undefined : "dimmed"}
                          title={missing.length ? missing.join(", ") : undefined}
                        >
                          {missing.length ? missing.join(", ") : "nothing"}
                        </Text>
                      </Table.Td>
                      <Table.Td onClick={(e) => e.stopPropagation()}>{recipeActions(recipe, count)}</Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          )
        )}

        {effectiveLocationId === null && recipes.length > 0 && (
          <Text size="xs" c="dimmed" mt="sm">
            Matching against stock in every location. Cooking takes what expires soonest first.
          </Text>
        )}
      </Card>

      <Card withBorder>
        <Title order={3} mb="xs">
          Import a Recipe
        </Title>
        <Text size="sm" c="dimmed" mb="sm">
          Photo, PDF or pasted text. You get to review and correct everything before it saves.
        </Text>

        <Group align="flex-start" grow>
          <Stack gap="sm">
            <FileButton onChange={handleParseFile} accept="image/*,application/pdf">
              {(props) => (
                <Button {...props} variant="default" loading={parseImage.isPending} disabled={busy}>
                  Choose photo or PDF
                </Button>
              )}
            </FileButton>
            <Text size="xs" c="dimmed">
              On a phone this offers the camera. PDFs with selectable text read best; scans fall
              back to OCR.
            </Text>
          </Stack>

          <Stack gap="sm">
            <Textarea
              label="Or paste recipe text"
              value={importText}
              onChange={(event) => setImportText(event.currentTarget.value)}
              minRows={5}
              placeholder={"Ingredients:\n1 cup flour\n2 eggs\n\nSteps:\n1. Mix and bake."}
              disabled={busy}
            />
            <Button
              onClick={handleParseText}
              disabled={busy || !importText.trim()}
              loading={parseText.isPending}
            >
              Parse Text
            </Button>
          </Stack>
        </Group>
      </Card>

      <RecipeImportSearch />

      <RecipeAvailabilityModal
        recipe={availabilityRecipe}
        locationId={effectiveLocationId}
        onClose={() => setAvailabilityRecipe(null)}
      />

      <Modal
        opened={pendingDelete !== null}
        onClose={() => setPendingDelete(null)}
        title="Delete recipe?"
      >
        <Stack>
          <Alert color="red" variant="light">
            <b>{pendingDelete?.name}</b> and its ingredient list will be removed. Inventory you have
            already cooked is not affected. This cannot be undone.
          </Alert>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setPendingDelete(null)}>
              Cancel
            </Button>
            <Button color="red" loading={deleteRecipe.isPending} onClick={handleDelete}>
              Delete
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
