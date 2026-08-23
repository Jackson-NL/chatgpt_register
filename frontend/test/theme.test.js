import test from "node:test";
import assert from "node:assert/strict";
import { applyTheme, normalizeTheme, readStoredTheme, THEME_STORAGE_KEY } from "../src/theme.js";

test("theme helpers normalize and persist only light/dark values", () => {
  assert.equal(normalizeTheme("dark"), "dark");
  assert.equal(normalizeTheme("other"), "light");
  assert.equal(readStoredTheme({ getItem: () => "dark" }), "dark");
  assert.equal(readStoredTheme({ getItem: () => "other" }), "light");
  assert.equal(THEME_STORAGE_KEY, "accountops-theme");
});

test("applyTheme updates the document root without changing the requested value", () => {
  const root = { classList: { toggle(name, enabled) { this[name] = enabled; } }, dataset: {}, style: {} };
  assert.equal(applyTheme("dark", root), "dark");
  assert.equal(root.classList["theme-dark"], true);
  assert.equal(root.dataset.theme, "dark");
  assert.equal(root.style.colorScheme, "dark");
  assert.equal(applyTheme("light", root), "light");
  assert.equal(root.classList["theme-dark"], false);
  assert.equal(root.dataset.theme, "light");
});
