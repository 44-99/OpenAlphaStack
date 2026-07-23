import { describe, expect, it } from 'vitest';
import { agentTaskIdForNode, buildNodeAgentPrompt, buildWorkflowFlow, workflowCalendarNotice, workflowViewContext } from './WorkflowBoard';
import type { WorkflowEvent, WorkflowGraph, WorkflowGraphNode } from '../types';

const threeStageGraph = (): WorkflowGraph => ({
  run_id: 'paper_test',
  is_alive: true,
  nodes: [
    { id: 'research', name: 'Research', status: 'success' },
    { id: 'execution', name: 'Execution', status: 'running' },
    { id: 'evaluation', name: 'Evaluation', status: 'idle' },
  ],
  edges: [
    { from: 'research', to: 'execution', kind: 'data', label: 'Published paper plan', refs: ['plan.published'], required: true },
    { from: 'execution', to: 'evaluation', kind: 'data', label: 'State and ledger', refs: ['account.state', 'account.ledger'], required: false },
  ],
});

describe('WorkflowBoard helpers', () => {
  it('builds concise stage context for Codex', () => {
    const node: WorkflowGraphNode = {
      id: 'research', name: 'Research', status: 'success', summary: 'plan published',
    };
    const events: WorkflowEvent[] = [{
      event_id: 'wf_1', run_id: 'paper_test', phase: 'premarket', stage_id: 'research',
      node_id: 'agent_research', node_name: 'Agent research', status: 'success', summary: 'published once',
    }];

    expect(buildNodeAgentPrompt(node, events)).toContain('阶段=Research(research)');
    expect(buildNodeAgentPrompt(node, events)).toContain('published once');
  });

  it('maps only product stages to scheduled Agent tasks', () => {
    expect(agentTaskIdForNode('research')).toBe('premarket_plan');
    expect(agentTaskIdForNode('evaluation')).toBe('postclose_review');
    expect(agentTaskIdForNode('execution')).toBe('');
  });

  it('aggregates historical detailed events under the execution stage', () => {
    const graph = threeStageGraph();
    const events: WorkflowEvent[] = [{
      event_id: 'wf_1', run_id: 'paper_test', phase: 'intraday',
      node_id: 'intraday_event_stream', node_name: 'legacy stream', status: 'running',
      input_refs: ['plan.published'], output_refs: ['account.ledger'],
    }];

    const flow = buildWorkflowFlow(graph, events, 'execution');
    expect(flow.nodes).toHaveLength(3);
    expect(flow.nodes.find((node) => node.id === 'execution')?.data).toMatchObject({
      selected: true,
      current: true,
      eventCount: 1,
      inputRefs: ['plan.published'],
      outputRefs: ['account.ledger'],
    });
    expect(flow.edges).toHaveLength(2);
  });

  it('marks research as waiting when a live run has no actionable plan', () => {
    const graph = threeStageGraph();
    graph.observation_mode = true;
    graph.observation_reason = '盘中启动且缺少今日盘前计划';
    graph.nodes = graph.nodes.map((node) => ({ ...node, status: 'idle' }));

    const flow = buildWorkflowFlow(graph, [], 'execution');
    expect(flow.nodes.find((node) => node.id === 'research')?.data.waiting).toBe(true);
    expect(flow.nodes.find((node) => node.id === 'execution')?.data.current).toBe(false);
  });

  it('lays out Research, Execution and Evaluation in sequence', () => {
    const flow = buildWorkflowFlow(threeStageGraph(), [], '');
    const x = (id: string) => flow.nodes.find((node) => node.id === id)?.position.x || 0;
    expect(x('execution')).toBeGreaterThan(x('research'));
    expect(x('evaluation')).toBeGreaterThan(x('execution'));
  });

  it('uses backend calendar metadata without guessing', () => {
    const graph = threeStageGraph();
    Object.assign(graph, {
      calendar_date: '2026-06-06', display_date: '2026-06-04', is_trading_day: false,
      market_status: 'closed', market_message: '今日休市（周六休市）',
    });

    expect(workflowViewContext(graph, { updated: '2026-06-04T08:30:00' }).tradeDate).toBe('2026-06-04');
    expect(workflowCalendarNotice(graph)).toEqual({ kind: 'closed', text: '今日休市（周六休市）' });
  });
});
