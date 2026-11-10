import { useState } from "react";
import { count, label, type Client } from "./api";

export function ClientList({ clients, selected, select, blocked }: {
  clients: Client[]; selected: string | null; select: (id: string) => void; blocked: ReadonlySet<string>;
}) {
  const [query, setQuery] = useState("");
  const [persona, setPersona] = useState("");
  const matching = clients.filter((client) =>
    (!persona || client.persona === persona) && `${client.name} ${client.id}`.toLowerCase().includes(query.trim().toLowerCase()),
  );
  return <div className="directory">
    <div className="client-filters">
      <input aria-label="Search clients" type="search" placeholder="Search name or ID" value={query} onChange={(event) => setQuery(event.currentTarget.value)} />
      <select aria-label="Filter by persona" value={persona} onChange={(event) => setPersona(event.currentTarget.value)}>
        <option value="">All personas</option>
        {[...new Set(clients.map((client) => client.persona))].sort().map((value) => <option value={value} key={value}>{label(value)}</option>)}
      </select>
    </div>
    <p className="eyebrow results-count">{count(matching.length)} clients</p>
    <ul className="client-list" aria-label="Client results">
      {matching.map((client) => <li key={client.id}>
        <button type="button" aria-pressed={selected === client.id} onClick={() => select(client.id)}>
          <span><strong>{client.name}</strong><small>{label(client.persona)} · {client.id}</small></span>
          {blocked.has(client.id) && <span className="blocked-tag">Blocked</span>}
          <span aria-hidden="true" className="row-arrow">↗</span>
        </button>
      </li>)}
    </ul>
    {matching.length === 0 && <p className="quiet">No clients match this search.</p>}
  </div>;
}
