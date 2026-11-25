import { createPinia } from "pinia";
import { page } from "vitest/browser";
import { render } from "vitest-browser-vue";
import { describe, expect, it } from "vitest";
import { useConnectionStore } from "../stores/connection";
import ConnectionBadge from "./ConnectionBadge.vue";

describe("ConnectionBadge", () => {
  it("shows the connection status in words", async () => {
    const pinia = createPinia();
    await render(ConnectionBadge, { global: { plugins: [pinia] } });

    useConnectionStore(pinia).set("live");

    await expect.element(page.getByTestId("connection-badge")).toHaveTextContent("live");
  });

  it("shows a Reconnecting banner while the socket is down", async () => {
    const pinia = createPinia();
    await render(ConnectionBadge, { global: { plugins: [pinia] } });

    useConnectionStore(pinia).set("reconnecting");

    await expect.element(page.getByTestId("connection-reconnecting")).toHaveTextContent("Reconnecting");
  });
});
