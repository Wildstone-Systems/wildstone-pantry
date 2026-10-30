import { Suspense, type ReactNode } from "react";

/** Wrap a lazily-loaded route element in a Suspense boundary. */
export function withSuspense(element: ReactNode): ReactNode {
  return <Suspense fallback={<div>Loading page...</div>}>{element}</Suspense>;
}
