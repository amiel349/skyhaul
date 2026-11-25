import { defineStore } from "pinia";
import { ref } from "vue";

export type ConnectionStatus = "offline" | "connecting" | "live" | "reconnecting";

export const useConnectionStore = defineStore("connection", () => {
  const status = ref<ConnectionStatus>("offline");
  function set(next: ConnectionStatus): void {
    status.value = next;
  }
  return { status, set };
});
