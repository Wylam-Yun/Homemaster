import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const vendor = resolve(root, "src/homemaster/browser/vendor/opencli_1_8_7/dist/src/browser");
const output = resolve(root, "src/homemaster/browser/generated/opencli_1_8_7");
const { generateSnapshotJs, getFormStateJs } = await import(
  `file://${resolve(vendor, "dom-snapshot.js")}`
);
const { buildExtractHtmlJs } = await import(`file://${resolve(vendor, "extract.js")}`);
const { buildHtmlTreeJs } = await import(`file://${resolve(vendor, "html-tree.js")}`);

await mkdir(output, { recursive: true });
const files = new Map([
  ["dom_snapshot_full.js", generateSnapshotJs({ interactiveOnly: false })],
  ["dom_snapshot_interactive.js", generateSnapshotJs({ interactiveOnly: true })],
  ["form_state.js", getFormStateJs()],
  ["extract_html.js", buildExtractHtmlJs(null)],
  ["html_tree.js", buildHtmlTreeJs({ depth: 8, childrenMax: 100, textMax: 500 })],
]);
const manifest = { upstream: "@jackwener/opencli@1.8.7", generated: {} };
for (const [name, content] of files) {
  await writeFile(resolve(output, name), `${content}\n`, "utf8");
  manifest.generated[name] = createHash("sha256").update(`${content}\n`).digest("hex");
}
await writeFile(resolve(output, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
