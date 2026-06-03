import type { LedgerEntry, PlanData, WorkflowEvent } from '../types';

export function ReviewBoard({ events, ledger, plan }: { events: WorkflowEvent[]; ledger: LedgerEntry[]; plan: PlanData }) {
  const errors = events.filter((event) => event.status === 'error');
  const trades = ledger.filter((row) => row.symbol || row.code);

  return (
    <section className="review-board">
      <article className="info-card">
        <header><strong>计划 vs 执行</strong><span>{plan.market_bias || '--'}</span></header>
        <p>候选 {(plan.buy_candidates || []).length} 只，成交 {trades.length} 笔</p>
      </article>
      <article className="info-card">
        <header><strong>风险事件</strong><span>{errors.length}</span></header>
        {errors.slice(0, 6).map((event) => <p key={event.event_id}>{event.node_name}: {event.summary}</p>)}
        {!errors.length ? <p>暂无失败节点</p> : null}
      </article>
      <article className="info-card">
        <header><strong>最近成交</strong><span>{trades.length}</span></header>
        {trades.slice(0, 6).map((row, index) => (
          <p key={row.seq || index}>{row.time} {row.symbol || row.code} {row.decision || row.action} @{row.price}</p>
        ))}
        {!trades.length ? <p>暂无成交</p> : null}
      </article>
    </section>
  );
}
