// Fails when a .vue file under src/ has no sibling test file (Foo.vue needs Foo.test.ts).
// No dependencies: runs with plain Node. Run from web/ as `pnpm check:tests`.
import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

/** Every .vue file under `dir`, as paths relative to `root`. */
export function listVueFiles(root, dir = "src") {
  const found = [];
  for (const entry of readdirSync(join(root, dir), { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) found.push(...listVueFiles(root, path));
    else if (entry.name.endsWith(".vue")) found.push(path);
  }
  return found.sort();
}

/** The sibling test path a .vue file must have. */
export function testFileFor(vueFile) {
  return vueFile.replace(/\.vue$/, ".test.ts");
}

/** The .vue files whose sibling test file does not exist (`exists` is injected for tests). */
export function missingTests(vueFiles, exists) {
  return vueFiles.filter((file) => !exists(testFileFor(file)));
}

function main() {
  const root = fileURLToPath(new URL("..", import.meta.url));
  const missing = missingTests(listVueFiles(root), (file) => existsSync(join(root, file)));
  if (missing.length === 0) return 0;
  console.error("Component test missing for:");
  for (const file of missing) console.error(`  ${file}  (expected ${testFileFor(file)})`);
  return 1;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) process.exitCode = main();
