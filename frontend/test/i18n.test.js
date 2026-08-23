import test from "node:test";
import assert from "node:assert/strict";
import { LANGUAGE_STORAGE_KEY, LANGUAGE_OPTIONS, normalizeLanguage, readStoredLanguage, saveStoredLanguage, translate } from "../src/i18n.js";

test("language helpers normalize and persist supported languages", () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => values.set(key, value),
  };
  assert.equal(LANGUAGE_STORAGE_KEY, "accountops-language");
  assert.equal(normalizeLanguage("en-US"), "en-US");
  assert.equal(normalizeLanguage("fr-FR"), "zh-CN");
  assert.equal(saveStoredLanguage("en-US", storage), "en-US");
  assert.equal(readStoredLanguage(storage), "en-US");
  assert.equal(LANGUAGE_OPTIONS[1].label, "English");
});

test("translate covers shared UI labels and preserves raw diagnostic text", () => {
  assert.equal(translate("账号管理", "en-US"), "Accounts");
  assert.equal(translate("注册工作台", "zh-CN"), "注册工作台");
  assert.equal(translate("backend: invalid_grant", "en-US"), "backend: invalid_grant");
});

test("translate covers dynamic dashboard and operations labels", () => {
  assert.equal(translate("今日注册成功 12 · 失败 3 · 成功率 80%", "en-US"), "Today's successful registrations 12 · Failed 3 · Success rate 80%");
  assert.equal(translate("已载入 37 个 · 最多 3 个", "en-US"), "37 loaded · up to 3");
  assert.equal(translate("9 天后过期", "en-US"), "9 days until expiry");
  assert.equal(translate("已配置 0 个账号", "en-US"), "0 accounts configured");
  assert.equal(translate("运行中 · 自动滚动到最新", "en-US"), "Running · Auto-scroll to latest");
});
