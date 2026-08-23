export const THEME_STORAGE_KEY = "accountops-theme";

export function normalizeTheme(value) {
  return value === "dark" ? "dark" : "light";
}

export function readStoredTheme(storage = typeof globalThis !== "undefined" ? globalThis.localStorage : null) {
  try {
    return normalizeTheme(storage?.getItem(THEME_STORAGE_KEY));
  } catch {
    return "light";
  }
}

export function applyTheme(theme, root = typeof document !== "undefined" ? document.documentElement : null) {
  const normalized = normalizeTheme(theme);
  if (!root) return normalized;
  root.classList.toggle("theme-dark", normalized === "dark");
  root.dataset.theme = normalized;
  root.style.colorScheme = normalized;
  return normalized;
}
