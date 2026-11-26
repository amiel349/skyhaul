import { commandFaketory } from "../../tests/faketories";
import { page } from "vitest/browser";
import { describe, expect, it } from "vitest";
import type { Command } from "../api/types";
import { useCommandsStore } from "../stores/commands";
import { fakeDb } from "../../tests/fake-db";
import { renderComponent } from "../../tests/render";
import CommandList from "./CommandList.vue";

async function renderList() {
  const { pinia } = await renderComponent(CommandList, { props: { droneId: "d1" } });
  return useCommandsStore(pinia);
}

const id = (n: number) => `00000000-0000-4000-8000-00000000000${n}`;
const shown = (text: string) => page.getByText(text, { exact: true });
const escapeRegExp = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const items = () => page.getByRole("listitem");

describe("CommandList", () => {
  it("loads the history when it opens and shows it newest first", async () => {
    fakeDb.commands.add(
      commandFaketory.build({ id: id(1), type: "ARM", created_at: "2026-01-01T00:00:00Z" }),
      commandFaketory.build({ id: id(2), type: "LAND", created_at: "2026-01-01T00:05:00Z" }),
    );

    await renderList();

    await expect.element(page.getByRole("list", { name: "Commands of this drone" })).toBeInTheDocument();
    await expect.element(items().nth(1)).toBeInTheDocument();
    await expect.element(items().nth(0)).toMatchTextContent("LAND");
    await expect.element(items().nth(1)).toMatchTextContent("ARM");
    expect(items().elements()).toHaveLength(2);
    expect(fakeDb.commands.listed).toEqual(["d1"]);
  });

  it("summarises a list in the payload by its size instead of printing [object Object]", async () => {
    fakeDb.commands.add(
      commandFaketory.build({
        id: id(1),
        type: "START_MISSION",
        // The generated type is Record<string, never> (a known contract gap); the real payload has values.
        payload: {
          mission_id: "m1",
          waypoints: [
            { lat: 32.07, lon: 34.8, altitude_m: 40 },
            { lat: 32.08, lon: 34.81, altitude_m: 40 },
            { lat: 32.09, lon: 34.82, altitude_m: 40 },
          ],
        } as unknown as Command["payload"],
      }),
    );

    await renderList();

    await expect.element(page.getByText("mission_id m1, waypoints 3 items")).toBeInTheDocument();
    await expect.element(page.getByText("[object Object]")).not.toBeInTheDocument();
  });

  it("says No commands yet when the drone has none", async () => {
    await renderList();

    await expect.element(shown("No commands yet")).toBeInTheDocument();
  });

  it("changes the status of a row in place when command.updated arrives", async () => {
    fakeDb.commands.add(commandFaketory.build({ id: id(1), status: "SENT" }));
    const store = await renderList();
    await expect.element(shown("SENT")).toBeInTheDocument();

    store.applyUpdate(commandFaketory.build({ id: id(1), status: "ACKED" }));

    await expect.element(shown("ACKED")).toBeInTheDocument();
    expect(items().elements()).toHaveLength(1);
  });

  it("shows nack_reason and nack_detail as text, never as HTML", async () => {
    const hostile = '<img src=x onerror="alert(1)">';
    fakeDb.commands.add(commandFaketory.build({ id: id(1), status: "NACKED", nack_reason: "INVALID_STATE", nack_detail: hostile }));

    await renderList();

    await expect.element(shown("INVALID_STATE")).toBeInTheDocument();
    await expect.element(page.getByText(new RegExp(escapeRegExp(hostile)))).toBeInTheDocument();
    expect(page.getByRole("img").elements()).toHaveLength(0);
  });

  it("shows No reply from drone for TIMED_OUT, then the new status with Late reply", async () => {
    fakeDb.commands.add(commandFaketory.build({ id: id(1), status: "TIMED_OUT" }));
    const store = await renderList();
    await expect.element(shown("No reply from drone")).toBeInTheDocument();

    store.applyUpdate(commandFaketory.build({ id: id(1), status: "ACKED", late: true }));

    await expect.element(shown("ACKED")).toBeInTheDocument();
    await expect.element(shown("Late reply")).toBeInTheDocument();
  });

  it("shows Not sent to the drone for FAILED", async () => {
    fakeDb.commands.add(commandFaketory.build({ id: id(1), status: "FAILED" }));

    await renderList();

    await expect.element(shown("Not sent to the drone")).toBeInTheDocument();
  });
});
