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

type Point = readonly [number, number];

function inset(point: Point, toward: Point, distance: number): Point {
  const dx = toward[0] - point[0];
  const dy = toward[1] - point[1];
  const length = Math.hypot(dx, dy);
  const scale = length ? Math.min(distance / length, 0.5) : 0;
  return [point[0] + dx * scale, point[1] + dy * scale];
}

export function flowCurve(source: Point, target: Point) {
  const [x1, y1] = source;
  const [x2, y2] = target;
  const distance = Math.hypot(x2 - x1, y2 - y1);
  if (distance < 1) {
    const radius = Math.hypot(x1, y1);
    const [nx, ny] = radius ? [x1 / radius, y1 / radius] : [0, -1];
    const [tx, ty] = [-ny, nx];
    const start = [x1 + tx * 4, y1 + ty * 4];
    const end: Point = [x1 - tx * 4, y1 - ty * 4];
    const a = [x1 + nx * 22 + tx * 14, y1 + ny * 22 + ty * 14];
    const b = [x1 + nx * 22 - tx * 14, y1 + ny * 22 - ty * 14];
    return { path: `M${start} C${a} ${b} ${end}`, end };
  }

  const bend = Math.min(18, distance * 0.21) / distance;
  const [offsetX, offsetY] = [-(y2 - y1) * bend, (x2 - x1) * bend];
  const a: Point = [x1 * 0.56 + offsetX, y1 * 0.56 + offsetY];
  const b: Point = [x2 * 0.56 + offsetX, y2 * 0.56 + offsetY];
  const start = inset(source, a, 3.5);
  const end = inset(target, b, Math.min(5.5, distance / 4));
  return { path: `M${start} C${a} ${b} ${end}`, end };
}
