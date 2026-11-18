import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";

import { api } from "./client";

/**
 * Resolve an image URL so it works when logins are required.
 *
 * A plain `<img src="/api/...">` cannot send the Authorization header, so our
 * own API images are fetched through the authenticated client and shown as a
 * local blob URL. External URLs (an imported recipe's picture) pass through
 * unchanged. Returns `undefined` while loading or on error, so callers show
 * their placeholder.
 */
export function useAuthedImageSrc(src: string | null | undefined): string | undefined {
  const isApiImage = Boolean(src && src.startsWith("/api/"));

  const { data: blob } = useQuery({
    queryKey: ["authed-image", src],
    queryFn: async () => (await api.get<Blob>(src as string, { responseType: "blob" })).data,
    enabled: isApiImage,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  const objectUrl = useMemo(() => (blob instanceof Blob ? URL.createObjectURL(blob) : undefined), [blob]);
  useEffect(() => () => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
  }, [objectUrl]);

  if (!src) return undefined;
  return isApiImage ? objectUrl : src;
}
