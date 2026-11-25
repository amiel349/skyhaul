// Fake dbs register their reset() here; the browser setup runs them all after each test.
// A separate module so a component's fakes never import the setup file (and its hooks).
const resets: Array<() => void> = [];

export function registerReset(fn: () => void): void {
  resets.push(fn);
}

export function runResets(): void {
  for (const fn of resets) fn();
}
