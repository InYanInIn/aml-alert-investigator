import { arc, group, pointRadial, scaleSqrt } from "d3";
import { useMemo } from "react";
import { count, eur, label, type Flow, type Network } from "./api";
import { aggregateFlows } from "./flows";

const width = 820;
const height = 466;
const center = [width / 2, height / 2] as const;
const turn = 2 * Math.PI;
const ring = 151;
const palette = ["#9dc7de", "#e2bd89", "#c6e878", "#aeb6e4", "#d7a49c", "#a0aeb6"];
const externalKinds = [
  "business_client", "business_supplier", "cash_desk", "employer", "grant_office", "landlord", "merchant",
  "mortgage_lender", "other_external", "parent", "private_buyer", "relative", "tax_office", "university", "utility",
];

function chartLayout(network: Network) {
  const positions = new Map<string, [number, number]>();
  const internal = network.clients.filter((client) => client.persona !== "external");
  const personas = Array.from(group(internal, (client) => client.persona)).sort(([a], [b]) => a.localeCompare(b));
  const sectors = personas.map(([persona, clients], index) => {
    const start = index * turn / personas.length + 0.13;
    const end = (index + 1) * turn / personas.length - 0.13;
    const mid = (start + end) / 2;
    const color = palette[index % palette.length];
    positions.set(persona, pointRadial(mid, ring));
    const nodes = clients.slice().sort((a, b) => a.id.localeCompare(b.id)).map((client, offset) => {
      const angle = start + (end - start) * (offset + 0.5) / clients.length;
      const position = pointRadial(angle, ring - 5 - (offset % 6) * 5.5);
      positions.set(client.id, position);
      return { client, position };
    });
    return { persona, start, end, mid, color, nodes };
  });
  const externals = network.clients.filter((client) => client.persona === "external");
  const kinds = [...externalKinds, ...externals.map(({ id }) => id.slice(9)).filter((kind) => !externalKinds.includes(kind)).sort()];
  const outside = externals.map((client) => {
    const index = kinds.indexOf(client.id.slice(9));
    const angle = (index + 0.5) * turn / kinds.length;
    const [x, y] = pointRadial(angle, 204);
    const position: [number, number] = [x * 1.38, y];
    positions.set(client.id, position);
    return { client, position };
  });
  return { positions, sectors, outside };
}

function linkPath(flow: Flow, positions: Map<string, [number, number]>) {
  const [x1, y1] = positions.get(flow.from_client)!;
  const [x2, y2] = positions.get(flow.to_client)!;
  const length = Math.hypot(x2 - x1, y2 - y1);
  if (length < 1) return `M${x1},${y1} c-28,-40 38,-40 5,-3`;
  const dx = (x2 - x1) / length;
  const dy = (y2 - y1) / length;
  const pull = Math.min(100, length / 2);
  return `M${x1},${y1} C${x1 * 0.45 - dy * pull * 0.28},${y1 * 0.45 + dx * pull * 0.28} ${x2 * 0.45 - dy * pull * 0.28},${y2 * 0.45 + dx * pull * 0.28} ${x2 - dx * 6},${y2 - dy * 6}`;
}

export function Graph({ network, blocked, selected, select, outlined }: {
  network: Network; blocked: ReadonlySet<string>; selected: string | null; select: (id: string) => void; outlined: ReadonlySet<string>;
}) {
  const { positions, sectors, outside } = useMemo(() => chartLayout(network), [network]);
  const focused = network.clients.find((client) => client.id === selected);
  const flows = useMemo(() => {
    const source = selected
      ? aggregateFlows(network.arcs.filter((flow) => flow.from_client === selected || flow.to_client === selected))
      : aggregateFlows(network.arcs, new Map(network.clients.filter((client) => client.persona !== "external").map((client) => [client.id, client.persona])));
    const visible = source.filter(({ from_client, to_client }) => positions.has(from_client) && positions.has(to_client));
    return visible.sort((a, b) => b.count - a.count).slice(0, selected ? undefined : 34);
  }, [network, selected, positions]);
  const connected = new Set(flows.flatMap(({ from_client, to_client }) => [from_client, to_client]));
  const thickness = scaleSqrt().domain([0, Math.max(1, ...flows.map(({ count }) => count))]).range([0.55, focused ? 3.1 : 3.7]);
  const sectorArc = arc();
  const names = new Map(network.clients.map(({ id, name }) => [id, name]));
  return (
    <svg className="network-svg" viewBox={`0 0 ${width} ${height}`} role="group" aria-label="Radial payment network. Use the client list to select by keyboard.">
      <defs>
        <marker id="arrow-overview" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto" viewBox="0 0 5 5">
          <path d="M0,0 L5,2.5 L0,5Z" fill="#93a9b9" />
        </marker>
        <marker id="arrow-in" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto" viewBox="0 0 6 6">
          <path d="M0,0 L6,3 L0,6Z" fill="#9dc7de" />
        </marker>
        <marker id="arrow-out" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto" viewBox="0 0 6 6">
          <path d="M0,0 L6,3 L0,6Z" fill="#d5ef8a" />
        </marker>
      </defs>
      <g transform={`translate(${center[0]} ${center[1]})`}>
        {[55, 104, 158].map((radius) => <circle className="radar-ring" key={radius} r={radius} />)}
        <ellipse className="radar-ring outer-ring" rx={281} ry={204} />
        <path className="radar-axis" d="M-304 0H304 M0 -212V212" />
        <g key={selected ?? "overview"} className="flow-layer">
          {flows.map((flow) => {
            const direction = focused ? (flow.from_client === selected ? "out" : "in") : "overview";
            return <path key={JSON.stringify([flow.from_client, flow.to_client])} d={linkPath(flow, positions)} className={`flow flow-${direction}`} strokeWidth={thickness(flow.count)} markerEnd={`url(#arrow-${direction})`}>
              <title>{names.get(flow.from_client) ?? label(flow.from_client)} → {names.get(flow.to_client) ?? label(flow.to_client)} · {count(flow.count)} payments · {eur(flow.eur)}</title>
            </path>;
          })}
        </g>
        {sectors.map(({ persona, start, end, mid, color, nodes }) => {
          const [x, y] = pointRadial(mid, 170);
          return <g key={persona} style={{ color }}>
            <path className="sector" d={sectorArc({ innerRadius: 152, outerRadius: 156, startAngle: start, endAngle: end }) ?? undefined} />
            <text x={x} y={y} dominantBaseline="middle" textAnchor={x < -8 ? "end" : x > 8 ? "start" : "middle"} className="sector-label">
              {label(persona)} <tspan>{count(nodes.length)}</tspan>
            </text>
            {nodes.map(({ client, position: [cx, cy] }) => {
              const isBlocked = blocked.has(client.id);
              const isSelected = client.id === selected;
              return <g key={client.id} className={`graph-client${isBlocked ? " blocked" : ""}${focused && !connected.has(client.id) && !isSelected ? " dim" : ""}${isSelected ? " selected" : ""}`} transform={`translate(${cx} ${cy})`} onClick={() => select(client.id)}>
                <circle className="node-hit" r={6} />
                {(outlined.has(client.id) || isSelected) && <circle className="node-outline" r={5.3} />}
                {isBlocked ? <path className="blocked-node" d="M-2 -2 2 2 M2 -2 -2 2" /> : <circle className="node" r={isSelected ? 3.9 : 2.05} />}
                <title>{client.name}{isBlocked ? " · Blocked" : ""}</title>
              </g>;
            })}
          </g>;
        })}
        {outside.map(({ client, position: [x, y] }) => {
          const align = x < -8 ? "end" : x > 8 ? "start" : "middle";
          return <g key={client.id} className={`external-node${focused && !connected.has(client.id) ? " dim" : ""}`} transform={`translate(${x} ${y})`}>
            <path d="M0 -4 4 0 0 4 -4 0Z" />
            <text x={align === "start" ? 10 : align === "end" ? -10 : 0} y={align === "middle" ? (y < 0 ? -10 : 16) : 1} dominantBaseline="middle" textAnchor={align}>{client.name}</text>
          </g>;
        })}
        {flows.length === 0 && <text className="map-message" y={4} textAnchor="middle">
          {network.clients.length === 0 ? "No clients in this world" : "No payments in this window"}
        </text>}
      </g>
    </svg>
  );
}
