import { count, dayDate, eur, formatDate, type Week } from "./api";

export function Timeline({ weeks, day, latest, firstLive, selectDay }: {
  weeks: Week[]; day: number; latest: number; firstLive: number; selectDay: (day: number | null) => void;
}) {
  const current = Math.ceil(day / 7);
  const peak = Math.max(1, ...weeks.map(({ payments }) => payments));
  return <section className="timeline" aria-label="Payment timeline">
    <div className="section-heading timeline-heading">
      <div><p className="eyebrow">02 / Time window</p><h2>Activity timeline</h2></div>
      {latest > 0 && <div className="day-controls">
        <button type="button" aria-label="Previous day" disabled={day <= 1} onClick={() => selectDay(day - 1)}>←</button>
        <label htmlFor="published-day">Day <b>{day}</b> <span>/ {latest}</span></label>
        <button type="button" aria-label="Next day" disabled={day >= latest} onClick={() => selectDay(day + 1)}>→</button>
        <button type="button" className="text-button" onClick={() => selectDay(null)}>Latest</button>
      </div>}
    </div>
    {latest > 0 ? <>
      <input id="published-day" className="day-range" type="range" min={1} max={latest} value={day}
        aria-valuetext={`Day ${day}, ${formatDate(dayDate(day))}`} onChange={(event) => selectDay(event.currentTarget.valueAsNumber)} />
      <div className="weeks" style={{ gridTemplateColumns: `repeat(${Math.max(1, weeks.length)}, minmax(95px, 1fr))` }}>
        {weeks.map((week) => {
          const end = Math.min(week.week * 7, latest);
          return <button key={week.week} type="button" className="week" aria-pressed={week.week === current} onClick={() => selectDay(end)}
            title={`${count(week.payments)} payments · ${eur(week.eur)} · ${count(week.alerts)} alerts · ${count(week.blocks)} blocks`}>
            <span className="week-title">W{String(week.week).padStart(2, "0")} <span>{end < firstLive ? "History" : "Live"}</span></span>
            <span className="week-bar" aria-hidden="true"><i style={{ width: `${week.payments / peak * 100}%` }} /></span>
            <span className="week-stats">{count(week.payments)} pmts <span>{count(week.alerts)} alerts</span></span>
          </button>;
        })}
      </div>
      {weeks.length === 0 && <p className="quiet">No weekly payment totals for the published days.</p>}
    </> : <p className="quiet">Weekly payment activity appears after a world is created.</p>}
  </section>;
}
