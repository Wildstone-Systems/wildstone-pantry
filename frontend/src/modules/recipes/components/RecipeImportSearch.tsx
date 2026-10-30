import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Image,
  Loader,
  Modal,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useRef, useState } from "react";
import { useNavigate } from "react-router";

import { getApiErrorMessage } from "@/api/errors";
import { useAppState } from "@/state/AppStateProvider";
import { ROUTES } from "@/routes/paths";

import type { RecipeImportCandidate, RecipeSearchResult, RecipeValidationResponse } from "../hooks/recipes-hooks";
import {
  useExternalRecipeDetail,
  useSearchExternalRecipes,
  useValidateRecipe,
} from "../hooks/recipes-hooks";

/** Search a chili recipe, pick one, review it, import it — same "review before
 * it saves" contract as the photo/PDF/text import paths above it on the page,
 * just sourced from the web instead of something the user already has. */
export function RecipeImportSearch() {
  const { setStatus } = useAppState();
  const navigate = useNavigate();
  const generation = useRef(0);
  const [query, setQuery] = useState("");
  const [previewHit, setPreviewHit] = useState<RecipeSearchResult | null>(null);
  const [previewImage, setPreviewImage] = useState<string | null>(null);
  const [previewCandidate, setPreviewCandidate] = useState<RecipeImportCandidate | null>(null);
  const [validation, setValidation] = useState<RecipeValidationResponse | null>(null);

  const searchQuery = useSearchExternalRecipes(query);
  const detail = useExternalRecipeDetail();
  const validate = useValidateRecipe();

  const results = searchQuery.data?.results ?? [];
  const providersActive = searchQuery.data?.providers_active ?? ["themealdb"];
  const trimmed = query.trim();

  const openPreview = async (hit: RecipeSearchResult) => {
    const current = ++generation.current;
    setPreviewHit(hit);
    setPreviewCandidate(null);
    setValidation(null);
    try {
      const result = await detail.mutateAsync({ source: hit.source, external_id: hit.external_id });
      if (current !== generation.current) return;
      setPreviewImage(result.image_url);
      // Carry the provider's picture onto the recipe we import. It was already
      // being fetched for this preview and then thrown away, so an imported
      // recipe arrived without the photo the user just looked at.
      setPreviewCandidate({ ...result.recipe, image_url: result.image_url });
      const checked = await validate.mutateAsync(result.recipe);
      if (current === generation.current) setValidation(checked);
    } catch (err) {
      if (current !== generation.current) return;
      setStatus(getApiErrorMessage(err, "Could not load that recipe"));
      setPreviewHit(null);
    }
  };

  const closePreview = () => {
    generation.current += 1;
    setPreviewHit(null);
    setPreviewCandidate(null);
    setPreviewImage(null);
  };

  const handleImport = () => {
    if (!previewCandidate) return;
    navigate(ROUTES.recipesNew, { state: { draft: {
      title: previewCandidate.name,
      instructions: previewCandidate.instructions ?? "",
      servings: previewCandidate.servings,
      ingredients: previewCandidate.ingredients.map((ingredient, index) => ({ ...ingredient, raw_line: ingredient.name, source_line_id: `field:ingredients[${index}]` })),
      raw_text: JSON.stringify(previewCandidate),
      source: "online",
      structured_source: previewCandidate,
    } } });
  };

  const previewBusy = detail.isPending || validate.isPending;

  return (
    <Card withBorder>
      <Title order={3} mb="xs">
        Find a Recipe Online
      </Title>
      <Text size="sm" c="dimmed" mb="sm">
        Search {providersActive.includes("spoonacular") ? "TheMealDB and Spoonacular" : "TheMealDB"} by name and
        import — you'll get to review ingredients and units before anything saves.
      </Text>

      <TextInput
        placeholder="Search for a recipe, e.g. chili"
        value={query}
        onChange={(e) => setQuery(e.currentTarget.value)}
        rightSection={searchQuery.isFetching ? <Loader size="xs" /> : null}
      />

      {!providersActive.includes("spoonacular") && (
        <Text size="xs" c="dimmed" mt={4}>
          Only TheMealDB is active right now (a small, curated catalog) — set SPOONACULAR_API_KEY for a much
          bigger one.
        </Text>
      )}

      {trimmed.length >= 2 && !searchQuery.isFetching && results.length === 0 && (
        <Text size="sm" c="dimmed" mt="sm">
          No recipes found for "{trimmed}".
        </Text>
      )}

      {results.length > 0 && (
        <SimpleGrid cols={{ base: 2, sm: 3, md: 4 }} spacing="sm" mt="sm">
          {results.map((hit) => (
            <Card
              key={`${hit.source}:${hit.external_id}`}
              withBorder
              padding="xs"
              style={{ cursor: "pointer", display: "flex", flexDirection: "column" }}
              onClick={() => openPreview(hit)}
            >
              {hit.image_url ? (
                <Image
                  src={hit.image_url}
                  alt={hit.name}
                  h={140}
                  fit="contain"
                  radius="sm"
                  style={{ background: "var(--mantine-color-gray-1)" }}
                />
              ) : (
                <div style={{ height: 140, background: "var(--mantine-color-gray-1)", borderRadius: 6 }} />
              )}
              <Text size="xs" fw={600} mt={6} lineClamp={2}>
                {hit.name}
              </Text>
              {(hit.category || hit.area) && (
                <Group gap={4} mt={4}>
                  {hit.area && (
                    <Badge size="xs" variant="dot" color="teal">
                      {hit.area}
                    </Badge>
                  )}
                  {hit.category && (
                    <Badge size="xs" variant="light" color="gray">
                      {hit.category}
                    </Badge>
                  )}
                </Group>
              )}
              {(hit.servings || hit.ready_in_minutes) && (
                <Text size="xs" c="dimmed" mt={4}>
                  {[
                    hit.servings ? `${hit.servings} serving${hit.servings === 1 ? "" : "s"}` : null,
                    hit.ready_in_minutes ? `${hit.ready_in_minutes} min` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </Text>
              )}
              <Badge size="xs" variant="light" style={{ alignSelf: "flex-start", marginTop: 8 }}>
                {hit.source_label}
              </Badge>
            </Card>
          ))}
        </SimpleGrid>
      )}

      <Modal opened={previewHit !== null} onClose={closePreview} title={previewHit?.name ?? "Recipe preview"} size="lg">
        {previewBusy ? (
          <Group justify="center" py="xl">
            <Loader />
          </Group>
        ) : previewCandidate ? (
          <Stack>
            {previewImage && <Image src={previewImage} alt={previewCandidate.name} h={160} fit="cover" radius="sm" />}
            <Text size="sm" c="dimmed">
              {previewCandidate.servings} serving{previewCandidate.servings === 1 ? "" : "s"} ·{" "}
              {previewCandidate.ingredients.length} ingredients
            </Text>

            {validation?.would_upsert && (
              <Alert color="blue" variant="light">
                You've already imported this recipe — importing again updates it rather than duplicating it.
              </Alert>
            )}

            <Table.ScrollContainer minWidth={400}>
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Ingredient</Table.Th>
                    <Table.Th>Qty</Table.Th>
                    <Table.Th>Unit</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {previewCandidate.ingredients.map((ing, i) => {
                    const line = validation?.ingredients[i];
                    return (
                      <Table.Tr key={i}>
                        <Table.Td>{ing.name}</Table.Td>
                        <Table.Td>{ing.quantity}</Table.Td>
                        <Table.Td>
                          <Group gap={4} wrap="nowrap">
                            <Text size="sm">{ing.unit}</Text>
                            {line?.unit_resolution === "flagged" && (
                              <Badge size="xs" color="orange" variant="light">
                                check{line.suggestion ? ` — try ${line.suggestion}` : ""}
                              </Badge>
                            )}
                          </Group>
                        </Table.Td>
                      </Table.Tr>
                    );
                  })}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>

            {previewCandidate.instructions && (
              <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
                {previewCandidate.instructions}
              </Text>
            )}

            <Group justify="flex-end">
              <Button variant="default" onClick={closePreview}>
                Cancel
              </Button>
              <Button onClick={handleImport} color="green">
                Review in editor
              </Button>
            </Group>
          </Stack>
        ) : null}
      </Modal>
    </Card>
  );
}
