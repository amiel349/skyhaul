import { page } from "vitest/browser";
import { describe, expect, it } from "vitest";
import { useCommandsStore } from "../stores/commands";
import { worker } from "../../tests/worker";
import { renderComponent } from "../../tests/render";
import { fakeDb } from "../../tests/fake-db";
import { commandEndpoints } from "../../tests/fake-endpoints";
import CommandForm from "./CommandForm.vue";

async function renderForm(linkLost = false) {
  const { pinia } = await renderComponent(CommandForm, { props: { droneId: "d1", linkLost } });
  return useCommandsStore(pinia);
}

const type = () => page.getByRole("combobox", { name: "Command" });
const send = () => page.getByRole("button", { name: /^Send/ });
const field = (label: string) => page.getByRole("textbox", { name: label });
const alertText = (text: string) => page.getByRole("alert").filter({ hasText: text });

describe("CommandForm", () => {
  it("offers the six direct commands and never START_MISSION", async () => {
    await renderForm();

    const options = page.getByRole("option").elements().map((o) => (o as HTMLOptionElement).value);
    expect(options).toEqual(["ARM", "TAKEOFF", "GOTO", "RETURN_HOME", "LAND", "SET_SPEED"]);
  });

  it("shows the payload inputs of the picked type and posts {type, payload}", async () => {
    await renderForm();

    await type().selectOptions("GOTO");
    await field("Latitude (deg)").fill("32.1");
    await field("Longitude (deg)").fill("34.8");
    await field("Altitude (m)").fill("30");
    await send().click();

    await expect.poll(() => fakeDb.commands.requests.length).toBe(1);
    expect(fakeDb.commands.requests[0]?.droneId).toBe("d1");
    expect(fakeDb.commands.requests[0]?.body).toEqual({ type: "GOTO", payload: { lat: 32.1, lon: 34.8, altitude_m: 30 } });
  });

  it("adds a SENT row to the drone's command list on 202", async () => {
    const store = await renderForm();

    await send().click();

    await expect.poll(() => store.forDrone("d1").map((c) => c.status)).toEqual(["SENT"]);
  });

  it("blocks an empty required field and an out-of-range number without calling the API", async () => {
    await renderForm();
    await type().selectOptions("TAKEOFF");

    await send().click();
    await expect.element(field("Altitude (m)")).toHaveAccessibleDescription("Required");

    await field("Altitude (m)").fill("500");
    await send().click();
    await expect.element(field("Altitude (m)")).toHaveAccessibleDescription(expect.stringContaining("Must be 5 to 120"));
    expect(fakeDb.commands.requests).toHaveLength(0);
  });

  it("shows the server's field errors next to the inputs on 422", async () => {
    worker.use(
      commandEndpoints.respondWith(422, {
        detail: "Invalid payload",
        fields: { "payload.altitude_m": ["Too high for this drone"] },
      }),
    );
    await renderForm();
    await type().selectOptions("TAKEOFF");
    await field("Altitude (m)").fill("30");

    await send().click();

    await expect.element(field("Altitude (m)")).toHaveAccessibleDescription("Too high for this drone");
    await expect.element(alertText("Invalid payload")).toHaveTextContent("Invalid payload");
  });

  it("says Too many commands, wait, with the Retry-After seconds, on 429", async () => {
    worker.use(commandEndpoints.respondWith(429, { retryAfter: 12, detail: "Slow down" }));
    await renderForm();

    await send().click();

    await expect.element(page.getByRole("alert")).toHaveTextContent("Too many commands, wait 12 s");
  });

  it("shows an error and adds no row on 503 command_not_sent", async () => {
    worker.use(commandEndpoints.respondWith(503, { detail: "The broker did not confirm" }));
    const store = await renderForm();

    await send().click();

    await expect.element(page.getByRole("alert")).toHaveTextContent("The broker did not confirm");
    expect(store.forDrone("d1")).toHaveLength(0);
  });

  it("shows the server detail on 409", async () => {
    worker.use(commandEndpoints.respondWith(409, { detail: "Drone is on a mission" }));
    await renderForm();

    await send().click();

    await expect.element(page.getByRole("alert")).toHaveTextContent("Drone is on a mission");
  });

  it("warns when the link is lost but still lets the dispatcher send", async () => {
    await renderForm(true);

    await expect.element(page.getByRole("status")).toMatchTextContent("Link lost");
    await expect.element(send()).toBeEnabled();
  });

  it("disables Send until the answer, and a retry reuses the Idempotency-Key", async () => {
    let release = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    worker.use(commandEndpoints.respondWith(503, { detail: "The broker did not confirm", gate }));
    await renderForm();

    await send().click();
    await expect.element(send()).toBeDisabled();
    release();
    await expect.element(send()).toBeEnabled();

    worker.use(commandEndpoints.respondWith(202));
    await send().click();

    await expect.poll(() => fakeDb.commands.requests.length).toBe(2);
    expect(fakeDb.commands.requests[0]?.idempotencyKey).toBeTruthy();
    expect(fakeDb.commands.requests[1]?.idempotencyKey).toBe(fakeDb.commands.requests[0]?.idempotencyKey);
  });
});
