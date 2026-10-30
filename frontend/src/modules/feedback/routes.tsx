import { lazy } from "react";

import type { ModuleManifest } from "@/core/registry";
import { withSuspense } from "@/core/lazyRoute";
import { ROUTES } from "@/routes/paths";

const FeedbackPage = lazy(() => import("./pages/FeedbackPage").then((m) => ({ default: m.FeedbackPage })));

export const feedbackManifest: ModuleManifest = {
  id: "feedback",
  routes: [{ path: ROUTES.feedback, element: withSuspense(<FeedbackPage />) }],
};
