import { commandFaketory } from "../../tests/faketories";
import { page } from "vitest/browser";
import { describe, expect, it } from "vitest";
import { useCommandsStore } from "../stores/commands";
import { fakeDb } from "../../tests/fake-db";
import { renderComponent } from "../../tests/render";
import CommandList from "./CommandList.vue";

async function renderList() {
  const { pinia } = await renderComponent(CommandList, { props: { droneId: "d1" } });
  return useCommandsStore(pinia);
}

const id = (n: number) => `00000000-0000-4000-8000-00000000000${n}`;
const status = (cid: string) => page.getByTestId(`command-status-${cid}`);

describe("CommandList", () => {
  it("loads the history when it opens and shows it newest first", async () => {
    fakeDb.commands.add(
      commandFaketory.build({ id: id(1), type: "ARM", created_at: "2026-01-01T00:00:00Z" }),
      commandFaketory.build({ id: id(2), type: "LAND", created_at: "2026-01-01T00:05:00Z" }),
    );

    await renderList();

    await expect.element(page.getByTestId(`command-row-${id(2)}`)).toBeInTheDocument();
    const rows = [...page.getByTestId("command-list").element().querySelectorAll("li")].map((li) =>
      li.getAttribute("data-testid"),
    );
    expect(rows).toEqual([`command-row-${id(2)}`, `command-row-${id(1)}`]);
    expect(fakeDb.commands.listed).toEqual(["d1"]);
  });

  it("says No commands yet when the drone has none", async () => {
    await renderList();

    await expect.element(page.getByTestId("command-log-empty")).toHaveTextContent("No commands yet");
  });

  it("changes the status of a row in place when command.updated arrives", async () => {
    fakeDb.commands.add(commandFaketory.build({ id: id(1), status: "SENT" }));
    const store = await renderList();
    await expect.element(status(id(1))).toHaveTextContent("SENT");

    store.applyUpdate(commandFaketory.build({ id: id(1), status: "ACKED" }));

    await expect.element(status(id(1))).toHaveTextContent("ACKED");
    expect(page.getByTestId("command-list").element().querySelectorAll("li")).toHaveLength(1);
  });

  it("shows nack_reason and nack_detail as text, never as HTML", async () => {
    const hostile = '<img src=x onerror="alert(1)">';
    fakeDb.commands.add(commandFaketory.build({ id: id(1), status: "NACKED", nack_reason: "INVALID_STATE", nack_detail: hostile }));

    await renderList();

    await expect.element(page.getByTestId(`command-nack-reason-${id(1)}`)).toHaveTextContent("INVALID_STATE");
    const detail = page.getByTestId(`command-nack-detail-${id(1)}`);
    await expect.element(detail).toMatchTextContent(hostile);
    expect(detail.element().querySelector("img")).toBeNull();
  });

  it("shows No reply from drone for TIMED_OUT, then the new status with Late reply", async () => {
    fakeDb.commands.add(commandFaketory.build({ id: id(1), status: "TIMED_OUT" }));
    const store = await renderList();
    await expect.element(page.getByTestId(`command-timeout-${id(1)}`)).toHaveTextContent("No reply from drone");

    store.applyUpdate(commandFaketory.build({ id: id(1), status: "ACKED", late: true }));

    await expect.element(status(id(1))).toHaveTextContent("ACKED");
    await expect.element(page.getByTestId(`command-late-${id(1)}`)).toHaveTextContent("Late reply");
  });

  it("shows Not sent to the drone for FAILED", async () => {
    fakeDb.commands.add(commandFaketory.build({ id: id(1), status: "FAILED" }));

    await renderList();

    await expect.element(page.getByTestId(`command-failed-${id(1)}`)).toHaveTextContent("Not sent to the drone");
  });
});
