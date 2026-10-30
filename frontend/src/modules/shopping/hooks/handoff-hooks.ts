import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

const LIST_KEY = ["shopping-list"] as const;
const SESSIONS_KEY = ["handoff-sessions"] as const;

export type Retailer = {
  key: string;
  label: string;
  capabilities: string[];
  available: string[];
  status: string;
};

export type HandoffItem = {
  id: string;
  shopping_list_item_id: string | null;
  item_id: string | null;
  name: string;
  quantity: string | number;
  unit: string;
  state: string;
  external_id: string | null;
  external_name: string | null;
  external_url: string | null;
  note: string | null;
  search_url: string | null;
  search_query: string;
  suggested_location_id: string | null;
};

export type HandoffSession = {
  id: string;
  retailer_key: string;
  retailer_label: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  items: HandoffItem[];
  purchase_id?: string | null;
  purchased_count?: number | null;
};

/** Every known retailer with declared vs currently-usable capabilities (ADR 0019). */
export function useRetailers() {
  return useQuery({
    queryKey: ["retailers"],
    queryFn: async () => {
      const res = await api.get<{ retailers: Retailer[] }>("/api/retailers");
      return res.data.retailers;
    },
    staleTime: 300_000,
  });
}

/** Resumable active handoff sessions. */
export function useActiveHandoffSessions() {
  return useQuery({
    queryKey: SESSIONS_KEY,
    queryFn: async () => {
      const res = await api.get<{ sessions: HandoffSession[] }>("/api/shopping-list/handoff");
      return res.data.sessions;
    },
  });
}

export function useHandoffSession(sessionId: string | undefined) {
  return useQuery({
    queryKey: ["handoff-session", sessionId],
    enabled: !!sessionId,
    queryFn: async () => {
      const res = await api.get<HandoffSession>(`/api/shopping-list/handoff/${sessionId}`);
      return res.data;
    },
  });
}

/** Snapshot the pending buy list into a guided shopping session. */
export function useStartHandoff() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (retailer: string) => {
      const res = await api.post<HandoffSession>("/api/shopping-list/handoff", { retailer });
      return res.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: SESSIONS_KEY }),
  });
}

export function useUpdateHandoffItem(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      itemId,
      ...patch
    }: {
      itemId: string;
      state?: string;
      external_id?: string;
      external_name?: string;
      external_url?: string;
      note?: string;
    }) => {
      const res = await api.patch<HandoffItem>(`/api/shopping-list/handoff/${sessionId}/items/${itemId}`, patch);
      return res.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["handoff-session", sessionId] }),
  });
}

/** The post-shop confirmation: closes list entries, teaches the product map,
 * and records an idempotent Purchase (ADR 0019 D5). */
export function useCompleteHandoff(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      purchased_item_ids: string[];
      location_id?: string | null;
      item_locations?: Record<string, string>;
    }) => {
      const res = await api.post<HandoffSession>(`/api/shopping-list/handoff/${sessionId}/complete`, input);
      return res.data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: LIST_KEY });
      qc.invalidateQueries({ queryKey: SESSIONS_KEY });
      qc.invalidateQueries({ queryKey: ["handoff-session", sessionId] });
      qc.invalidateQueries({ queryKey: ["inventory"] });
    },
  });
}
