// Hooks
export { useInventory, useItemLocations, useTransactions } from "./hooks/inventory-hooks";
export type { InventoryLine, LocationCandidate, TransactionLine } from "./hooks/inventory-hooks";
// Household-wide (null = all locations, pooled) insight hooks, used by Insights.
export { useScopedExpiring, useScopedRestock, useScopedTrends } from "./hooks/scoped-hooks";

// Pages
export { InventoryPage } from "./pages/InventoryPage";
export { DashboardPage } from "./pages/DashboardPage";
export { AddLocationPage } from "./pages/AddLocationPage";
