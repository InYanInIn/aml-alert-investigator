import type { Flow } from "./api";

export function aggregateFlows(arcs: Flow[], groups: ReadonlyMap<string, string> = new Map()): Flow[] {
  const combined = new Map<string, Flow>();
  for (const arc of arcs) {
    const from_client = groups.get(arc.from_client) ?? arc.from_client;
    const to_client = groups.get(arc.to_client) ?? arc.to_client;
    const key = JSON.stringify([from_client, to_client]);
    const existing = combined.get(key);
    if (existing) {
      existing.count += arc.count;
      existing.eur += arc.eur;
    } else {
      combined.set(key, { ...arc, from_client, to_client });
    }
  }
  return [...combined.values()];
}
