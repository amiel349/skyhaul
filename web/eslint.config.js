import { defineConfigWithVueTs, vueTsConfigs } from "@vue/eslint-config-typescript";
import pluginVue from "eslint-plugin-vue";

export default defineConfigWithVueTs(
  { ignores: ["dist/**", "coverage/**", "src/testing/public/**"] },
  pluginVue.configs["flat/recommended"],
  vueTsConfigs.recommended,
  // Drone names, alert text and emails are untrusted: never render them as HTML.
  { rules: { "vue/no-v-html": "error" } },
);
