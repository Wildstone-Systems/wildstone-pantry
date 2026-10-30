import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { HouseholdProfile } from "@/api/types";

// The profiles LIST hook is shared (open, used by the kiosk shell too):
export { useHouseholdProfiles } from "@/api/hooks";

const PROFILES_KEY = ["household", "profiles"] as const;

export type CreateProfileInput = {
  display_name: string;
  daily_calorie_target?: number | null;
  account_id?: string | null;
};

export function useCreateProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: CreateProfileInput) => {
      const res = await api.post<HouseholdProfile>("/api/household/profiles", input);
      return res.data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: PROFILES_KEY }),
  });
}

export type UpdateProfileInput = {
  id: string;
  display_name?: string;
  daily_calorie_target?: number | null;
  is_active?: boolean;
};

export function useUpdateProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...patch }: UpdateProfileInput) => {
      const res = await api.patch<HouseholdProfile>(`/api/household/profiles/${id}`, patch);
      return res.data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: PROFILES_KEY }),
  });
}

export function useSetProfilePin() {
  const queryClient = useQueryClient();
  return useMutation({
    // pin=null clears it.
    mutationFn: async ({ id, pin }: { id: string; pin: string | null }) => {
      const res = await api.put<HouseholdProfile>(`/api/household/profiles/${id}/pin`, { pin });
      return res.data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: PROFILES_KEY }),
  });
}
