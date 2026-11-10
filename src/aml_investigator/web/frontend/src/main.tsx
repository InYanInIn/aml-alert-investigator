import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, compactEur, count, dayDate, errorText, eur, formatDate, formatWindow, usd, type Client, type ClientDetail, type Job, type Network, type Week, type World } from "./api";
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
  const inspector = useRef<HTMLElement>(null);
  const clientSearch = useRef<HTMLInputElement>(null);
  const backToClients = useRef<HTMLButtonElement>(null);
  const mapHeading = useRef<HTMLDivElement>(null);
  const previousClient = useRef<string | null>(null);
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
        const next = current ? `${current.period_id}:${current.status}:${current.day}` : "";
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

  useEffect(() => {
    if (panel === "clients" && selected !== previousClient.current) {
      (selected ? backToClients.current : clientSearch.current)?.focus({ preventScroll: true });
    }
    previousClient.current = selected;
  }, [panel, selected]);

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

  function showInspector() {
    inspector.current?.scrollIntoView({ block: "nearest" });
  }

  const network = graph?.data;
  const chartDay = graph?.day ?? day;
  const chartPending = Boolean(day) && chartDay !== day && !graphError;
  const outlined = useMemo(() => evaluator && network
    ? new Set(network.accounts.filter(({ id }) => evaluator.has(id)).map(({ client_id }) => client_id)) : emptyIds, [evaluator, network]);
  const blocked = useMemo(() => {
    const date = dayDate(chartDay).toISOString().slice(0, 10);
    return new Set(clients.filter(({ blocked_from }) => blocked_from !== null && blocked_from <= date).map(({ id }) => id));
  }, [clients, chartDay]);
  const arcs = network?.arcs ?? [];
  const payments = selected ? arcs.filter(({ from_client, to_client }) => from_client === selected || to_client === selected) : arcs;
  const paymentCount = payments.reduce((sum, flow) => sum + flow.count, 0);
  const paymentEur = payments.reduce((sum, flow) => sum + flow.eur, 0);
  const windowName = chartDay ? formatWindow(Math.max(1, chartDay - 6), chartDay) : "No published data";
  const selectedName = clients.find(({ id }) => id === selected)?.name;

  return <>
    <header className="masthead">
      <div className="brand"><Signal /><span>AML <strong>Investigator</strong></span></div>
      <nav aria-label="Workspace"><a href="/world" aria-current="page">World</a><a href="/">Analyst</a></nav>
    </header>
    <main className="workspace">
      <section className="workspace-header" aria-labelledby="workspace-title">
        <div><h1 id="workspace-title">Payment network</h1>
          <p className="world-date">{latest ? `Day ${latest} · ${formatDate(dayDate(latest))}` : "Local simulation"}</p>
        </div>
        <div className="headline-data">
          {world && <><div className="budget"><span>Spend</span><strong>{usd(world.spent_usd)}</strong><span>/ {usd(world.limit_usd)}</span></div>
            <button className="primary" type="button" onClick={() => { setPanel("simulate"); showInspector(); }}>{busy ? "View run" : latest ? "Simulate" : "Set up"}</button>
          </>}
        </div>
      </section>
      {worldError && <div className="notice error" role="alert">{worldError} <button type="button" className="text-button" onClick={() => void refreshWorld()}>Retry World</button></div>}
      {world?.thresholds_banner && <p className="notice">{world.thresholds_banner}</p>}
      {jobError && <p className="error" role="alert">Simulation status unavailable. Retrying. {jobError}</p>}
      <div className="workspace-grid">
        <section className="network-panel" aria-labelledby="network-title">
          <div ref={mapHeading} className="section-heading network-heading">
            <div className="map-window">
              <h2 id="network-title" className="sr-only">{selectedName ? `Payments for ${selectedName}` : "Flow map"}</h2>
              {selected && <button className="secondary map-back" type="button" onClick={() => setSelected(null)}>← Back to network</button>}
              <span>{windowName}</span>
              {chartPending ? <span className="quiet" role="status">Updating…</span>
                : busy && <span className="run-state" role="status">Running</span>}
            </div>
            <div className="network-metrics">
              {network && <><span><strong>{count(paymentCount)}</strong> {paymentCount === 1 ? "payment" : "payments"}</span><strong title={eur(paymentEur)}>{compactEur(paymentEur)}</strong></>}
            </div>
          </div>
          {network && graphError && <p className="map-error error" role="alert">{graphError} <button type="button" className="text-button" onClick={() => setGraphRetry(graphRetry + 1)}>Retry map</button></p>}
          <div className="network-canvas" aria-busy={chartPending || (!network && !graphError && (!world || Boolean(day)))}>
            {network ? <Graph network={network} blocked={blocked} selected={selected} select={(id) => {
              selectClient(id);
              mapHeading.current?.scrollIntoView({ block: "nearest" });
            }} outlined={outlined} /> :
              <div className="graph-placeholder"><Signal /><h3>{graphError ? "Map unavailable" : day || !world ? "Loading payments…" : "No world yet"}</h3>
                {graphError && <p>{graphError}</p>}
                {graphError && <button type="button" className="secondary" onClick={() => setGraphRetry(graphRetry + 1)}>Retry map</button>}
              </div>}
          </div>
          {network && <div className="network-footer">
            <div className="legend" aria-label="Map legend">
              {selected ? <><span><i className="legend-in" /> Received</span><span><i className="legend-out" /> Sent</span></>
                : <><span><i className="legend-client" /> Client</span><span><i className="legend-external" /> External</span></>}
              {selected && payments.some(({ from_client, to_client }) => from_client === to_client) &&
                <span><i className="legend-internal" /> Internal</span>}
              {blocked.size > 0 && <span><i className="legend-blocked">×</i> Blocked</span>}
              {evaluator && <span><i className="legend-evaluator" /> Evaluator outline</span>}
            </div>
            {selected && <button className="text-button map-client-link" type="button" onClick={() => { setPanel("clients"); showInspector(); }}>View client</button>}
          </div>}
        </section>
        <aside ref={inspector} className="inspector" aria-label="Clients and simulation">
          <div className="panel-tabs" aria-label="Side panel">
            <button type="button" aria-pressed={panel === "clients" && Boolean(latest)} disabled={!latest} onClick={() => setPanel("clients")}>Clients</button>
            <button type="button" aria-pressed={panel === "simulate" || !latest} onClick={() => setPanel("simulate")}>{latest ? "Simulation" : "World setup"}</button>
          </div>
          <div className="inspector-body">
            {world && (panel === "simulate" || !latest) ?
              <SimulatePanel job={job} pending={pending !== null} busy={busy} world={world} start={setPending} reset={reset} />
              : latest ? <>
              <div hidden={selected !== null}>
                <ClientList clients={clients} select={selectClient} blocked={blocked} searchRef={clientSearch} />
              </div>
              {selected && <button ref={backToClients} type="button" className="text-button back-to-clients" onClick={() => setSelected(null)}>← All clients</button>}
              {selected && (detail?.id === selected
                ? <ClientCard client={detail} blocked={blocked.has(selected)} />
                : detailError ? <p className="error" role="alert">{detailError} <button type="button" className="text-button" onClick={() => setDetailRetry(detailRetry + 1)}>Retry client</button></p>
                : <p className="quiet" role="status">Loading client profile…</p>)}
            </> : <p className="quiet">Connecting to the World API…</p>}
          </div>
          {latest > 0 && <div className="inspector-footer">
            <button type="button" className="text-button evaluator-toggle" aria-pressed={Boolean(evaluator)} disabled={overlayLoading}
              onClick={() => void toggleEvaluator()}><i aria-hidden="true" /> Evaluator overlay <span>{overlayLoading ? "…" : evaluator ? "On" : "Off"}</span></button>
            {overlayError && <p className="error" role="alert">{overlayError}</p>}
          </div>}
        </aside>
        <Timeline weeks={weeks} day={day} latest={latest} firstLive={world?.first_live_day ?? 22} pinned={pinned !== null} selectDay={setPinned} />
      </div>
    </main>
  </>;
}

createRoot(document.getElementById("root")!).render(<App />);
