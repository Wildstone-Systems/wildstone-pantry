import { Box, Button, FileButton, Group, Image, Text } from "@mantine/core";
import { useState } from "react";

import { getApiErrorMessage } from "@/api/errors";
import { useAppState } from "@/state/AppStateProvider";

import { useDeleteRecipeImage, useUploadRecipeImage } from "../hooks/recipes-hooks";

type Props = {
  recipeId: string;
  recipeName: string;
  /** Already resolved by the API — an uploaded photo beats an imported one. */
  imageSrc: string | null | undefined;
  /** Whether this recipe has an *uploaded* photo, as opposed to an imported URL.
   *  Only an uploaded one can be removed; removing it falls back to the import. */
  hasUpload: boolean;
};

/** A recipe's picture, with the controls to set or replace it.
 *
 * The empty state is a real drop target rather than a hidden menu item: adding
 * a photo is a thing people do once per recipe and then forget about, so it has
 * to be obvious the first time and out of the way afterwards.
 */
export function RecipePhoto({ recipeId, recipeName, imageSrc, hasUpload }: Props) {
  const { setStatus } = useAppState();
  const upload = useUploadRecipeImage(recipeId);
  const remove = useDeleteRecipeImage(recipeId);
  const [hovering, setHovering] = useState(false);

  const handleFile = async (file: File | null) => {
    if (!file) return;
    try {
      await upload.mutateAsync(file);
      setStatus(`Photo added to ${recipeName}`);
    } catch (err) {
      setStatus(getApiErrorMessage(err, "Could not save that photo"));
    }
  };

  const handleRemove = async () => {
    try {
      await remove.mutateAsync();
      setStatus(`Photo removed from ${recipeName}`);
    } catch (err) {
      setStatus(getApiErrorMessage(err, "Could not remove that photo"));
    }
  };

  if (!imageSrc) {
    return (
      <FileButton onChange={handleFile} accept="image/*">
        {(props) => (
          <Button
            {...props}
            variant="default"
            loading={upload.isPending}
            leftSection={<span>📷</span>}
            size="sm"
          >
            Add a photo
          </Button>
        )}
      </FileButton>
    );
  }

  return (
    <Box
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      style={{ position: "relative", borderRadius: 8, overflow: "hidden" }}
    >
      <Image
        src={imageSrc}
        alt={recipeName}
        // `contain`, not `cover`: a recipe photo is usually framed deliberately
        // and cropping it to a fixed box cuts the dish in half.
        fit="contain"
        h={{ base: 200, sm: 260 }}
        radius="md"
        bg="var(--mantine-color-default-hover)"
      />
      {/* The controls stay out of the way until you go looking for them — on
          touch there is no hover, so they're always visible there. */}
      <Group
        gap="xs"
        style={{
          position: "absolute",
          right: 8,
          bottom: 8,
          opacity: hovering ? 1 : 0.25,
          transition: "opacity 120ms",
        }}
      >
        <FileButton onChange={handleFile} accept="image/*">
          {(props) => (
            <Button {...props} size="xs" variant="filled" color="dark" loading={upload.isPending}>
              Replace
            </Button>
          )}
        </FileButton>
        {hasUpload && (
          <Button size="xs" variant="filled" color="dark" onClick={handleRemove} loading={remove.isPending}>
            Remove
          </Button>
        )}
      </Group>
      {upload.isPending && (
        <Text size="xs" c="dimmed" mt={4}>
          Uploading…
        </Text>
      )}
    </Box>
  );
}
