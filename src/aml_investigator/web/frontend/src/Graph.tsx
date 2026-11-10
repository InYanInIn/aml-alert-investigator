import { arc, group, pointRadial, scaleSqrt } from "d3";
import { useId, useMemo } from "react";
import { countLabel, eur, label, type Network } from "./api";
import { aggregateFlows, flowCurve } from "./flows";

const turn = 2 * Math.PI;
const ring = 155;
const palette = ["#a3c6d1", "#c5ac8c", "#c7d5a1", "#b3accb", "#c5a09c", "#a0aeb6"];
const externalKinds = [
  "business_client", "business_supplier", "cash_desk", "employer", "grant_office", "landlord", "merchant",
  "mortgage_lender", "other_external", "parent", "private_buyer", "relative", "tax_office", "university", "utility",
];

function chartLayout(network: Network) {
  const positions = new Map<string, [number, number]>();
  const internal = network.clients.filter((client) => client.persona !== "external");
  const personas = Array.from(group(internal, (client) => client.persona)).sort(([a], [b]) => a.localeCompare(b));
  const sectors = personas.map(([persona, clients], index) => {
    const start = index * turn / personas.length + 0.15;
    const end = (index + 1) * turn / personas.length - 0.15;
    const mid = (start + end) / 2;
    const color = palette[index % palette.length];
    positions.set(persona, pointRadial(mid, ring));
    const nodes = clients.slice().sort((a, b) => a.id.localeCompare(b.id)).map((client, offset) => {
      const angle = start + (end - start) * (offset + 0.5) / clients.length;
      const position = pointRadial(angle, ring - 7 - (offset % 6) * 5);
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
    const [x, y] = pointRadial(angle, 200);
    const position: [number, number] = [x * 1.23, y];
    positions.set(client.id, position);
    return { client, position };
  });
  return { positions, sectors, outside };
}

export function Graph({ network, blocked, selected, select, outlined }: {
  network: Network; blocked: ReadonlySet<string>; selected: string | null; select: (id: string) => void; outlined: ReadonlySet<string>;
}) {
  const { positions, sectors, outside } = useMemo(() => chartLayout(network), [network]);
  const focused = network.clients.find((client) => client.id === selected);
  const gradientPrefix = useId();
  const { flows, withinSector } = useMemo(() => {
    const source = selected
      ? aggregateFlows(network.arcs.filter((flow) => flow.from_client === selected || flow.to_client === selected))
      : aggregateFlows(network.arcs, new Map(network.clients.filter((client) => client.persona !== "external").map((client) => [client.id, client.persona])));
    const visible = source.filter(({ from_client, to_client }) =>
      (selected || from_client !== to_client) && positions.has(from_client) && positions.has(to_client),
    );
    return {
      flows: visible.sort((a, b) => b.count - a.count).slice(0, selected ? undefined : 17),
      withinSector: new Map(source.filter(({ from_client, to_client }) => !selected && from_client === to_client)
        .map((flow) => [flow.from_client, flow])),
    };
  }, [network, selected, positions]);
  const connected = new Set(flows.flatMap(({ from_client, to_client }) => [from_client, to_client]));
  const labeledExternals = new Set([...connected].filter((id) => id.startsWith("external:")).slice(0, focused ? 7 : 5));
  const thickness = scaleSqrt().domain([0, Math.max(1, ...flows.map(({ count }) => count))]).range([0.65, focused ? 2.45 : 2.7]);
  const sectorArc = arc();
  const names = new Map(network.clients.map(({ id, name }) => [id, name]));
  const routes = flows.map((flow, index) => {
    const source = positions.get(flow.from_client)!;
    const target = positions.get(flow.to_client)!;
    const direction = !focused ? "overview" : flow.from_client === flow.to_client ? "internal" : flow.from_client === selected ? "out" : "in";
    return { flow, source, target, direction, id: `${gradientPrefix}-${index}`, ...flowCurve(source, target) };
  });

  return (
    <svg className="network-svg" viewBox="-356 -222 712 444" role="group" aria-label="Payment network. Choose a client in the directory to inspect its payments.">
      <defs>
        {routes.filter(({ flow }) => flow.from_client !== flow.to_client).map(({ id, direction, source, target }) =>
          <linearGradient key={id} id={id} className={`flow-${direction}`} gradientUnits="userSpaceOnUse" x1={source[0]} y1={source[1]} x2={target[0]} y2={target[1]}>
            <stop stopColor="currentColor" stopOpacity={focused ? 0.27 : 0.16} />
            <stop offset="100%" stopColor="currentColor" stopOpacity={focused ? 0.93 : 0.62} />
          </linearGradient>)}
      </defs>
      {[73, 112, ring].map((radius) => <circle className="radar-ring" key={radius} r={radius} />)}
      <g key={selected ?? "overview"} className="flow-layer">
        {routes.slice().reverse().map(({ flow, id, path, end, direction }) =>
          <g key={JSON.stringify([flow.from_client, flow.to_client])} className={`flow-route flow-${direction}`}>
            <title>{names.get(flow.from_client) ?? label(flow.from_client)} → {names.get(flow.to_client) ?? label(flow.to_client)} · {countLabel(flow.count, "payment")} · {eur(flow.eur)}</title>
            <path className="flow" d={path} stroke={flow.from_client === flow.to_client ? "currentColor" : `url(#${id})`} strokeWidth={thickness(flow.count)} />
            <circle className={`flow-destination${focused ? "" : " flow-destination-hover"}`} cx={end[0]} cy={end[1]} r={2} />
          </g>)}
      </g>
      {sectors.map(({ persona, start, end, mid, color, nodes }) => {
        const [x, y] = pointRadial(mid, 172);
        const internal = withinSector.get(persona);
        const quietSector = focused && !nodes.some(({ client }) => connected.has(client.id) || client.id === selected);
        return <g key={persona} className={quietSector ? "quiet-sector" : undefined} style={{ color }}>
          <path className="sector" d={sectorArc({ innerRadius: ring - 0.6, outerRadius: ring + 1.8, startAngle: start, endAngle: end }) ?? undefined}>
            <title>{label(persona)} · {countLabel(nodes.length, "client")}{internal && ` · ${countLabel(internal.count, "within-group payment")} · ${eur(internal.eur)}`}</title>
          </path>
          <text x={x} y={y} dominantBaseline="middle" textAnchor={x < -8 ? "end" : x > 8 ? "start" : "middle"} className="sector-label">
            {label(persona)}
          </text>
          <g aria-hidden="true">
            {nodes.map(({ client, position: [cx, cy] }) => {
              const isBlocked = blocked.has(client.id);
              const isSelected = client.id === selected;
              return <g key={client.id} className={`graph-client${isBlocked ? " blocked" : ""}${focused && !connected.has(client.id) && !isSelected ? " dim" : ""}${isSelected ? " selected" : ""}`} transform={`translate(${cx} ${cy})`} onClick={() => select(client.id)}>
                <circle className="node-hit" r={6} />
                {(outlined.has(client.id) || isSelected) && <circle className="node-outline" r={5.3} />}
                {isBlocked ? <path className="blocked-node" d="M-2 -2 2 2 M2 -2 -2 2" /> : <circle className="node" r={isSelected ? 3.8 : 1.8} />}
                <title>{client.name}{isBlocked ? " · Blocked" : ""}</title>
              </g>;
            })}
          </g>
        </g>;
      })}
      {outside.map(({ client, position: [x, y] }) => {
        const align = x < -8 ? "end" : x > 8 ? "start" : "middle";
        return <g key={client.id} className={`external-node${!connected.has(client.id) ? " dim" : ""}`} transform={`translate(${x} ${y})`} data-half={y < 0 ? "upper" : "lower"} data-edge={Math.abs(x) > 125 || undefined}>
          <title>{client.name}</title>
          <path d="M0 -3.5 3.5 0 0 3.5 -3.5 0Z" />
          {labeledExternals.has(client.id) &&
            <text x={align === "start" ? 10 : align === "end" ? -10 : 0} y={align === "middle" ? (y < 0 ? -10 : 14) : 1} dominantBaseline="middle" textAnchor={align}>{client.name}</text>}
        </g>;
      })}
      {flows.length === 0 && withinSector.size === 0 && <text className="map-message" y={4} textAnchor="middle">
        {network.clients.length === 0 ? "No clients in this world" : "No payments in this window"}
      </text>}
    </svg>
  );
}
