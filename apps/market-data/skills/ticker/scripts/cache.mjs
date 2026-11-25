// One JSON file per list under the temp dir, written whole through a rename so
// a reader never sees half of one.
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const DIR = join(tmpdir(), "market-data-ticker-lookup");

/** `{fresh, stale}`: the list when younger than maxAgeMs, else the older one. */
export function readList(name, maxAgeMs) {
  try {
    const cached = JSON.parse(readFileSync(join(DIR, name), "utf8"));
    if (!Array.isArray(cached.coins)) return {};
    return Date.now() - cached.at < maxAgeMs ? { fresh: cached.coins } : { stale: cached.coins };
  } catch {
    return {};
  }
}

export function writeList(name, coins) {
  try {
    mkdirSync(DIR, { recursive: true });
    const file = join(DIR, name);
    const temp = `${file}.${process.pid}`;
    writeFileSync(temp, JSON.stringify({ at: Date.now(), coins }));
    renameSync(temp, file);
  } catch {}
}
