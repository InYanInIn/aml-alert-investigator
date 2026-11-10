import { useCallback, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, count, dayDate, errorText, eur, formatDate, usd, type Client, type ClientDetail, type Job, type Network, type Week, type World } from "./api";
import { ClientCard } from "./ClientCard";
import { ClientList } from "./ClientList";
import { Graph } from "./Graph";
import { SimulatePanel } from "./SimulatePanel";
import { Timeline } from "./Timeline";
import "./world.css";

const emptyIds = new Set<string>();

function Signal({ className }: { className?: string }) {
  return <svg className={className} aria-hidden="true" width="36" height="36" viewBox="0 0 36 36" fill="none">
    <circle cx="18" cy="18" r="15" stroke="currentColor" opacity=".25" />
    <path d="M4 20c8 0 13-3 18-11m-9 20c5-7 9-10 19-12" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    <circle cx="22" cy="9" r="2.3" fill="currentColor" /><circle cx="13" cy="29" r="2.3" fill="currentColor" />
  </svg>;
}

function App() {
  const [world, setWorld] = useState<World>();
  const [weeks, setWeeks] = useState<Week[]>([]);
  const [clients, setClients] = useState<Client[]>([]);
  const [worldError, setWorldError] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [pending, setPending] = useState<number | null>(null);
  const [jobError, setJobError] = useState("");
  const [pinned, setPinned] = useState<number | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [panel, setPanel] = useState<"clients" | "simulate">("clients");
  const [graph, setGraph] = useState<{ day: number; data: Network } | null>(null);
  const [graphError, setGraphError] = useState("");
  const [graphRetry, setGraphRetry] = useState(0);
  const [detail, setDetail] = useState<ClientDetail | null>(null);
  const [detailError, setDetailError] = useState("");
  const [detailRetry, setDetailRetry] = useState(0);
  const [evaluator, setEvaluator] = useState<ReadonlySet<string> | null>(null);
  const [overlayLoading, setOverlayLoading] = useState(false);
  const [overlayError, setOverlayError] = useState("");
  const [revision, setRevision] = useState(0);
  const latest = world?.day ?? 0;
  const day = Math.min(pinned ?? latest, latest);
  const busy = pending !== null || job?.status === "running";

  const refreshWorld = useCallback(async () => {
    try {
      const snapshot = await api.world();
      const [timeline, directory] = snapshot.day > 0 ? await Promise.all([api.timeline(), api.clients()]) : [[], []];
      setWorld(snapshot);
      setWeeks(timeline);
      setClients(directory);
      setWorldError("");
    } catch (failure) {
      setWorldError(errorText(failure));
    }
  }, []);

  useEffect(() => { void refreshWorld(); }, [refreshWorld, revision]);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    let signature = "";
    async function poll() {
      try {
        const current = await api.job();
        if (!alive) return;
        setJob(current);
        setJobError("");
        if (current?.period_id === pending) setPending(null);
        const next = JSON.stringify(current);
        if (signature !== next && current) await refreshWorld();
        signature = next;
        if (alive && (current?.status === "running" || (pending !== null && current?.period_id !== pending))) {
          timer = setTimeout(poll, 2_000);
        }
      } catch (failure) {
        if (!alive) return;
        setJobError(errorText(failure));
        timer = setTimeout(poll, 2_000);
      }
    }
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [pending, refreshWorld, revision]);

  useEffect(() => {
    if (!day) return;
    const controller = new AbortController();
    setGraphError("");
    api.graph(day, controller.signal).then((data) => {
      if (!controller.signal.aborted) setGraph({ day, data });
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted) setGraphError(errorText(failure));
    });
    return () => controller.abort();
  }, [day, revision, graphRetry]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setDetailError("");
    api.client(selected, controller.signal).then((client) => {
      if (!controller.signal.aborted) setDetail(client);
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted) setDetailError(errorText(failure));
    });
    return () => controller.abort();
  }, [selected, latest, detailRetry]);

  async function reset() {
    setPinned(null);
    setSelected(null);
    setGraph(null);
    setDetail(null);
    setEvaluator(null);
    setOverlayError("");
    setJob(null);
    setPanel("clients");
    setRevision((value) => value + 1);
  }

  async function toggleEvaluator() {
    setOverlayError("");
    if (evaluator) {
      setEvaluator(null);
      return;
    }
    setOverlayLoading(true);
    try {
      const rows = await api.evaluator();
      setEvaluator(new Set(rows.map(({ account_id }) => account_id)));
    } catch (failure) {
      setOverlayError(errorText(failure));
    } finally {
      setOverlayLoading(false);
    }
  }

  function selectClient(id: string) {
    setSelected(id);
    setPanel("clients");
  }

  const network = graph?.day === day ? graph.data : undefined;
  const outlined = useMemo(() => evaluator && network
    ? new Set(network.accounts.filter(({ id }) => evaluator.has(id)).map(({ client_id }) => client_id)) : emptyIds, [evaluator, network]);
  const blocked = useMemo(() => {
    const date = dayDate(day).toISOString().slice(0, 10);
    return new Set(clients.filter(({ blocked_from }) => blocked_from !== null && blocked_from <= date).map(({ id }) => id));
  }, [clients, day]);
  const arcs = network?.arcs ?? [];
  const paymentCount = arcs.reduce((sum, flow) => sum + flow.count, 0);
  const paymentEur = arcs.reduce((sum, flow) => sum + flow.eur, 0);
  const windowName = day ? `${formatDate(dayDate(Math.max(1, day - 6)))} – ${formatDate(dayDate(day))}` : "Awaiting first publication";
  const selectedName = clients.find(({ id }) => id === selected)?.name;

  return <>
    <header className="masthead">
      <div className="brand"><Signal /><span>AML <strong>Investigator</strong></span></div>
      <nav aria-label="Workspace"><a href="/world" aria-current="page">World</a><a href="/">Analyst <span aria-hidden="true">↗</span></a></nav>
      <span className="environment"><i aria-hidden="true" /> Local simulation</span>
    </header>
    <main className="workspace">
      <section className="workspace-header" aria-labelledby="workspace-title">
        <div><p className="eyebrow">World / Transaction observatory</p><h1 id="workspace-title">Payment network<span>.</span></h1></div>
        <div className="headline-data">
          <div><span className="eyebrow">Published day</span><strong>{latest ? String(latest).padStart(2, "0") : "—"}</strong><small>{latest ? formatDate(dayDate(latest)) : "No world yet"}</small></div>
          <div><span className="eyebrow">Investigation spend</span><strong>{world ? usd(world.spent_usd) : "—"}</strong><small>{world ? `of ${usd(world.limit_usd)} limit` : "Spend limit"}</small></div>
          {world && <button className="primary" type="button" onClick={() => setPanel("simulate")}>{busy ? "View run" : "Simulate"} <span aria-hidden="true">↗</span></button>}
        </div>
      </section>
      {worldError && <div className="notice error" role="alert">{worldError} <button type="button" className="text-button" onClick={() => void refreshWorld()}>Retry World</button></div>}
      {world?.thresholds_banner && <p className="notice">{world.thresholds_banner}</p>}
      {busy && <p className="run-banner" role="status">Simulation running <span>· Reviewing published day {day}</span></p>}
      {jobError && <p className="error" role="alert">Simulation status unavailable. Retrying. {jobError}</p>}
      <div className="workspace-grid">
        <section className="network-panel" aria-labelledby="network-title">
          <div className="section-heading network-heading">
            <div><p className="eyebrow">01 / Flow map</p><h2 id="network-title">{selectedName ?? "Network overview"}</h2><p className="window-date">{windowName}</p></div>
            <div className="network-metrics">
              <div><span className="eyebrow">Payments</span><strong>{network ? count(paymentCount) : "—"}</strong></div>
              <div><span className="eyebrow">Volume</span><strong>{network ? eur(paymentEur) : "—"}</strong></div>
            </div>
          </div>
          <div className="network-canvas" aria-busy={!network && !graphError && (!world || Boolean(day))}>
            {network ? <Graph network={network} blocked={blocked} selected={selected} select={selectClient} outlined={outlined} /> :
              <div className="graph-placeholder"><Signal /><h3>{graphError ? "Map unavailable" : day || !world ? "Loading the network" : "The network is quiet"}</h3>
                <p>{graphError || (day || !world ? "Reading the published payment window." : "Create a world to see clients and their opening payment history.")}</p>
                {graphError && <button type="button" className="secondary" onClick={() => setGraphRetry(graphRetry + 1)}>Retry map</button>}
              </div>}
          </div>
          <div className="network-footer">
            <div className="legend" aria-label="Map legend">
              <span><i className="legend-client" /> Client</span><span><i className="legend-external" /> External kind</span>
              <span><i className="legend-blocked">×</i> Blocked</span>
              {selected ? <><span><i className="legend-in">→</i> Incoming</span><span><i className="legend-out">→</i> Outgoing</span></>
                : <span><i className="legend-direction">→</i> Payment direction</span>}
              {evaluator && <span><i className="legend-evaluator" /> Evaluator outline</span>}
            </div>
            {selected && <button className="text-button" type="button" onClick={() => setSelected(null)}>Clear focus</button>}
          </div>
        </section>
        <aside className="inspector" aria-label="World controls and client inspector">
          <div className="panel-tabs" aria-label="Inspector view">
            <button type="button" aria-pressed={panel === "clients" && Boolean(latest)} disabled={!latest} onClick={() => setPanel("clients")}>Inspector</button>
            <button type="button" aria-pressed={panel === "simulate" || !latest} onClick={() => setPanel("simulate")}>Simulation</button>
          </div>
          <div className="inspector-body">
            {world && (panel === "simulate" || !latest) ? <>
              <p className="eyebrow">Controls / World</p><h2>{latest ? "Advance the world" : "Create a world"}</h2>
              <SimulatePanel job={job} pending={pending !== null} busy={busy} world={world} start={setPending} reset={reset} />
            </> : latest ? <>
              <p className="eyebrow">Directory / Clients</p>
              <div className="inspector-heading"><h2>{selected ? "Client profile" : "Explore clients"}</h2>
                {selected && <button type="button" className="text-button" onClick={() => setSelected(null)}>All clients</button>}
              </div>
              {selected ? (detail?.id === selected
                ? <ClientCard client={detail} windowDay={day} blocked={blocked.has(selected)} />
                : detailError ? <p className="error" role="alert">{detailError} <button type="button" className="text-button" onClick={() => setDetailRetry(detailRetry + 1)}>Retry client</button></p>
                : <p className="quiet" role="status">Loading client profile…</p>)
                : <ClientList clients={clients} selected={selected} select={selectClient} blocked={blocked} />}
            </> : <p className="quiet">Connecting to the World API…</p>}
          </div>
          {latest > 0 && <div className="inspector-footer">
            <button type="button" className="text-button evaluator-toggle" aria-pressed={Boolean(evaluator)} disabled={overlayLoading}
              onClick={() => void toggleEvaluator()}><i aria-hidden="true" /> Evaluator overlay <span>{overlayLoading ? "…" : evaluator ? "On" : "Off"}</span></button>
            {overlayError && <p className="error" role="alert">{overlayError}</p>}
          </div>}
        </aside>
        <Timeline weeks={weeks} day={day} latest={latest} firstLive={world?.first_live_day ?? 22} selectDay={setPinned} />
      </div>
    </main>
  </>;
}

createRoot(document.getElementById("root")!).render(<App />);
