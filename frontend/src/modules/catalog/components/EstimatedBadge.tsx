import { Badge, Tooltip } from "@mantine/core";

type Props = {
  estimated: boolean | undefined;
  source: string | null | undefined;
  size?: "xs" | "sm";
};

/** Marks a value nobody read off a package.
 *
 * The provenance was already being stored — it is the only reason twenty bad
 * nutrition matches could be found and undone. But it lived inside an edit
 * modal, so an item silently carrying the macros of "DENNY'S, onion rings"
 * looked exactly like one carrying its own label data. A value you cannot tell
 * apart from a measured one is a value nobody will ever question.
 */
export function EstimatedBadge({ estimated, source, size = "xs" }: Props) {
  if (!estimated) return null;
  return (
    <Tooltip
      multiline
      w={260}
      withArrow
      events={{ hover: true, focus: true, touch: true }}
      label={
        source
          ? `Estimated from "${source}" — not read off this product's label. Check it if the numbers look wrong.`
          : "Estimated, not read off this product's label."
      }
    >
      <Badge size={size} variant="light" color="yellow" style={{ cursor: "help" }} tabIndex={0}>
        est ⓘ
      </Badge>
    </Tooltip>
  );
}
