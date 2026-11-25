// The only place that touches socket.io-client. Stores call these functions.
import { io } from "socket.io-client";
import type { Socket } from "socket.io-client";
import type { DroneStateEvent, SocketConnectError } from "../api/types";

export interface FleetSocketHandlers {
  onConnect: () => void;
  onDisconnect: () => void;
  onDroneState: (event: DroneStateEvent) => void;
  onAuthError: (reason: SocketConnectError["message"]) => void;
}

let socket: Socket | null = null;

export function openFleetSocket(
  getToken: () => string | null,
  handlers: FleetSocketHandlers,
): void {
  closeFleetSocket();
  const next = io("/fleet", {
    path: "/socket.io",
    // Read on every (re)connect; the token goes in the auth payload, never in the URL.
    auth: (cb) => cb({ token: getToken() ?? "" }),
    transports: ["websocket"],
  });
  next.on("connect", handlers.onConnect);
  next.on("disconnect", handlers.onDisconnect);
  next.on("drone.state", handlers.onDroneState);
  next.on("connect_error", (error: Error) => {
    if (error.message === "unauthorized" || error.message === "token_expired") {
      next.disconnect(); // stop reconnecting with a bad token
      handlers.onAuthError(error.message);
    }
  });
  socket = next;
}

export function closeFleetSocket(): void {
  socket?.removeAllListeners();
  socket?.disconnect();
  socket = null;
}
