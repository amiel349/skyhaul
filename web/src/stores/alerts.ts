import { defineStore } from "pinia";
import { computed, reactive, shallowReactive } from "vue";
import { api } from "../api/client";
import type { Alert, AlertSeverity } from "../api/types";

export type SeverityFilter = AlertSeverity | "ALL";

export type AckResult =
  | { ok: true; already: boolean }
  | { ok: false; kind: "forbidden" | "not_found" | "failed"; message: string };

const SEVERITY_RANK: Record<AlertSeverity, number> = { CRITICAL: 0, WARNING: 1, INFO: 2 };

/** Unread = open (not cleared) and not yet acknowledged by anyone. */
const isUnread = (alert: Alert): boolean => !alert.cleared_at && !alert.acknowledged_at;

/** The group an alert sits in: needs attention (open, nobody has it), acknowledged, then cleared. */
function group(alert: Alert): number {
  if (alert.cleared_at) return 2;
  return alert.acknowledged_at ? 1 : 0;
}

/** Within a group CRITICAL comes first, then the newest. */
function order(a: Alert, b: Alert): number {
  if (group(a) !== group(b)) return group(a) - group(b);
  if (a.severity !== b.severity) return SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity];
  if (a.raised_at !== b.raised_at) return a.raised_at < b.raised_at ? 1 : -1;
  return a.id < b.id ? 1 : a.id > b.id ? -1 : 0;
}

/**
 * Merge two states of one alert. The API has no version field (design note Q2), so a state only
 * moves forward: once acknowledged or cleared, a late older event cannot undo it.
 */
function merge(known: Alert, incoming: Alert): Alert {
  return {
    ...incoming,
    acknowledged_by: known.acknowledged_by ?? incoming.acknowledged_by,
    acknowledged_at: known.acknowledged_at ?? incoming.acknowledged_at,
    cleared_at: known.cleared_at ?? incoming.cleared_at,
  };
}

const same = (a: Alert, b: Alert): boolean => JSON.stringify(a) === JSON.stringify(b);

export const useAlertsStore = defineStore("alerts", () => {
  const byId = shallowReactive(new Map<string, Alert>());
  /** The dispatcher's view. The API filters by `open` and `drone_id`; severity is filtered here. */
  const filter = reactive({ severity: "ALL" as SeverityFilter, droneId: "", openOnly: true });
  const loadError = reactive({ failed: false });
  const ackError = shallowReactive(new Map<string, string>());
  const acking = shallowReactive(new Set<string>());
  /** Bumped by reset(): an answer that was in flight at sign-out is dropped. */
  let epoch = 0;

  const sorted = computed(() => [...byId.values()].sort(order));
  const visible = computed(() =>
    sorted.value.filter(
      (alert) =>
        (filter.severity === "ALL" || alert.severity === filter.severity) &&
        (filter.droneId === "" || alert.drone_id === filter.droneId) &&
        (!filter.openOnly || !alert.cleared_at),
    ),
  );
  /** Unread = open (not cleared) and not yet acknowledged by anyone. */
  const unread = computed(() => sorted.value.filter(isUnread).length);
  /** Unread among the rows the table shows, so the count beside the title never contradicts the table. */
  const visibleUnread = computed(() => visible.value.filter(isUnread).length);

  /** Insert or merge by id. An older state, or a duplicate, changes nothing. */
  function applyUpdate(incoming: Alert): void {
    const known = byId.get(incoming.id);
    if (!known) {
      byId.set(incoming.id, incoming);
      return;
    }
    const next = merge(known, incoming);
    if (!same(known, next)) byId.set(incoming.id, next);
  }

  async function fetchPage(query: { open?: boolean; drone_id?: string }): Promise<Alert[] | null> {
    try {
      const { data } = await api.GET("/alerts", { params: { query: { ...query, limit: 200 } } });
      if (!data) {
        loadError.failed = true;
        return null;
      }
      loadError.failed = false;
      return data.items;
    } catch {
      loadError.failed = true;
      return null;
    }
  }

  /** Fetch what the current filters ask for (`?open=`, `?drone_id=`) and merge it in. */
  async function load(): Promise<void> {
    const started = epoch;
    const items = await fetchPage({
      open: filter.openOnly ? true : undefined,
      drone_id: filter.droneId || undefined,
    });
    if (started !== epoch || !items) return;
    for (const item of items) applyUpdate(item);
  }

  /**
   * Refresh after opening the page or a socket reconnect. REST is the source of truth: the open
   * alerts replace what we hold (one that is gone from the open list was cleared while we were
   * away), then the current filters are fetched too if they ask for more than that.
   */
  async function refetch(): Promise<void> {
    const started = epoch;
    // Only an alert we already held when the request left can be judged gone: the answer is a
    // snapshot, so one raised over the socket while it was in flight is newer and must stay.
    const heldBefore = new Set(byId.keys());
    const items = await fetchPage({ open: true });
    if (started !== epoch) return;
    if (items) {
      const open = new Set(items.map((a) => a.id));
      for (const [id, alert] of [...byId]) {
        if (heldBefore.has(id) && !alert.cleared_at && !open.has(id)) byId.delete(id);
      }
      for (const item of items) applyUpdate(item);
    }
    if (!filter.openOnly || filter.droneId !== "") await load();
  }

  function setFilter(patch: Partial<typeof filter>): void {
    Object.assign(filter, patch);
    if (patch.openOnly !== undefined || patch.droneId !== undefined) void load();
  }

  async function ack(id: string): Promise<AckResult> {
    const started = epoch;
    ackError.delete(id);
    acking.add(id);
    try {
      const { data, response } = await api.POST("/alerts/{alert_id}/ack", { params: { path: { alert_id: id } } });
      if (started !== epoch) return { ok: true, already: false };
      if (data) {
        applyUpdate(data);
        return { ok: true, already: false };
      }
      if (response.status === 409) {
        // Someone else got there first: quietly show who, no error.
        await refetch();
        return { ok: true, already: true };
      }
      const result: AckResult =
        response.status === 403
          ? { ok: false, kind: "forbidden", message: "Not allowed" }
          : response.status === 404
            ? { ok: false, kind: "not_found", message: "Alert not found" }
            : { ok: false, kind: "failed", message: "Could not acknowledge the alert" };
      ackError.set(id, result.message);
      return result;
    } catch {
      const result: AckResult = { ok: false, kind: "failed", message: "Could not reach the server" };
      if (started === epoch) ackError.set(id, result.message);
      return result;
    } finally {
      if (started === epoch) acking.delete(id);
    }
  }

  function reset(): void {
    epoch += 1;
    byId.clear();
    ackError.clear();
    acking.clear();
    loadError.failed = false;
    filter.severity = "ALL";
    filter.droneId = "";
    filter.openOnly = true;
  }

  return { byId, filter, loadError, ackError, acking, visible, unread, visibleUnread, applyUpdate, load, refetch, setFilter, ack, reset };
});
