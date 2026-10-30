import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

// Wildstone Home Lab connection (ADR 0026). All real-admin-only on the server.
const BASE = "/api/integrations/wildstone";

export type WildstoneStatus = {
  connected: boolean;
  /** When a pairing last completed; null if never (or connected by hand). */
  paired_at: string | null;
  keystone_url: string;
  publisher_token_set: boolean;
  /** What pairing tells Wildstone about where people open PantryOS (PANTRYOS_PUBLIC_URL). */
  web_url: string | null;
  /** Why the configured address isn't sent; null when it is, or when unset. */
  web_url_problem: string | null;
  relay: {
    ledger_seq: number | null;
    pending: number;
    delivered: number;
    rejected: number;
    last_delivered_at: string | null;
    last_error: string | null;
  };
};

export type PairingCode = { code: string; expires_in: number };

export type MemberLink = { profile_id: string; display_name: string; member: string | null };

const statusKey = ["admin", "wildstone", "connection"] as const;
const membersKey = ["admin", "wildstone", "members"] as const;

/** Connection status. `pollMs` > 0 re-reads it — used while a pairing code is
 * on screen, so the card notices the moment Wildstone completes the pairing. */
export function useWildstoneStatus(pollMs = 0) {
  return useQuery({
    queryKey: statusKey,
    queryFn: async () => (await api.get<WildstoneStatus>(`${BASE}/connection`)).data,
    refetchInterval: pollMs > 0 ? pollMs : false,
  });
}

export function useCreatePairingCode() {
  return useMutation({
    mutationFn: async () => (await api.post<PairingCode>(`${BASE}/pairing`)).data,
  });
}

export function useTestWildstone() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => (await api.post<{ ok: boolean; status: number }>(`${BASE}/connection/test`)).data,
    onSettled: () => qc.invalidateQueries({ queryKey: statusKey }),
  });
}

export function useDisconnectWildstone() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => (await api.delete<WildstoneStatus>(`${BASE}/connection`)).data,
    onSuccess: (data) => qc.setQueryData(statusKey, data),
  });
}

export function useMemberLinks(enabled: boolean) {
  return useQuery({
    queryKey: membersKey,
    queryFn: async () => (await api.get<MemberLink[]>(`${BASE}/members`)).data,
    enabled,
  });
}

export function useSetMemberLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ profileId, member }: { profileId: string; member: string }) =>
      (await api.put<MemberLink>(`${BASE}/members/${profileId}`, { member: member.trim() || null })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: membersKey }),
  });
}
