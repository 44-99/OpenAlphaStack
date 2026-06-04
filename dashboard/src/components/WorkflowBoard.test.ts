import { describe, expect, it } from 'vitest';
import { buildNodeAgentPrompt, buildWorkflowFlow, isRerunBlocked } from './WorkflowBoard';
import type { WorkflowEvent, WorkflowGraph, WorkflowGraphNode } from '../types';

describe('WorkflowBoard helpers', () => {
  it('builds concise node context for the agent terminal', () => {
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
    expect(isRerunBlocked('ledger_writer')).toBe(true);
    expect(isRerunBlocked('order_simulator')).toBe(true);
    expect(isRerunBlocked('market_snapshot')).toBe(false);
  });

  it('builds blueprint flow nodes and semantic edges', () => {
    const graph: WorkflowGraph = {
      run_id: 'paper_test',
      nodes: [
        { id: 'market_snapshot', name: '市场快照', enabled: true, locked: false, status: 'success' },
        { id: 'risk_validation', name: '风控校验', enabled: true, locked: true, status: 'running' },
      ],
      edges: [{ from: 'market_snapshot', to: 'risk_validation' }],
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
      active: true,
      inputRefs: ['plan.buy_candidates'],
      outputRefs: ['plan.risk_report'],
    });
    expect(flow.edges[0]).toMatchObject({
      source: 'market_snapshot',
      target: 'risk_validation',
      type: 'smoothstep',
    });
  });

  it('keeps premarket sub-agents as parallel blueprint branches', () => {
    const graph: WorkflowGraph = {
      run_id: 'paper_test',
      nodes: [
        { id: 'market_snapshot', name: '市场快照', enabled: true, locked: false, status: 'success', output_refs: ['market.snapshot'] },
        { id: 'sub_agent_a', name: '子代理A', enabled: true, locked: false, status: 'idle', input_refs: ['market.snapshot'] },
        { id: 'sub_agent_b', name: '子代理B', enabled: true, locked: false, status: 'idle', input_refs: ['market.snapshot'] },
        { id: 'sub_agent_c', name: '子代理C', enabled: true, locked: false, status: 'idle', input_refs: ['market.snapshot'] },
      ],
      edges: [
        { from: 'market_snapshot', to: 'sub_agent_a' },
        { from: 'market_snapshot', to: 'sub_agent_b' },
        { from: 'market_snapshot', to: 'sub_agent_c' },
      ],
    };

    const flow = buildWorkflowFlow(graph, [], 'market_snapshot');
    const branchTargets = flow.edges.map((edge) => edge.target).sort();

    expect(branchTargets).toEqual(['sub_agent_a', 'sub_agent_b', 'sub_agent_c']);
    expect(flow.edges[0].label).toBe('market.snapshot');
  });
});
