import type { RouteObject } from "react-router-dom";

/**
 * Frontend module manifest.
 *
 * Mirrors the backend kernel: each feature module declares the routes it
 * contributes, so adding a module never means editing the central route table.
 * - `routes` are rendered as children of the authenticated AppLayout shell
 *   (inside the app layout).
 * - `publicRoutes` are rendered outside the guard/layout (e.g. activation).
 */
export type ModuleManifest = {
  id: string;
  routes?: RouteObject[];
  publicRoutes?: RouteObject[];
};

const registry: ModuleManifest[] = [];

export function registerModule(manifest: ModuleManifest): ModuleManifest {
  if (registry.some((m) => m.id === manifest.id)) {
    throw new Error(`Module "${manifest.id}" is already registered`);
  }
  registry.push(manifest);
  return manifest;
}

export function registeredModules(): ModuleManifest[] {
  return registry;
}

/** Routes mounted inside the authenticated AppLayout shell. */
export function moduleAppRoutes(): RouteObject[] {
  return registry.flatMap((m) => m.routes ?? []);
}

/** Routes mounted outside the app layout. */
export function modulePublicRoutes(): RouteObject[] {
  return registry.flatMap((m) => m.publicRoutes ?? []);
}
