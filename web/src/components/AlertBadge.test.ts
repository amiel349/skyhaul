import { page } from "vitest/browser";
import { describe, expect, it } from "vitest";
import { alertFaketory } from "../../tests/faketories";
import { renderComponent } from "../../tests/render";
import { useAlertsStore } from "../stores/alerts";
import AlertBadge from "./AlertBadge.vue";

describe("AlertBadge", () => {
  it("counts the alerts in the table and the unread ones among them, and follows the filters live", async () => {
    const { pinia } = await renderComponent(AlertBadge);
    const alerts = useAlertsStore(pinia);
    alerts.applyUpdate(alertFaketory.build({ id: "a1", severity: "CRITICAL" }));
    alerts.applyUpdate(alertFaketory.build({ id: "a2" }));
    await expect.element(page.getByRole("status", { name: "2 alerts, 2 unread" })).toBeInTheDocument();

    alerts.applyUpdate(alertFaketory.build({ id: "a3" }));
    alerts.applyUpdate(alertFaketory.build({ id: "a1", severity: "CRITICAL", acknowledged_by: "u1", acknowledged_at: "2026-01-01T00:05:00Z" }));
    await expect.element(page.getByRole("status", { name: "3 alerts, 2 unread" })).toBeInTheDocument();

    alerts.applyUpdate(alertFaketory.build({ id: "a2", cleared_at: "2026-01-01T00:06:00Z" }));
    await expect.element(page.getByRole("status", { name: "2 alerts, 1 unread" })).toBeInTheDocument();

    alerts.setFilter({ severity: "CRITICAL" });
    await expect.element(page.getByRole("status", { name: "1 alert" })).toBeInTheDocument(); // acknowledged: no unread part
  });
});
