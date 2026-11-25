<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useAuthStore } from "../stores/auth";
import { useThemeStore, type Theme } from "../theme/theme";
import { avatarColor, initialsOf } from "./avatar";

const auth = useAuthStore();
const theme = useThemeStore();
const open = ref(false);
const root = ref<HTMLElement | null>(null);
const button = ref<HTMLButtonElement | null>(null);
const menu = ref<HTMLElement | null>(null);

const email = computed(() => auth.user?.email ?? "");
const initials = computed(() => initialsOf(email.value));
const color = computed(() => avatarColor(email.value));
const options: { value: Theme; label: string; testid: string }[] = [
  { value: "dark", label: "Dark", testid: "theme-option-dark" },
  { value: "light", label: "Light", testid: "theme-option-light" },
];

function items(): HTMLElement[] {
  return Array.from(menu.value?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? []);
}

function close(refocus: boolean): void {
  open.value = false;
  if (refocus) void nextTick(() => button.value?.focus());
}

function onOutside(event: Event): void {
  if (root.value && !root.value.contains(event.target as Node)) close(false);
}

watch(open, async (isOpen) => {
  if (isOpen) {
    document.addEventListener("pointerdown", onOutside);
    await nextTick();
    items()[0]?.focus();
  } else {
    document.removeEventListener("pointerdown", onOutside);
  }
});
onBeforeUnmount(() => document.removeEventListener("pointerdown", onOutside));

function onKeydown(event: KeyboardEvent): void {
  if (!open.value) return;
  if (event.key === "Escape") {
    event.preventDefault();
    close(true);
  } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const list = items();
    const at = list.indexOf(document.activeElement as HTMLElement);
    const step = event.key === "ArrowDown" ? 1 : -1;
    list[(at + step + list.length) % list.length]?.focus();
  }
}

function onFocusOut(event: FocusEvent): void {
  // Tab past the last item leaves the menu: close it without stealing focus back.
  const next = event.relatedTarget as Node | null;
  if (open.value && next && root.value && !root.value.contains(next)) close(false);
}

function choose(value: Theme): void {
  theme.set(value); // the menu stays open so the change can be seen
}

function logout(): void {
  close(false);
  auth.logout();
}
</script>

<template>
  <div
    ref="root"
    class="user-menu"
    @keydown="onKeydown"
    @focusout="onFocusOut"
  >
    <button
      ref="button"
      type="button"
      class="avatar"
      data-testid="user-avatar"
      aria-haspopup="menu"
      :aria-expanded="open"
      :aria-label="`Account menu for ${email}`"
      :style="{ background: color }"
      @click="open = !open"
    >
      {{ initials }}
    </button>
    <div
      v-if="open"
      ref="menu"
      class="menu"
      role="menu"
      aria-label="Account"
      data-testid="user-menu"
    >
      <div class="who">
        <span
          class="email"
          data-testid="user-menu-email"
        >{{ email }}</span>
        <span
          class="role"
          data-testid="user-menu-role"
        >{{ auth.user?.role }}</span>
      </div>
      <div
        class="group"
        role="group"
        aria-label="Theme"
      >
        <span class="label">Theme</span>
        <button
          v-for="option in options"
          :key="option.value"
          type="button"
          role="menuitemradio"
          class="item"
          :aria-checked="theme.resolved === option.value"
          :data-testid="option.testid"
          tabindex="-1"
          @click="choose(option.value)"
        >
          <span
            class="mark"
            aria-hidden="true"
          >{{ theme.resolved === option.value ? "✓" : "" }}</span>
          {{ option.label }}
        </button>
      </div>
      <button
        type="button"
        role="menuitem"
        class="item"
        data-testid="user-menu-logout"
        tabindex="-1"
        @click="logout"
      >
        <span
          class="mark"
          aria-hidden="true"
        />
        Log out
      </button>
    </div>
  </div>
</template>

<style scoped>
.user-menu {
  position: relative;
}
.avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 2px solid var(--line);
  color: #fff;
  font-weight: 600;
  font-size: 13px;
  cursor: pointer;
}
.avatar:hover {
  border-color: var(--accent);
}
.menu {
  position: absolute;
  right: 0;
  top: calc(100% + 8px);
  z-index: 2000;
  min-width: 220px;
  max-width: 80vw;
  padding: 6px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgb(0 0 0 / 0.3);
}
.who {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 4px;
}
.email {
  overflow-wrap: anywhere;
}
.role {
  color: var(--muted);
  font-size: 12px;
  text-transform: capitalize;
}
.group {
  display: flex;
  flex-direction: column;
  border-bottom: 1px solid var(--line);
  margin-bottom: 4px;
  padding-bottom: 4px;
}
.label {
  padding: 4px 10px;
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
}
.item {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 100%;
  text-align: left;
  background: none;
  border: 0;
  border-radius: 6px;
  padding: 7px 10px;
  cursor: pointer;
}
.item:hover,
.item:focus-visible {
  background: var(--panel-2);
}
.mark {
  display: inline-block;
  width: 1em;
  color: var(--accent);
}
</style>
