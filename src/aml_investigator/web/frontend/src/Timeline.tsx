import { countLabel, dayDate, eur, formatDate, formatWindow, type Week } from "./api";

export function Timeline({ weeks, day, latest, firstLive, pinned, selectDay }: {
  weeks: Week[]; day: number; latest: number; firstLive: number; pinned: boolean; selectDay: (day: number | null) => void;
}) {
  const current = Math.ceil(day / 7);
  const selectedWeek = weeks.find(({ week }) => week === current);
  const peak = Math.max(1, ...weeks.map(({ payments }) => payments));
  return <section className="timeline" aria-label="Payment timeline">
    <div className="section-heading timeline-heading">
      <div><h2>Timeline</h2>
        {selectedWeek && <p className="week-summary">{countLabel(selectedWeek.payments, "payment")} <span>·</span> {countLabel(selectedWeek.alerts, "alert")}</p>}
      </div>
      {latest > 0 && <div className="day-controls">
        <button type="button" aria-label="Previous day" disabled={day <= 1} onClick={() => selectDay(day - 1)}>←</button>
        <label htmlFor="published-day">Day <b>{day}</b> <span>/ {latest}</span></label>
        <button type="button" aria-label="Next day" disabled={day >= latest} onClick={() => selectDay(day + 1)}>→</button>
        {pinned && <button type="button" className="text-button" onClick={() => selectDay(null)}>Latest</button>}
      </div>}
    </div>
    {latest > 0 ? <>
      <input id="published-day" className="day-range" type="range" min={1} max={latest} value={day}
        aria-valuetext={`Day ${day}, ${formatDate(dayDate(day))}`} onChange={(event) => selectDay(event.currentTarget.valueAsNumber)} />
      <div className="weeks" style={{ gridTemplateColumns: `repeat(${Math.max(1, weeks.length)}, minmax(65px, 1fr))` }}>
        {weeks.map((week) => {
          const start = (week.week - 1) * 7 + 1;
          const end = Math.min(week.week * 7, latest);
          const description = `${formatWindow(start, end)}, ${end < firstLive ? "history" : "live"} · ${countLabel(week.payments, "payment")} · ${eur(week.eur)} · ${countLabel(week.alerts, "alert")} · ${countLabel(week.blocks, "block")}`;
          return <button key={week.week} type="button" className={`week${end < firstLive ? " history" : ""}`} aria-pressed={week.week === current} onClick={() => selectDay(end)}
            title={description} aria-label={description}>
            <span className="week-bar" aria-hidden="true"><i style={{ height: `${week.payments / peak * 100}%` }} /></span>
            <span className="week-title">{formatWindow(start, start)}{week.alerts > 0 && <i aria-hidden="true" />}</span>
          </button>;
        })}
      </div>
      {weeks.length === 0 && <p className="quiet">No weekly payment totals for the published days.</p>}
    </> : <p className="quiet">Weekly payment activity appears after a world is created.</p>}
  </section>;
}
