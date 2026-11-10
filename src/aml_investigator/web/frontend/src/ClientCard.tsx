import { count, eur, formatDate, label, type ClientDetail } from "./api";

export function ClientCard({ client, windowDay, blocked }: { client: ClientDetail; windowDay: number; blocked: boolean }) {
  return <article className="client-card">
    <div className="profile-title">
      <span className="eyebrow">{label(client.persona)} / {client.id}</span>
      <h3>{client.name}</h3>
      <p>{client.occupation ?? "Occupation unavailable"}</p>
      <span className={blocked ? "blocked-tag" : "active-tag"}>{blocked ? "Blocked" : "Active"} on day {windowDay}</span>
    </div>
    <div className="profile-metrics">
      <div><span className="eyebrow">Latest balance</span><strong>{eur(client.balance)}</strong></div>
      <div><span className="eyebrow">Declared / month</span><strong>{eur(client.declared_income)}</strong></div>
    </div>
    <details open>
      <summary>Profile &amp; accounts</summary>
      {client.description && <p>{client.description}</p>}
      <dl>
        {client.since && <><dt>Customer since</dt><dd>{formatDate(client.since)}</dd></>}
        {client.purpose && <><dt>Purpose</dt><dd>{client.purpose}</dd></>}
        {client.expected_activity && <><dt>Expected activity</dt><dd>{client.expected_activity}</dd></>}
        <dt>Accounts</dt><dd>{client.accounts.join(", ")}</dd>
        {client.blocked_from && <><dt>Blocked from</dt><dd>{formatDate(client.blocked_from)}</dd></>}
      </dl>
    </details>
    <details>
      <summary>Recent payments <span>{count(client.payments.length)}</span></summary>
      <ul className="detail-list">
        {client.payments.map((payment) => <li key={payment.txn_id}>
          <div><strong>{eur(payment.eur)}</strong><small>{formatDate(payment.ts)}</small></div>
          <small>{payment.from_account} → {payment.to_account}</small>
          <p>{payment.category}{payment.reference ? ` · ${payment.reference}` : ""}</p>
        </li>)}
      </ul>
      {client.payments.length === 0 && <p className="quiet">No payments published.</p>}
    </details>
    <details>
      <summary>Spending by week <span>{count(client.spending.length)}</span></summary>
      <ul className="detail-list">
        {client.spending.map((item) => <li key={JSON.stringify([item.week, item.category])}>
          <div><strong>{item.category}</strong><small>Week {item.week}</small></div><span>{eur(item.eur)}</span>
        </li>)}
      </ul>
      {client.spending.length === 0 && <p className="quiet">No spending published.</p>}
    </details>
    <details>
      <summary>Alerts <span>{count(client.alerts.length)}</span></summary>
      <ul className="detail-list">
        {client.alerts.map((alert) => <li key={alert.alert_id}>
          <div><a href={`/alerts/${encodeURIComponent(alert.alert_id)}`}>Alert {alert.alert_id} ↗</a><small>{formatDate(alert.alert_date)}</small></div>
          <small>{alert.account_id}</small>
        </li>)}
      </ul>
      {client.alerts.length === 0 && <p className="quiet">No alerts published.</p>}
    </details>
  </article>;
}
