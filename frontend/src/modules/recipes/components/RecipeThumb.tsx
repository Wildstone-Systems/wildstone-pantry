import { Box } from "@mantine/core";
import { ChefHat } from "lucide-react";
import { useState, type CSSProperties } from "react";

type Props = {
  src: string | null | undefined;
  /** Width; defaults to filling the parent. */
  w?: number | string;
  h: number | string;
  radius?: number;
  style?: CSSProperties;
};

/** A recipe's picture at card/list size, cropped to fill its box.
 *
 * Falls back to the warm `pantry-photo` placeholder when the recipe has no
 * picture *or* the picture fails to load (an imported URL can go stale), so a
 * row of cards stays the same shape either way. Decorative (`alt=""`): the
 * recipe name is always rendered next to it.
 */
export function RecipeThumb({ src, w = "100%", h, radius = 0, style }: Props) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const showImage = Boolean(src) && failedSrc !== src;

  return (
    <Box
      className={showImage ? undefined : "pantry-photo"}
      data-testid="recipe-thumb"
      style={{ width: w, height: h, borderRadius: radius, overflow: "hidden", flexShrink: 0, ...style }}
    >
      {showImage ? (
        <img
          src={src ?? undefined}
          alt=""
          loading="lazy"
          onError={() => setFailedSrc(src ?? null)}
          style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
        />
      ) : (
        <ChefHat size={typeof h === "number" ? Math.max(14, Math.min(32, h / 3)) : 24} aria-hidden />
      )}
    </Box>
  );
}
