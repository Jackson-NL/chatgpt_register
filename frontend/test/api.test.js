import test from "node:test";
import assert from "node:assert/strict";
import { api } from "../src/api/index.js";

test("registration log API keeps limit=0 for full-history export", async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    calls.push(url);
    return {
      ok: true,
      headers: { get: () => "application/json" },
      json: async () => ({ logs: [] }),
    };
  };
  try {
    await api.registrations.logs(12, { after: 0, limit: 0 });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(calls[0], "/api/registrations/12/logs?after=0&limit=0");
});

test("batch log API keeps limit=0 for full-history export", async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    calls.push(url);
    return {
      ok: true,
      headers: { get: () => "application/json" },
      json: async () => ({ logs: [] }),
    };
  };
  try {
    await api.batches.logs(34, { after: 0, limit: 0 });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(calls[0], "/api/batches/34/logs?after=0&limit=0");
});
