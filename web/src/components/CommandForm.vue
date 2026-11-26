<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import type { DirectCommandType } from "../api/types";
import { COMMAND_TYPES, FIELDS, validatePayload, type FieldErrors, type FieldName } from "../commands/payload";
import { useCommandsStore } from "../stores/commands";
import AppButton from "./AppButton.vue";
import AppSelect from "./AppSelect.vue";

const props = defineProps<{ droneId: string; linkLost: boolean }>();

const commands = useCommandsStore();
const type = ref<DirectCommandType>("ARM");
const typeOptions = COMMAND_TYPES.map((name) => ({ value: name, label: name }));
const values = reactive<Partial<Record<FieldName, string>>>({});
const fieldErrors = ref<FieldErrors>({});
const error = ref<string | null>(null);
const sending = ref(false);

const fields = computed(() => FIELDS[type.value]);

// A new type or another drone starts a fresh form.
watch([type, () => props.droneId], () => {
  for (const key of Object.keys(values)) delete values[key as FieldName];
  fieldErrors.value = {};
  error.value = null;
});

function show(message: string): void {
  error.value = message;
}

async function submit(): Promise<void> {
  if (sending.value) return;
  error.value = null;
  const checked = validatePayload(type.value, values);
  if (!checked.ok) {
    fieldErrors.value = checked.errors;
    return;
  }
  fieldErrors.value = {};
  sending.value = true;
  try {
    const result = await commands.send(props.droneId, type.value, checked.payload);
    if (result.ok) return;
    if (result.kind === "validation") {
      const known: FieldErrors = {};
      const rest: string[] = [];
      for (const [path, messages] of Object.entries(result.fields ?? {})) {
        const name = path.replace(/^payload\./, "") as FieldName;
        if (fields.value.some((f) => f.name === name)) known[name] = messages.join(", ");
        else rest.push(`${path}: ${messages.join(", ")}`);
      }
      fieldErrors.value = known;
      show(rest.length > 0 ? `${result.message} (${rest.join("; ")})` : result.message);
    } else if (result.kind === "rate_limited") {
      show(result.retryAfterS ? `${result.message} ${result.retryAfterS} s` : result.message);
    } else show(result.message);
  } finally {
    sending.value = false;
  }
}
</script>

<template>
  <form
    class="form"
    novalidate
    aria-label="Send a command"
    data-testid="command-form"
    @submit.prevent="submit"
  >
    <p
      v-if="linkLost"
      class="warn"
      role="status"
      data-testid="command-link-warning"
    >
      Link lost. The drone may not receive this command. You can still send it.
    </p>
    <div class="row">
      <AppSelect
        id="command-type"
        :model-value="type"
        label="Command"
        :options="typeOptions"
        data-testid="command-type"
        @update:model-value="type = $event as DirectCommandType"
      />
    </div>
    <div
      v-for="spec in fields"
      :key="`${type}-${spec.name}`"
      class="row"
    >
      <label :for="`command-field-${spec.name}`">{{ spec.label }} ({{ spec.unit }})</label>
      <input
        :id="`command-field-${spec.name}`"
        v-model="values[spec.name]"
        type="text"
        inputmode="decimal"
        autocomplete="off"
        :required="spec.required"
        :aria-invalid="fieldErrors[spec.name] ? 'true' : 'false'"
        :aria-describedby="fieldErrors[spec.name] ? `command-field-error-${spec.name}` : undefined"
        :data-testid="`command-field-${spec.name}`"
      >
      <span
        v-if="fieldErrors[spec.name]"
        :id="`command-field-error-${spec.name}`"
        class="err"
        role="alert"
        :data-testid="`command-field-error-${spec.name}`"
      >{{ fieldErrors[spec.name] }}</span>
    </div>
    <p
      v-if="error"
      class="err"
      role="alert"
      data-testid="command-error"
    >
      {{ error }}
    </p>
    <AppButton
      type="submit"
      class="send"
      variant="primary"
      :loading="sending"
      data-testid="command-send"
    >
      {{ sending ? "Sending..." : "Send" }}
    </AppButton>
  </form>
</template>

<style scoped>
.form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.row {
  display: grid;
  gap: 4px;
}
label {
  font-size: 11px;
  color: var(--muted);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
input {
  background: var(--panel-2);
  color: var(--ink);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 7px 10px;
  font: inherit;
  min-width: 0;
}
input:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}
input[aria-invalid="true"] {
  border-color: var(--crit);
}
.err,
.warn {
  margin: 0;
  font-size: 12px;
}
.err {
  color: var(--crit);
}
.warn {
  padding: 8px 10px;
  border: 1px solid var(--warn);
  border-radius: 6px;
  color: var(--warn);
}
.send {
  align-self: flex-start;
}
</style>
