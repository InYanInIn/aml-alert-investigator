import { useEffect, useState, type FormEvent } from "react";
import { api, count, errorText, formatDate, label, usd, type Days, type Estimate, type Job, type System, type World } from "./api";

export function WorldSetup({ world, busy, done }: { world: World; busy: boolean; done: () => Promise<void> }) {
  const [seed, setSeed] = useState(1001);
  const [counts, setCounts] = useState(world.persona_counts);
  const [confirm, setConfirm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (world.day > 0 && !confirm) {
      setConfirm(true);
      return;
    }
    setSaving(true);
    setError("");
    try {
      await api.create(seed, counts);
      await done();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setSaving(false);
      setConfirm(false);
    }
  }

  return <form className="world-setup" onSubmit={submit}>
    <p className="quiet">Generate a synthetic client population and its opening payment history.</p>
    <fieldset disabled={busy || saving || confirm}>
      <label className="setup-field">Seed<input type="number" required min={0} step={1} value={Number.isNaN(seed) ? "" : seed}
        onChange={(event) => setSeed(event.currentTarget.valueAsNumber)} /></label>
      <p className="eyebrow">Accounts by persona</p>
      {Object.entries(counts).map(([persona, value]) => <label className="setup-field" key={persona}>
        {label(persona)}
        <input type="number" required min={0} step={1} value={Number.isNaN(value) ? "" : value}
          onChange={(event) => setCounts({ ...counts, [persona]: event.currentTarget.valueAsNumber })} />
      </label>)}
    </fieldset>
    {confirm && <div className="notice" role="alert">
      <strong>Replace the current world?</strong>
      <p>All {count(world.day)} published days and their investigation history will be removed. Historical spend of {usd(world.spent_usd)} is retained.</p>
      <button type="button" className="text-button" disabled={saving} onClick={() => setConfirm(false)}>Cancel</button>
    </div>}
    {error && <p className="error" role="alert">{error}</p>}
    <button type="submit" className={world.day === 0 || confirm ? "primary" : "secondary"} disabled={busy || saving}>
      {saving ? "Generating history…" : world.day === 0 ? "Create world" : confirm ? "Confirm reset" : "Reset world"}
    </button>
  </form>;
}

export function SimulatePanel({ job, pending, busy, world, start, reset }: {
  job: Job | null; pending: boolean; busy: boolean; world: World; start: (period: number) => void; reset: () => Promise<void>;
}) {
  const [days, setDays] = useState<Days>(7);
  const [system, setSystem] = useState<System>("azure_profile");
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [estimateError, setEstimateError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [starting, setStarting] = useState(false);
  const [refreshEstimate, setRefreshEstimate] = useState(0);

  useEffect(() => {
    if (!world.day || busy) return;
    const controller = new AbortController();
    setEstimate(null);
    setEstimateError("");
    api.estimate(days, system, controller.signal).then(setEstimate).catch((failure: unknown) => {
      if (!controller.signal.aborted) setEstimateError(errorText(failure));
    });
    return () => controller.abort();
  }, [days, system, world.day, world.spent_usd, busy, refreshEstimate]);

  async function run(event: FormEvent) {
    event.preventDefault();
    setSubmitError("");
    setStarting(true);
    try {
      const result = await api.simulate(days, system);
      start(result.period_id);
    } catch (failure) {
      setSubmitError(errorText(failure));
    } finally {
      setStarting(false);
    }
  }

  if (!world.day) return <WorldSetup world={world} busy={busy} done={reset} />;

  return <>
    {(job || pending) && <div className="job-notice" role="status">
      <p className="eyebrow">{job?.status === "failed" ? "Run interrupted" : busy ? "Run in progress" : "Last simulation"}</p>
      {job && <><strong>Period {job.period_id} · {formatDate(job.day)}</strong><p>{count(job.investigations)} investigations · {usd(job.cost_usd)}</p></>}
      {job?.error && <p className="error">{job.error}</p>}
    </div>}
    <form className="simulation-form" onSubmit={run}>
      <fieldset disabled={busy || starting}>
        <legend>Advance by</legend>
        <div className="day-options">{([1, 7, 30] as const).map((value) => <label key={value}>
          <input type="radio" name="days" value={value} checked={days === value} onChange={() => { setEstimate(null); setDays(value); }} />
          <span>{value} {value === 1 ? "day" : "days"}</span>
        </label>)}</div>
      </fieldset>
      <fieldset disabled={busy || starting}>
        <legend>Decision system</legend>
        <div className="model-options">
          {([["azure_profile", "Azure Profile", "Paid · AI investigation"], ["b4", "B4 rules", "Free · rule-based decisions"]] as const).map(([value, name, copy]) => (
            <label key={value}><input type="radio" name="system" value={value} checked={system === value}
              onChange={() => { setEstimate(null); setSystem(value); }} />
              <span><strong>{name}</strong><small>{copy}</small></span>
            </label>
          ))}
        </div>
      </fieldset>
      {!busy && <div className="estimate" aria-live="polite">
        {estimate ? <>
          <span>{count(estimate.expected_alerts)} expected alerts · cap {count(estimate.cap)}</span>
          <strong>{usd(estimate.cost_usd)} est.</strong>
          {!estimate.within_limit && <p className="error">This run would exceed the spend limit. Select fewer days or switch to B4 rules.</p>}
        </> : estimateError ? <><p className="error">{estimateError}</p><button type="button" className="text-button" onClick={() => setRefreshEstimate(refreshEstimate + 1)}>Retry estimate</button></>
          : <span>Estimating run…</span>}
      </div>}
      {submitError && <p className="error" role="alert">{submitError}</p>}
      <button className="primary" type="submit" disabled={busy || starting || !estimate?.within_limit}>
        {busy || starting ? "Simulation in progress" : "Run simulation"} <span aria-hidden="true">↗</span>
      </button>
    </form>
    <details className="reset-details"><summary>World settings</summary><WorldSetup world={world} busy={busy} done={reset} /></details>
  </>;
}
