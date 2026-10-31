import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../pages/app/settings/script.js", import.meta.url), "utf8",
);
const functions = source.slice(
  source.indexOf("  function getRulePackIds("),
  source.indexOf("  function getTargetSuggestions("),
);
const save = source.slice(
  source.indexOf("  async function saveRules("),
  source.indexOf("  async function exportBackup("),
);

test("saving an edited rule preserves ordered pack IDs and legacy rules", async () => {
  const submitted = [];
  const sandbox = {
    rules: [
      { id: "session", scope: "session", target: "chat", pack_id: "stale", pack_ids: ["pack-b", "pack-a", "pack-b"] },
      { id: "default", scope: "default", pack_id: "pack-a" },
    ],
    rulesBusy: false, rulesLoaded: true, savedRulesSnapshot: "",
    saveRulesBtn: {},
    renderRulesValidation: () => true,
    updateRulesState() {}, setLoading() {}, clearLoading() {},
    ensureDefaultRuleAtEnd() {}, renderRules() {}, addLog() {},
    apiPost: async (path, payload) => {
      submitted.push({ path, payload: JSON.parse(JSON.stringify(payload)) });
      return payload;
    },
  };
  vm.createContext(sandbox);
  await vm.runInContext(`${functions}\n${save}\nsaveRules()`, sandbox);
  assert.equal(submitted.length, 1);
  assert.deepEqual(submitted[0], {
    path: "settings/rules",
    payload: { rules: [
      { id: "session", scope: "session", target: "chat", pack_id: "pack-b", pack_ids: ["pack-b", "pack-a"] },
      { id: "default", scope: "default", pack_id: "pack-a" },
    ] },
  });
});

test("rule selectors keep all selections and reveal missing secondary packs", () => {
  const selected = [];
  const sandbox = {
    installedPacks: [{ id: "pack-a", name: "A" }, { id: "pack-b", name: "B" }],
    document: { createElement: () => ({}) },
    select: { appendChild: (option) => selected.push(option) },
  };
  vm.createContext(sandbox);
  vm.runInContext(`${functions}\nappendPackOptions(select, ["pack-b", "missing"]);`, sandbox);
  assert.deepEqual(selected.filter((option) => option.selected).map((option) => option.value), ["missing", "pack-b"]);
});
