import { describe, expect, it } from 'vitest';
import { agentTaskIdForNode, buildNodeAgentPrompt, buildWorkflowFlow, isRerunBlocked, workflowCalendarNotice, workflowViewContext } from './WorkflowBoard';
import type { WorkflowEvent, WorkflowGraph, WorkflowGraphNode } from '../types';

describe('WorkflowBoard helpers', () => {
  it('builds concise node context for copying into Codex', () => {
    const node: WorkflowGraphNode = {
      id: 'risk_validation',
      name: '风控校验',
      enabled: true,
      locked: true,
      status: 'success',
      summary: '2 candidates passed',
    };
    const events: WorkflowEvent[] = [
      {
        event_id: 'wf_1',
        run_id: 'paper_test',
        phase: 'premarket',
        node_id: 'risk_validation',
        node_name: '风控校验',
        status: 'success',
        summary: '仓位通过',
      },
    ];

    const prompt = buildNodeAgentPrompt(node, events);

    expect(prompt).toContain('节点=风控校验(risk_validation)');
    expect(prompt).toContain('最近事件=success/premarket: 仓位通过');
  });

  it('blocks rerun for intraday execution and ledger nodes', () => {
    expect(isRerunBlocked('intraday_event_stream')).toBe(true);
    expect(isRerunBlocked('fastlane_tick')).toBe(true);
    expect(isRerunBlocked('market_snapshot')).toBe(false);
  });

  it('maps workflow nodes to scheduled agent task timelines', () => {
    expect(agentTaskIdForNode('agent_research')).toBe('premarket_plan');
    expect(agentTaskIdForNode('trade_attribution')).toBe('postclose_review');
    expect(agentTaskIdForNode('risk_validation')).toBe('');
  });

  it('builds blueprint flow nodes and semantic edges', () => {
    const graph: WorkflowGraph = {
      run_id: 'paper_test',
      is_alive: true,
      nodes: [
        { id: 'market_snapshot', name: '市场快照', enabled: true, locked: false, status: 'success' },
        { id: 'risk_validation', name: '风控校验', enabled: true, locked: true, status: 'running' },
      ],
      edges: [{ from: 'market_snapshot', to: 'risk_validation', kind: 'data', label: '候选', refs: ['plan.buy_candidates'], required: true }],
    };
    const flow = buildWorkflowFlow(graph, [
      {
        event_id: 'wf_1',
        run_id: 'paper_test',
        phase: 'premarket',
        node_id: 'risk_validation',
        node_name: '风控校验',
        status: 'running',
        input_refs: ['plan.buy_candidates'],
        output_refs: ['plan.risk_report'],
      },
    ], 'risk_validation');

    expect(flow.nodes).toHaveLength(2);
    expect(flow.nodes.find((node) => node.id === 'risk_validation')?.data).toMatchObject({
      selected: true,
      current: true,
      waiting: false,
      inputRefs: ['plan.buy_candidates'],
      outputRefs: ['plan.risk_report'],
    });
    expect(flow.edges[0]).toMatchObject({
      source: 'market_snapshot',
      target: 'risk_validation',
      type: 'smoothstep',
      animated: true,
      label: '候选',
      data: {
        kind: 'data',
        refs: ['plan.buy_candidates'],
        required: true,
      },
    });
  });

  it('marks observation mode as waiting without treating selection as runtime', () => {
    const graph: WorkflowGraph = {
      run_id: 'paper_test',
      is_alive: true,
      observation_mode: true,
      observation_reason: '盘中启动且缺少今日盘前计划；仅观察，等待下一次盘前计划窗口',
      nodes: [
        { id: 'state_watcher', name: '状态观察', enabled: true, locked: false, status: 'idle' },
        { id: 'fastlane_tick', name: '盘中快车道', enabled: true, locked: false, status: 'idle' },
      ],
      edges: [{ from: 'state_watcher', to: 'fastlane_tick' }],
    };

    const flow = buildWorkflowFlow(graph, [], 'fastlane_tick');

    expect(flow.nodes.find((node) => node.id === 'state_watcher')?.data).toMatchObject({
      selected: false,
      current: false,
      waiting: true,
    });
    expect(flow.nodes.find((node) => node.id === 'fastlane_tick')?.data).toMatchObject({
      selected: true,
      current: false,
      waiting: false,
    });
    expect(flow.edges[0].animated).toBe(false);
  });

  it('prefers explicit agent research running nodes over stale historical intraday time', () => {
    const graph: WorkflowGraph = {
      run_id: 'paper_2026-06-03T09-40-41',
      is_alive: true,
      observation_mode: true,
      observation_reason: '盘中启动且缺少今日盘前计划；仅观察，等待下一次盘前计划窗口',
      market_status: 'stale',
      calendar_date: '2026-06-09',
      display_date: '2026-06-03',
      data_time: '2026-06-03 11:17:06',
      nodes: [
        { id: 'agent_research', name: '自主 Agent 研判', enabled: true, locked: false, status: 'running' },
        { id: 'plan_writer', name: '计划写入', enabled: true, locked: true, status: 'running' },
        { id: 'state_watcher', name: '状态观察', enabled: true, locked: false, status: 'idle' },
        { id: 'fastlane_tick', name: '盘中快车道', enabled: true, locked: false, status: 'idle' },
      ],
      edges: [
        { from: 'agent_research', to: 'plan_writer' },
        { from: 'state_watcher', to: 'fastlane_tick' },
      ],
    };

    const flow = buildWorkflowFlow(graph, [], '');

    expect(flow.nodes.find((node) => node.id === 'agent_research')?.data).toMatchObject({
      current: true,
      waiting: false,
    });
    expect(flow.nodes.find((node) => node.id === 'fastlane_tick')?.data).toMatchObject({
      current: false,
      waiting: false,
    });
    expect(flow.edges.find((edge) => edge.id === 'state_watcher-fastlane_tick')?.animated).toBe(false);
  });

  it('does not infer fastlane from stale historical data_time without running nodes', () => {
    const graph: WorkflowGraph = {
      run_id: 'paper_2026-06-03T09-40-41',
      is_alive: true,
      observation_mode: false,
      market_status: 'stale',
      calendar_date: '2026-06-09',
      display_date: '2026-06-03',
      data_time: '2026-06-03 13:25:00',
      nodes: [
        { id: 'state_watcher', name: '状态观察', enabled: true, locked: false, status: 'idle' },
        { id: 'fastlane_tick', name: '盘中快车道', enabled: true, locked: false, status: 'success' },
      ],
      edges: [{ from: 'state_watcher', to: 'fastlane_tick' }],
    };

    const flow = buildWorkflowFlow(graph, [], '');

    expect(flow.nodes.find((node) => node.id === 'fastlane_tick')?.data.current).toBe(false);
    expect(flow.edges[0].animated).toBe(false);
  });

  it('highlights the final postclose node after a run has stopped', () => {
    const graph: WorkflowGraph = {
      run_id: 'paper_2026-06-03T09-40-41',
      is_alive: false,
      run_status: 'stopped',
      data_time: '2026-06-09 15:05:00',
      nodes: [
        { id: 'fastlane_tick', name: '盘中快车道', enabled: true, locked: false, status: 'idle' },
        { id: 'intraday_event_stream', name: '关键事件流', enabled: true, locked: true, status: 'success' },
        { id: 'daily_report', name: '盘后日报', enabled: true, locked: false, status: 'success' },
      ],
      edges: [
        { from: 'fastlane_tick', to: 'intraday_event_stream' },
        { from: 'intraday_event_stream', to: 'daily_report' },
      ],
    };
    const events: WorkflowEvent[] = [
      {
        event_id: 'wf_intraday',
        run_id: graph.run_id,
        phase: 'intraday',
        node_id: 'intraday_event_stream',
        node_name: '关键事件流',
        status: 'success',
        started_at: '2026-06-09T14:53:35',
        ended_at: '2026-06-09T14:53:35',
        summary: '关键 tick: 监控 4 只，触发 1 条',
      },
      {
        event_id: 'wf_daily',
        run_id: graph.run_id,
        phase: 'postclose',
        node_id: 'daily_report',
        node_name: '盘后日报',
        status: 'success',
        started_at: '2026-06-09T15:00:00',
        ended_at: '2026-06-09T15:00:00',
        summary: '盘后报告完成，成交 4 笔',
      },
    ];

    const flow = buildWorkflowFlow(graph, events, '');

    expect(flow.nodes.find((node) => node.id === 'daily_report')?.data.current).toBe(true);
    expect(flow.nodes.find((node) => node.id === 'fastlane_tick')?.data.current).toBe(false);
  });

  it('highlights fastlane while a live paper run is in trading hours even without running node events', () => {
    const graph: WorkflowGraph = {
      run_id: 'paper_test',
      is_alive: true,
      observation_mode: false,
      data_time: '2026-06-09 13:25:00',
      nodes: [
        { id: 'plan_writer', name: '盘前计划', enabled: true, locked: true, status: 'success' },
        { id: 'state_watcher', name: '状态观察', enabled: true, locked: false, status: 'idle' },
        { id: 'fastlane_tick', name: '盘中快车道', enabled: true, locked: false, status: 'success' },
        { id: 'intraday_event_stream', name: '关键事件流', enabled: true, locked: true, status: 'success' },
      ],
      edges: [
        { from: 'plan_writer', to: 'state_watcher' },
        { from: 'state_watcher', to: 'fastlane_tick' },
        { from: 'fastlane_tick', to: 'intraday_event_stream' },
      ],
    };
    const events: WorkflowEvent[] = [
      {
        event_id: 'wf_intraday',
        run_id: 'paper_test',
        phase: 'intraday',
        node_id: 'intraday_event_stream',
        node_name: '关键事件流',
        status: 'success',
        started_at: '2026-06-09T13:00:01',
        ended_at: '2026-06-09T13:00:01',
        summary: '关键 tick: candidate_buy',
      },
    ];

    const flow = buildWorkflowFlow(graph, events, '');

    expect(flow.nodes.find((node) => node.id === 'fastlane_tick')?.data).toMatchObject({
      current: true,
      waiting: false,
    });
    expect(flow.edges.find((edge) => edge.id === 'state_watcher-fastlane_tick')?.animated).toBe(true);
    expect(flow.edges.find((edge) => edge.id === 'fastlane_tick-intraday_event_stream')?.animated).toBe(true);
  });

  it('keeps autonomous agent research as the only premarket consumer of market snapshot', () => {
    const graph: WorkflowGraph = {
      run_id: 'paper_test',
      nodes: [
        { id: 'market_snapshot', name: '市场快照', enabled: true, locked: false, status: 'success', output_refs: ['artifact.market.snapshot'] },
        { id: 'agent_research', name: '自主 Agent 研判', enabled: true, locked: false, status: 'idle', input_refs: ['artifact.market.snapshot', 'account.state', 'rule.skills'] },
        { id: 'risk_validation', name: '风控校验', enabled: true, locked: true, status: 'idle', input_refs: ['artifact.agent.plan_draft'] },
      ],
      edges: [
        { from: 'market_snapshot', to: 'agent_research', kind: 'data', label: 'Agent 任务上下文', refs: ['artifact.market.snapshot', 'account.state', 'rule.skills'], required: true },
        { from: 'agent_research', to: 'risk_validation', kind: 'data', label: 'Agent 计划草案', refs: ['artifact.agent.plan_draft', 'artifact.agent.research'], required: true },
      ],
    };

    const flow = buildWorkflowFlow(graph, [], 'market_snapshot');
    const marketTargets = flow.edges
      .filter((edge) => edge.source === 'market_snapshot')
      .map((edge) => edge.target)
      .sort();

    expect(marketTargets).toEqual(['agent_research']);
    expect(flow.edges.find((edge) => edge.source === 'market_snapshot' && edge.target === 'risk_validation')).toBeUndefined();
    expect(flow.edges[0].label).toBe('Agent 任务上下文');
  });

  it('does not infer edge refs from node inputs and outputs when edge refs are absent', () => {
    const graph: WorkflowGraph = {
      run_id: 'paper_test',
      nodes: [
        { id: 'upstream', name: '上游', enabled: true, locked: false, status: 'success', output_refs: ['guessed.output'] },
        { id: 'downstream', name: '下游', enabled: true, locked: false, status: 'idle', input_refs: ['guessed.output'] },
      ],
      edges: [{ from: 'upstream', to: 'downstream', kind: 'sequence', label: '完成后触发' }],
    };

    const flow = buildWorkflowFlow(graph, [], '');

    expect(flow.edges[0].label).toBe('完成后触发');
    expect(flow.edges[0].data?.kind).toBe('sequence');
    expect(flow.edges[0].data?.refs).toEqual([]);
  });

  it('lays out the new premarket agent workflow in sequence', () => {
    const graph: WorkflowGraph = {
      run_id: 'paper_test',
      nodes: [
        { id: 'market_snapshot', name: '市场快照', enabled: true, locked: false, status: 'success' },
        { id: 'agent_research', name: '自主 Agent 研判', enabled: true, locked: false, status: 'success' },
        { id: 'risk_validation', name: '风控校验', enabled: true, locked: true, status: 'success' },
      ],
      edges: [],
    };

    const flow = buildWorkflowFlow(graph, [], '');
    const pos = (id: string) => flow.nodes.find((node) => node.id === id)?.position || { x: 0, y: 0 };

    expect(pos('agent_research').x).toBeGreaterThan(pos('market_snapshot').x);
    expect(pos('risk_validation').x).toBeGreaterThan(pos('agent_research').x);
    expect(pos('agent_research').y).toBe(pos('market_snapshot').y);
  });

  it('uses dragged node positions when layout overrides are provided', () => {
    const graph: WorkflowGraph = {
      run_id: 'paper_test',
      nodes: [
        { id: 'market_snapshot', name: '市场快照', enabled: true, locked: false, status: 'success' },
      ],
      edges: [],
    };

    const flow = buildWorkflowFlow(graph, [], 'market_snapshot', '', {
      market_snapshot: { x: 420, y: 160 },
    });

    expect(flow.nodes[0].position).toEqual({ x: 420, y: 160 });
  });

  it('uses backend calendar metadata instead of guessing workflow display day', () => {
    const graph: WorkflowGraph = {
      run_id: 'paper_2026-06-04T09-30-00',
      calendar_date: '2026-06-06',
      display_date: '2026-06-04',
      is_trading_day: false,
      market_status: 'closed',
      market_message: '今日休市（周六休市），当前展示最近一次模拟盘记录：2026-06-04',
      nodes: [],
      edges: [],
    };

    const context = workflowViewContext(graph, { updated: '2026-06-04T08:30:00' });
    const notice = workflowCalendarNotice(graph);

    expect(context.tradeDate).toBe('2026-06-04');
    expect(context.calendarLabel).toBe('2026-06-06 / 休市');
    expect(notice).toEqual({
      kind: 'closed',
      text: '今日休市（周六休市），当前展示最近一次模拟盘记录：2026-06-04',
    });
  });
});
