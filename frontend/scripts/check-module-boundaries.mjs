#!/usr/bin/env node
/**
 * Module-boundary check (mirrors the backend's one-owner rule).
 *
 * A feature module may use another module ONLY through its public barrel
 * (`@/modules/<other>`). Deep imports into another module's internals
 * (`@/modules/<other>/pages|hooks|routes|components/...`) are forbidden —
 * they couple modules to each other's implementation.
 *
 * Same-module imports and barrel imports are allowed.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const modulesDir = join(root, "src", "modules");

const IMPORT_RE = /(?:import|export)[^"']*?from\s*["']([^"']+)["']/g;
const DEEP_RE = /^@\/modules\/([^/]+)\/.+/;

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (/\.(ts|tsx)$/.test(entry)) out.push(full);
  }
  return out;
}

const violations = [];
for (const file of walk(modulesDir)) {
  const rel = relative(modulesDir, file);
  const currentModule = rel.split(/[/\\]/)[0];
  const src = readFileSync(file, "utf8");
  let m;
  while ((m = IMPORT_RE.exec(src)) !== null) {
    const spec = m[1];
    const deep = DEEP_RE.exec(spec);
    if (deep && deep[1] !== currentModule) {
      violations.push({ file: relative(root, file), spec, other: deep[1] });
    }
  }
}

if (violations.length) {
  console.error("Module boundary violations (use the module's barrel `@/modules/<name>`):");
  for (const v of violations) {
    console.error(`  - ${v.file}\n      imports "${v.spec}" (reach into '${v.other}' internals)`);
  }
  process.exit(1);
}

console.log("module boundaries OK");
