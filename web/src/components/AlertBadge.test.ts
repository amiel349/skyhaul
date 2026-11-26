import { page } from "vitest/browser";
import { describe, expect, it } from "vitest";
import { alertFaketory } from "../../tests/faketories";
import { renderComponent } from "../../tests/render";
import { useAlertsStore } from "../stores/alerts";
import AlertBadge from "./AlertBadge.vue";

describe("AlertBadge", () => {
  it("announces the unread count and updates it live", async () => {
    const { pinia } = await renderComponent(AlertBadge);
    const alerts = useAlertsStore(pinia);
    alerts.applyUpdate(alertFaketory.build({ id: "a1" }));
    alerts.applyUpdate(alertFaketory.build({ id: "a2" }));
    await expect.element(page.getByRole("status", { name: "2 unread alerts" })).toBeInTheDocument();

    alerts.applyUpdate(alertFaketory.build({ id: "a3" }));
    alerts.applyUpdate(alertFaketory.build({ id: "a1", acknowledged_by: "u1", acknowledged_at: "2026-01-01T00:05:00Z" }));

    await expect.element(page.getByRole("status", { name: "2 unread alerts" })).toBeInTheDocument();
    alerts.applyUpdate(alertFaketory.build({ id: "a2", cleared_at: "2026-01-01T00:06:00Z" }));
    await expect.element(page.getByRole("status", { name: "1 unread alert" })).toBeInTheDocument();
  });
});
