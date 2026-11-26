import { page } from "vitest/browser";
import { describe, expect, it } from "vitest";
import { useConnectionStore } from "../stores/connection";
import ConnectionBadge from "./ConnectionBadge.vue";
import { renderComponent } from "../../tests/render";

describe("ConnectionBadge", () => {
  it("shows the connection status in words", async () => {
    const { pinia } = await renderComponent(ConnectionBadge);

    useConnectionStore(pinia).set("live");

    await expect.element(page.getByText("live", { exact: true })).toBeInTheDocument();
  });

  it("shows a Reconnecting banner while the socket is down", async () => {
    const { pinia } = await renderComponent(ConnectionBadge);

    useConnectionStore(pinia).set("reconnecting");

    await expect.element(page.getByRole("status")).toHaveTextContent("Reconnecting");
  });
});
