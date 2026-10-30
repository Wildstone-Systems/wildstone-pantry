import { Alert, Badge, Button, Card, Group, Loader, Stack, Table, Text, Title } from "@mantine/core";
import { useState } from "react";
import { RecipeAvailabilityModal } from "../components/RecipeAvailabilityModal";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { RecipeImportReceipt } from "../components/RecipeImportReceipt";
import { ROUTES } from "@/routes/paths";
import { useAppState } from "@/state/AppStateProvider";

import { IngredientStatusBadge } from "../components/IngredientStatusBadge";
import { RecipePhoto } from "../components/RecipePhoto";
import type { RecipeWithIngredients } from "../hooks/recipes-hooks";
import { useCookRecipe, useRecipeAvailability } from "../hooks/recipes-hooks";

/** Full read-only view of one recipe — instructions and ingredients laid out
 * to actually be read while cooking, not just a row of action icons. */
export function RecipeViewPage() {
  const { recipeId } = useParams();
  const navigate = useNavigate();
  const { setStatus } = useAppState();

  const recipeQuery = useQuery({
    queryKey: ["recipe", recipeId],
    queryFn: async () => (await api.get<RecipeWithIngredients>(`/api/recipes/${recipeId}`)).data,
    enabled: Boolean(recipeId),
  });

  const cookRecipe = useCookRecipe();
  const [reviewOpen, setReviewOpen] = useState(false);
  // Pooled across every location — "do I have this anywhere" is the question
  // being asked while reading a recipe.
  const availability = useRecipeAvailability(recipeId ?? null, null);
  const statusByIngredient = new Map((availability.data ?? []).map((row) => [row.ingredient_id, row]));

  const handleCook = async () => {
    if (!recipeId) return;
    try {
      await cookRecipe.mutateAsync({ recipeId, locationId: null, servings: 1, leftoverServings: 0, note: null });
      setStatus(`Cooked ${recipeQuery.data?.recipe.name ?? "recipe"} — ingredients taken first-expiring-first`);
    } catch (err) {
      setStatus(getApiErrorMessage(err, "Could not cook that recipe"));
    }
  };

  if (recipeQuery.isLoading) {
    return (
      <Group justify="center" py="xl">
        <Loader />
      </Group>

    );
  }

  if (recipeQuery.isError || !recipeQuery.data) {
    return (
      <Stack>
        <Alert color="red" title="Couldn't load that recipe">
          {getApiErrorMessage(recipeQuery.error, "It may have been deleted.")}
        </Alert>
        <Button component={Link} to={ROUTES.recipes} variant="default">
          Back to Recipes
        </Button>
      </Stack>
    );
  }

  const { recipe, ingredients } = recipeQuery.data;

  return (
    <Stack>
      <Group justify="space-between" align="flex-start" wrap="wrap">
        <div>
          <Title order={2}>{recipe.name}</Title>
          <Group gap="xs" mt={4}>
            <Badge variant="light">
              {recipe.servings} serving{recipe.servings === 1 ? "" : "s"}
            </Badge>
            {recipe.external_source && (
              <Badge variant="outline" color="grape">
                imported from {recipe.external_source}
              </Badge>
            )}
          </Group>
        </div>
        <Group>
          <Button variant="default" onClick={() => navigate(ROUTES.recipes)}>
            Back
          </Button>
          <Button variant="subtle" onClick={handleCook} loading={cookRecipe.isPending}>
            Cook now
          </Button>
          <Button variant="light" onClick={() => navigate(`/recipes/${recipeId}/cook`)}>
            Cook along
          </Button>
          <Button onClick={() => navigate(`/recipes/${recipeId}/edit`)}>Edit</Button>
        </Group>
      </Group>

      <RecipeImportReceipt recipe={recipe} />

      {/* Above the instructions: what you're aiming for is worth seeing before
          the wall of steps, and the empty state is where "add a photo" lives. */}
      <RecipePhoto
        recipeId={recipe.id}
        recipeName={recipe.name}
        imageSrc={recipe.image_src}
        hasUpload={Boolean(recipe.image_src?.startsWith("/api/recipes/"))}
      />

      <Card withBorder>
        <Title order={4} mb="sm">
          Instructions
        </Title>
        {recipe.instructions ? (
          <Text style={{ whiteSpace: "pre-wrap" }}>{recipe.instructions}</Text>
        ) : (
          <Text c="dimmed">No instructions yet — add some from Edit.</Text>
        )}
      </Card>

      <Card withBorder>
        <Title order={4} mb="sm">
          Ingredients
        </Title>
        <Button variant="light" mb="sm" onClick={() => setReviewOpen(true)}>
          Review ingredient matches
        </Button>
        {ingredients.length === 0 ? (
          <Text c="dimmed">No ingredients yet — add some from Edit.</Text>
        ) : (
          <Table.ScrollContainer minWidth={400}>
            <Table>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Item</Table.Th>
                  <Table.Th w={110}>Have?</Table.Th>
                  <Table.Th w={110}>Quantity</Table.Th>
                  <Table.Th w={90}>Unit</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {ingredients.map((ing) => (
                  <Table.Tr key={ing.ingredient_id ?? ing.item_id}>
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        <Text>{ing.item_name ?? ing.item_id}</Text>
                        {ing.optional && (
                          <Badge size="xs" variant="light" color="gray">
                            optional
                          </Badge>
                        )}
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      {/* ingredient_id is optional on the read type, so guard
                          rather than widen the Map key to string|undefined. */}
                      <IngredientStatusBadge
                        status={ing.ingredient_id ? statusByIngredient.get(ing.ingredient_id)?.status : undefined}
                        onHand={ing.ingredient_id ? statusByIngredient.get(ing.ingredient_id)?.on_hand : undefined}
                      />
                    </Table.Td>
                    <Table.Td>{Number(ing.quantity_required)}</Table.Td>
                    <Table.Td>{ing.unit}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>

      <RecipeAvailabilityModal
        recipe={reviewOpen ? recipe : null}
        locationId={null}
        onClose={() => setReviewOpen(false)}
      />
      {recipe.external_url && (
        <Text size="xs" c="dimmed">
          Source:{" "}
          <a href={recipe.external_url} target="_blank" rel="noreferrer">
            {recipe.external_url}
          </a>
        </Text>
      )}
    </Stack>
  );
}
