import { useEffect, useMemo, useState } from 'react';
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  applyNodeChanges,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
  type XYPosition,
} from '@xyflow/react';
import { api } from '../api';
import type { AgentRunTimeline, LedgerEntry, PlanData, WorkflowEvent, WorkflowGraph, WorkflowGraphNode } from '../types';

type FlowNodeData = {
  node: WorkflowGraphNode;
  selected: boolean;
  current: boolean;
  waiting: boolean;
  eventCount: number;
  latest?: WorkflowEvent;
  inputRefs: string[];
  outputRefs: string[];
};

type FlowEdgeData = {
  from: WorkflowGraphNode;
  to: WorkflowGraphNode;
  kind: 'data' | 'sequence';
  label: string;
  refs: string[];
  required: boolean;
};

const nodeTypes = { workflowNode: WorkflowFlowNode };

export function WorkflowBoard({ graph, events, plan, ledger, onCopyPrompt }: {
  graph?: WorkflowGraph;
  events: WorkflowEvent[];
  plan?: PlanData;
  ledger?: LedgerEntry[];
  onCopyPrompt?: (text: string) => void;
}) {
  const [selectedNodeId, setSelectedNodeId] = useState('');
  const [selectedEdgeId, setSelectedEdgeId] = useState('');
  const [artifact, setArtifact] = useState<{ title: string; content: string } | null>(null);
  const [nodePositions, setNodePositions] = useState<Record<string, XYPosition>>({});
  const [agentTimeline, setAgentTimeline] = useState<AgentRunTimeline | null>(null);
  const [agentTimelineMessage, setAgentTimelineMessage] = useState('');
  const selectedNode = useMemo(() => {
    if (selectedEdgeId) return undefined;
    if (!graph?.nodes.length) return undefined;
    return graph.nodes.find((node) => node.id === selectedNodeId) || graph.nodes[0];
  }, [graph, selectedNodeId, selectedEdgeId]);
  const selectedEvents = useMemo(() => {
    if (!selectedNode) return events;
    return sortWorkflowEvents(events.filter((event) => workflowEventStage(event) === selectedNode.id));
  }, [events, selectedNode]);
  const flow = useMemo(
    () => graph ? buildWorkflowFlow(graph, events, selectedNode?.id || '', selectedEdgeId, nodePositions) : { nodes: [], edges: [] },
    [graph, events, selectedNode?.id, selectedEdgeId, nodePositions],
  );
  const runtimeFocus = useMemo(() => getRuntimeFocus(graph, events), [graph, events]);
  const selectedEdge = useMemo(() => (
    flow.edges.find((edge) => edge.id === selectedEdgeId) as Edge<FlowEdgeData> | undefined
  ), [flow.edges, selectedEdgeId]);
  const context = useMemo(() => workflowViewContext(graph, plan || {}), [graph, plan]);
  const calendarNotice = workflowCalendarNotice(graph);

  useEffect(() => {
    if (!graph?.run_id) return;
    try {
      const raw = window.localStorage.getItem(workflowLayoutStorageKey(graph.run_id));
      setNodePositions(raw ? JSON.parse(raw) : {});
    } catch {
      setNodePositions({});
    }
  }, [graph?.run_id]);

  useEffect(() => {
    if (!graph?.run_id) return;
    try {
      window.localStorage.setItem(workflowLayoutStorageKey(graph.run_id), JSON.stringify(nodePositions));
    } catch {
      // Ignore storage failures; dragging should still work for the current render.
    }
  }, [graph?.run_id, nodePositions]);

  useEffect(() => {
    const taskId = agentTaskIdForNode(selectedNode?.id);
    if (!graph?.run_id || !taskId) {
      setAgentTimeline(null);
      setAgentTimelineMessage('');
      return;
    }
    let active = true;
    setAgentTimelineMessage('读取 Agent 任务审计轨迹...');
    api.agentRunTimeline(graph.run_id, taskId)
      .then((timeline) => {
        if (!active) return;
        setAgentTimeline(timeline);
        setAgentTimelineMessage('');
      })
      .catch((error: Error) => {
        if (!active) return;
        setAgentTimeline(null);
        setAgentTimelineMessage(error.message || 'Agent 任务审计轨迹读取失败');
      });
    return () => {
      active = false;
    };
  }, [graph?.run_id, selectedNode?.id]);

  if (!graph) return <div className="empty">暂无工作流数据</div>;

  function handleNodesChange(changes: NodeChange<Node<FlowNodeData>>[]) {
    const nextNodes = applyNodeChanges(changes, flow.nodes);
    const positionChanges = changes.filter((change) => change.type === 'position');
    if (!positionChanges.length) return;
    setNodePositions((current) => {
      const next = { ...current };
      nextNodes.forEach((node) => {
        if (positionChanges.some((change) => change.id === node.id)) {
          next[node.id] = node.position;
        }
      });
      return next;
    });
  }

  function resetNodeLayout() {
    if (!graph?.run_id) return;
    try {
      window.localStorage.removeItem(workflowLayoutStorageKey(graph.run_id));
    } catch {
      // Ignore storage failures; the in-memory reset is enough for this session.
    }
    setNodePositions({});
  }

  return (
    <section className={`workflow-board ${graph.market_status === 'closed' ? 'market-closed' : ''} ${graph.market_status === 'stale' ? 'market-stale' : ''}`}>
      <div className="workflow-graph">
        <header>
          <strong>流程画布</strong>
          <span className="workflow-graph-actions">
            <button onClick={resetNodeLayout} disabled={!Object.keys(nodePositions).length}>恢复默认布局</button>
            <span>
              {graph.run_id}
              {graph.run_status ? ` / ${graph.is_alive ? '运行中' : '已停止'}:${graph.run_status}` : ''}
              {graph.data_time ? ` / 数据 ${graph.data_time}` : ''}
            </span>
          </span>
        </header>
        <div className="workflow-context-strip">
          <span><b>数据日</b>{context.tradeDate}</span>
          <span><b>日历</b>{context.calendarLabel}</span>
          <span><b>运行</b>{context.runMode}</span>
          <span><b>计划</b>{context.planDate}</span>
        </div>
        {calendarNotice ? <div className={`workflow-runtime-alert ${calendarNotice.kind}`}>{calendarNotice.text}</div> : null}
        {runtimeFocus ? (
          <div className={`workflow-runtime-focus ${runtimeFocus.kind}`}>
            <b>{runtimeFocus.label}</b>
            <span>{runtimeFocus.detail}</span>
          </div>
        ) : null}
        <div className="workflow-flow-shell">
          <ReactFlowProvider>
            <ReactFlow
              nodes={flow.nodes}
              edges={flow.edges}
              nodeTypes={nodeTypes}
              onNodesChange={handleNodesChange}
              fitView
              fitViewOptions={{ padding: 0.18 }}
              minZoom={0.35}
              maxZoom={1.45}
              nodesDraggable
              nodesConnectable={false}
              elementsSelectable
              onNodeClick={(_, node) => {
                setSelectedNodeId(node.id);
                setSelectedEdgeId('');
              }}
              onEdgeClick={(_, edge) => {
                setSelectedEdgeId(edge.id);
                setSelectedNodeId('');
              }}
              proOptions={{ hideAttribution: true }}
            >
              <Background color="rgba(65, 224, 201, 0.16)" gap={22} size={1} />
              <Controls className="workflow-controls" showInteractive={false} />
            </ReactFlow>
          </ReactFlowProvider>
        </div>
      </div>
      <aside className="workflow-inspector">
        <header>
          <strong>{selectedEdge ? '数据流详情' : selectedNode?.name || '节点详情'}</strong>
          <span className="workflow-inspector-actions">
            {!selectedEdge && selectedNode && onCopyPrompt ? (
              <button onClick={() => onCopyPrompt(buildNodeAgentPrompt(selectedNode, selectedEvents))}>复制 Codex 提示</button>
            ) : null}
          </span>
        </header>
        {selectedEdge ? (
          <EdgeInspector edge={selectedEdge} />
        ) : (
          <>
            {selectedNode ? <NodeStatusPanel node={selectedNode} selectedEvents={selectedEvents} /> : null}
            {selectedNode ? <WorkflowNodeArtifact nodeId={selectedNode.id} plan={plan || {}} ledger={ledger || []} events={events} /> : null}
            {agentTaskIdForNode(selectedNode?.id) ? (
              <AgentTimelinePanel
                timeline={agentTimeline}
                message={agentTimelineMessage}
                onOpenArtifact={(taskId, ref) => loadAgentArtifact(graph.run_id, taskId, ref, setArtifact)}
              />
            ) : null}
            {selectedNode ? (
              <section className="workflow-inspector-section">
                <header><strong>数据引用</strong><span>{(selectedNode.input_refs || []).length + (selectedNode.output_refs || []).length} 项</span></header>
                <DataRefList title="输入" refs={selectedNode.input_refs || selectedEvents[0]?.input_refs || []} event={selectedEvents[0]} onOpen={setArtifact} kind="input" />
                <DataRefList title="输出" refs={selectedNode.output_refs || selectedEvents[0]?.output_refs || []} event={selectedEvents[0]} onOpen={setArtifact} kind="output" />
              </section>
            ) : null}
          </>
        )}
        {!selectedEdge ? (
          <section className="workflow-inspector-section">
            <header><strong>事件时间线</strong><span>{selectedEvents.length} 条</span></header>
            <div className="workflow-events">
              {selectedEvents.length ? selectedEvents.map((event) => (
                <article className={`workflow-event ${event.status}`} key={event.event_id}>
                  <span>{event.phase || '--'} / {formatTraceTime(event.started_at)}</span>
                  <strong>{event.node_name}</strong>
                  <p>{event.summary || '--'}</p>
                  {event.error ? <code>{event.error}</code> : null}
                  {event.artifact_dir ? (
                    <div className="artifact-actions">
                      {['input.json', 'output.json', 'prompt.txt', 'response.txt', 'error.txt'].map((name) => (
                        <button key={name} onClick={() => loadArtifact(event, name, setArtifact)}>{name}</button>
                      ))}
                    </div>
                  ) : null}
                </article>
              )) : <div className="empty compact">该节点暂无事件</div>}
            </div>
          </section>
        ) : null}
        {artifact ? (
          <div className="artifact-viewer">
            <header><strong>{artifact.title}</strong><button onClick={() => setArtifact(null)}>关闭</button></header>
            <pre>{artifact.content}</pre>
          </div>
        ) : null}
      </aside>
    </section>
  );
}

function AgentTimelinePanel({
  timeline,
  message,
  onOpenArtifact,
}: {
  timeline: AgentRunTimeline | null;
  message: string;
  onOpenArtifact: (taskId: string, ref: string) => void;
}) {
  const tasks = timeline ? Object.values(timeline.tasks || {}) : [];
  const timelineTaskId = timeline?.task_id || 'premarket_plan';
  return (
    <section className="workflow-inspector-section agent-timeline-panel">
      <header>
        <strong>Agent 任务轨迹</strong>
        <span>{timeline ? `${tasks.length} 个步骤 / ${timeline.events.length} 条事件` : '--'}</span>
      </header>
      {message ? <p>{message}</p> : null}
      {timeline?.warnings.length ? (
        <div className="workflow-runtime-alert stale">
          {timeline.warnings.slice(0, 3).join('；')}
          {timeline.warnings.length > 3 ? `；另 ${timeline.warnings.length - 3} 条` : ''}
        </div>
      ) : null}
      {timeline && !tasks.length && !timeline.warnings.length ? (
        <div className="empty compact">Agent 尚未提交任务审计轨迹</div>
      ) : null}
      <div className="workflow-events">
        {tasks.map((task) => (
          <article className={`workflow-event ${task.status}`} key={task.task_id}>
            <span>{task.role || '步骤'} / {statusLabel(task.status)}</span>
            <strong>{task.task_id}</strong>
            <p>{task.summary || '--'}</p>
            <div className="artifact-actions">
              {task.input_ref ? <button onClick={() => onOpenArtifact(timelineTaskId, task.input_ref || '')}>输入</button> : null}
              {task.output_ref ? <button onClick={() => onOpenArtifact(timelineTaskId, task.output_ref || '')}>输出</button> : null}
              {task.result_ref ? <button onClick={() => onOpenArtifact(timelineTaskId, task.result_ref || '')}>结果</button> : null}
            </div>
            <div className="trace-meta">
              {task.input_ref ? <span>输入 <b>{task.input_ref}</b></span> : null}
              {task.output_ref ? <span>输出 <b>{task.output_ref}</b></span> : null}
              {task.result_ref ? <span>结果 <b>{task.result_ref}</b></span> : null}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function NodeStatusPanel({ node, selectedEvents }: { node: WorkflowGraphNode; selectedEvents: WorkflowEvent[] }) {
  return (
    <section className="workflow-inspector-section node-status-panel">
      <header><strong>节点状态</strong><span>{phaseLabel(node.phase || inferNodePhase(node.id))}</span></header>
      <p>{node.summary || '暂无摘要'}</p>
      <div className="trace-meta">
        <span>状态 <b>{statusLabel(node.status)}</b></span>
        <span>开始 <b>{formatTraceTime(node.started_at)}</b></span>
        <span>结束 <b>{formatTraceTime(node.ended_at)}</b></span>
        <span>事件 <b>{selectedEvents.length}</b></span>
      </div>
    </section>
  );
}

function WorkflowNodeArtifact({ nodeId, plan, ledger, events }: {
  nodeId: string;
  plan: PlanData;
  ledger: LedgerEntry[];
  events: WorkflowEvent[];
}) {
  if (nodeId === 'research') {
    return <PlanArtifact plan={plan} />;
  }
  if (nodeId === 'evaluation') {
    return <ReviewArtifact plan={plan} ledger={ledger} events={events} />;
  }
  return null;
}

function PlanArtifact({ plan }: { plan: PlanData }) {
  if (!plan.market_bias) {
    return <section className="workflow-product-panel workflow-inspector-section muted">暂无盘前计划产物</section>;
  }
  const candidates = plan.buy_candidates || [];
  const actionable = candidates.filter((item) => Number(item.entry_min) > 0 && Number(item.entry_max) > 0 && Number(item.stop_loss) > 0);
  return (
    <section className="workflow-product-panel workflow-inspector-section">
      <header><strong>盘前计划产物</strong><span>{plan.updated || '--'}</span></header>
      <div className="product-metric-row">
        <span>方向 <b>{plan.market_bias}</b></span>
        <span>置信 <b>{plan.bias_confidence || 0}%</b></span>
        <span>候选 <b>{candidates.length}</b></span>
        <span>可执行 <b>{actionable.length}</b></span>
        <span>单仓上限 <b>{plan.rules?.max_single_position_pct || 25}%</b></span>
        <span>总仓上限 <b>{plan.rules?.max_total_position_pct || 80}%</b></span>
      </div>
      {plan.bias_reasoning ? <p>{plan.bias_reasoning}</p> : null}
      <div className="product-list">
        {candidates.slice(0, 6).map((item) => (
          <article key={item.code}>
            <strong>{item.code}</strong>
            <span>{item.strategy_type || '--'} / {item.position_pct || 0}%</span>
            <small>入场 {formatMaybeNumber(item.entry_min)} - {formatMaybeNumber(item.entry_max)} / 止损 {formatMaybeNumber(item.stop_loss)}</small>
          </article>
        ))}
        {!candidates.length ? <small>暂无候选标的</small> : null}
      </div>
    </section>
  );
}

function ReviewArtifact({ plan, ledger, events }: { plan: PlanData; ledger: LedgerEntry[]; events: WorkflowEvent[] }) {
  const trades = ledger.filter((row) => row.symbol || row.code);
  const errors = events.filter((event) => event.status === 'error');
  const planCodes = new Set((plan.buy_candidates || []).map((item) => item.code));
  const plannedTrades = trades.filter((row) => planCodes.has(row.symbol || row.code || ''));
  return (
    <section className="workflow-product-panel workflow-inspector-section">
      <header><strong>盘后复盘节点</strong><span>{trades.length} 笔成交</span></header>
      <div className="product-metric-row">
        <span>候选 <b>{(plan.buy_candidates || []).length}</b></span>
        <span>计划成交 <b>{plannedTrades.length}</b></span>
        <span>未成交候选 <b>{Math.max((plan.buy_candidates || []).length - plannedTrades.length, 0)}</b></span>
        <span>风险事件 <b>{errors.length}</b></span>
      </div>
      <div className="product-list">
        {trades.slice(0, 5).map((row, index) => (
          <article key={row.seq || index}>
            <strong>{row.symbol || row.code}</strong>
            <span>{row.decision || row.action || '--'} @{formatMaybeNumber(row.price)}</span>
            <small>{row.time || '--'} / {row.strategy || '--'}</small>
          </article>
        ))}
        {!trades.length ? <small>暂无成交，复盘仅展示流程风险和计划偏差。</small> : null}
      </div>
    </section>
  );
}

export function workflowViewContext(graph: WorkflowGraph | undefined, plan: PlanData) {
  if (!graph) {
    return {
      tradeDate: datePart(plan.updated) || '--',
      calendarLabel: '--',
      runMode: '--',
      viewState: '--',
      planDate: datePart(plan.updated) || '--',
    };
  }
  const dataDate = datePart(graph.data_time);
  const planDate = datePart(plan.updated);
  const runDate = runDateFromId(graph.run_id);
  const tradeDate = graph.display_date || dataDate || planDate || runDate || '--';
  const runMode = graph.run_id?.split('_')[0] || '--';
  const viewState = graph.is_alive ? (graph.observation_mode ? '观察中' : '实时') : '历史';
  const marketLabel = graph.market_status === 'closed' ? '休市' : graph.market_status === 'stale' ? '历史' : '交易日';
  return {
    tradeDate,
    calendarLabel: `${graph.calendar_date || '--'} / ${marketLabel}`,
    runMode: `${runMode} / ${viewState}`,
    viewState,
    planDate: planDate || '--',
  };
}

export function workflowCalendarNotice(graph?: WorkflowGraph) {
  if (!graph) return null;
  if (graph.market_status === 'closed') {
    return {
      kind: 'closed',
      text: graph.market_message || '今日休市，当前展示最近一次模拟盘记录。',
    };
  }
  if (graph.market_status === 'stale') {
    return {
      kind: 'stale',
      text: graph.market_message || '今天是交易日，但当前画布展示的是历史模拟盘记录。',
    };
  }
  if (graph.run_status && !graph.is_alive) {
    return {
      kind: 'history',
      text: '模拟盘未运行：当前画布展示的是历史 run，启动或恢复模拟盘后才会高亮实时节点。',
    };
  }
  return null;
}

function workflowLayoutStorageKey(runId: string) {
  return `openalphastack.workflow.layout.${runId}`;
}

export function agentTaskIdForNode(nodeId?: string) {
  if (nodeId === 'research') return 'premarket_plan';
  if (nodeId === 'evaluation') return 'postclose_review';
  return '';
}

function datePart(value?: string) {
  if (!value) return '';
  const match = value.match(/\d{4}-\d{2}-\d{2}/);
  return match ? match[0] : '';
}

function runDateFromId(runId?: string) {
  if (!runId) return '';
  return datePart(runId.replace('T', ' '));
}

export function buildNodeAgentPrompt(node: WorkflowGraphNode, events: WorkflowEvent[]) {
  const recent = events.slice(0, 3).map((event) => (
    `${event.status}/${event.phase || '--'}: ${event.summary || event.error || '--'}`
  )).join('；');
  return `请结合 OpenAlphaStack 当前流程阶段分析：阶段=${node.name}(${node.id})；状态=${node.status}；摘要=${node.summary || '--'}；最近事件=${recent || '暂无'}。`;
}

function WorkflowFlowNode({ data }: NodeProps<Node<FlowNodeData>>) {
  const { node, selected, current, waiting, eventCount, inputRefs, outputRefs } = data;
  return (
    <div className={`workflow-flow-node ${node.status} ${selected ? 'selected' : ''} ${current ? 'current' : ''} ${waiting ? 'waiting' : ''}`}>
      <Handle type="target" position={Position.Left} className="workflow-handle" />
      <div className="node-topline">
        <span>{phaseLabel(node.phase || inferNodePhase(node.id))}</span>
        <b>{waiting ? '等待' : statusLabel(node.status)}</b>
      </div>
      {current ? <span className="node-runtime-badge">当前运行</span> : null}
      {waiting ? <span className="node-runtime-badge waiting">等待中</span> : null}
      <strong>{node.name}</strong>
      <small>{node.summary || '等待输入'}</small>
      <div className="node-io">
        <span>IN {inputRefs.length}</span>
        <span>OUT {outputRefs.length}</span>
        <span>EVT {eventCount}</span>
      </div>
      {node.started_at ? <em>{formatTraceTime(node.started_at)}</em> : null}
      <Handle type="source" position={Position.Right} className="workflow-handle" />
    </div>
  );
}

export function buildWorkflowFlow(
  graph: WorkflowGraph,
  events: WorkflowEvent[],
  selectedNodeId: string,
  activeEdgeId = '',
  positionOverrides: Record<string, XYPosition> = {},
): { nodes: Node<FlowNodeData>[]; edges: Edge<FlowEdgeData>[] } {
  const latestEvents = new Map<string, WorkflowEvent[]>();
  sortWorkflowEvents(events).forEach((event) => {
    const stageId = workflowEventStage(event);
    if (!stageId) return;
    const rows = latestEvents.get(stageId) || [];
    rows.push(event);
    latestEvents.set(stageId, rows);
  });
  const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
  const runtimeNodeId = currentRuntimeNodeId(graph, events);
  const waitingNodeId = graph.observation_mode ? waitingRuntimeNodeId(graph) : '';
  const nodes = graph.nodes.map((node, index) => {
    const phase = inferNodePhase(node.id);
    const position = positionOverrides[node.id] || blueprintPosition(node.id, index);
    const nodeEvents = latestEvents.get(node.id) || [];
    const latest = nodeEvents[0] || {};
    const inputRefs = node.input_refs?.length ? node.input_refs : latest.input_refs || [];
    const outputRefs = node.output_refs?.length ? node.output_refs : latest.output_refs || [];
    return {
      id: node.id,
      type: 'workflowNode',
      position,
      data: {
        node: { ...node, phase: node.phase || phase, input_refs: inputRefs, output_refs: outputRefs },
        selected: node.id === selectedNodeId,
        current: node.id === runtimeNodeId,
        waiting: node.id === waitingNodeId,
        eventCount: nodeEvents.length,
        latest,
        inputRefs,
        outputRefs,
      },
    };
  });
  const edges = graph.edges.map((edge, index) => ({
    id: `${edge.from}-${edge.to}`,
    source: edge.from,
    target: edge.to,
    label: edgeDataLabel(edge),
    type: 'smoothstep',
    animated: isRuntimeEdge(edge, runtimeNodeId),
    className: `workflow-edge-${edge.kind || 'sequence'}`,
    style: {
      stroke: edgeStroke(edge, runtimeNodeId, activeEdgeId, edge.kind || 'sequence'),
      strokeWidth: isRuntimeEdge(edge, runtimeNodeId) || activeEdgeId === `${edge.from}-${edge.to}` ? 2.2 : 1.2,
      strokeDasharray: edge.kind === 'sequence' ? '6 7' : undefined,
    },
    labelStyle: { fill: '#9fb5c6', fontSize: 10, fontFamily: 'JetBrains Mono, Cascadia Code, monospace' },
    labelBgStyle: { fill: 'rgba(5, 9, 14, 0.82)', fillOpacity: 0.9 },
    data: {
      from: nodeById.get(edge.from) || emptyGraphNode(edge.from),
      to: nodeById.get(edge.to) || emptyGraphNode(edge.to),
      kind: edge.kind || 'sequence',
      label: edgeDataLabel(edge),
      refs: edgeRefs(edge),
      required: edge.required !== false,
    },
    zIndex: isRuntimeEdge(edge, runtimeNodeId) ? 8 : index,
  }));
  return { nodes, edges };
}

function currentRuntimeNodeId(graph: WorkflowGraph, events: WorkflowEvent[] = []) {
  const runningNode = graph.is_alive ? graph.nodes.find((node) => node.status === 'running') : undefined;
  if (runningNode) return runningNode.id;

  const latestEvent = sortWorkflowEvents(events)[0];
  if (!graph.is_alive) return latestEvent ? runtimeNodeFromEvent(latestEvent) : '';
  if (latestEvent?.status === 'running') return workflowEventStage(latestEvent);
  if (graph.observation_mode) return '';

  const canInferRuntime = canInferRuntimeFromGraphClock(graph);
  if (latestEvent && canInferRuntime && workflowEventStage(latestEvent) === 'execution') {
    return 'execution';
  }

  if (!canInferRuntime) return '';
  const phase = runtimePhaseFromDataTime(graph.data_time);
  if (phase === 'intraday' || phase === 'lunch') return 'execution';
  if (phase === 'postclose') return 'evaluation';
  if (phase === 'premarket') return 'research';
  return '';
}

function runtimeNodeFromEvent(event: WorkflowEvent) {
  return workflowEventStage(event);
}

function waitingRuntimeNodeId(graph: WorkflowGraph) {
  if (!graph.is_alive || !graph.observation_mode) return '';
  const reason = graph.observation_reason || '';
  if (reason.includes('post_market') || reason.includes('盘后')) return 'evaluation';
  return 'research';
}

function getRuntimeFocus(graph?: WorkflowGraph, events: WorkflowEvent[] = []) {
  if (!graph) return null;
  if (!graph.is_alive) return null;
  const current = graph.nodes.find((node) => node.id === currentRuntimeNodeId(graph, events));
  if (current) {
    return {
      kind: 'running',
      label: `当前节点：${current.name}`,
      detail: current.summary || statusLabel(current.status),
    };
  }
  if (graph.observation_mode) {
    const waitingNode = graph.nodes.find((node) => node.id === waitingRuntimeNodeId(graph));
    return {
      kind: 'waiting',
      label: `等待：${waitingNode?.name || '状态观察'}`,
      detail: graph.observation_reason || '当前处于观察模式，等待下一个可执行时段。',
    };
  }
  return {
    kind: 'running',
    label: '运行中：等待节点事件',
    detail: '引擎进程已启动，流程事件尚未写入或正在刷新。',
  };
}

function canInferRuntimeFromGraphClock(graph: WorkflowGraph) {
  if (graph.market_status === 'stale') return false;
  if (graph.market_status === 'closed') return false;
  if (graph.is_trading_day === false) return false;
  if (graph.calendar_date && graph.display_date && graph.calendar_date !== graph.display_date) return false;
  return true;
}

function runtimePhaseFromDataTime(value?: string) {
  const time = (value || '').match(/(\d{2}):(\d{2})(?::\d{2})?/)?.[0]?.slice(0, 5);
  if (!time) return '';
  if (time >= '09:15' && time < '11:30') return 'intraday';
  if (time >= '11:30' && time < '13:00') return 'lunch';
  if (time >= '13:00' && time < '15:00') return 'intraday';
  if (time >= '15:00') return 'postclose';
  return 'premarket';
}

function sortWorkflowEvents(events: WorkflowEvent[]) {
  return [...events].sort((left, right) => workflowEventStamp(right).localeCompare(workflowEventStamp(left)));
}

function workflowEventStamp(event: WorkflowEvent) {
  return `${event.started_at || event.ended_at || ''}:${event.event_id || ''}`;
}

function inferNodePhase(nodeId: string) {
  if (nodeId === 'research') return 'research';
  if (nodeId === 'evaluation') return 'evaluation';
  return 'execution';
}

function blueprintPosition(nodeId: string, fallback: number) {
  const positions: Record<string, { x: number; y: number }> = {
    research: { x: 90, y: 320 },
    execution: { x: 410, y: 320 },
    evaluation: { x: 730, y: 320 },
  };
  return positions[nodeId] || { x: 90 + fallback * 320, y: 320 };
}

function edgeDataLabel(edge: { label?: string; refs?: string[]; kind?: string }) {
  if (edge.label) return edge.label;
  if (edge.refs?.length) return edge.refs.slice(0, 2).join(' / ');
  return edge.kind === 'data' ? '数据依赖' : '流程顺序';
}

function edgeRefs(edge: { refs?: string[] }) {
  return edge.refs || [];
}

function emptyGraphNode(id: string): WorkflowGraphNode {
  return { id, name: id, status: 'idle' };
}

function isRuntimeEdge(edge: { from?: string; to?: string; source?: string; target?: string }, runtimeNodeId: string) {
  const from = edge.from || edge.source;
  const to = edge.to || edge.target;
  return Boolean(runtimeNodeId && (from === runtimeNodeId || to === runtimeNodeId));
}

function edgeStroke(
  edge: { from?: string; to?: string; source?: string; target?: string },
  runtimeNodeId: string,
  selectedEdgeId: string,
  kind = 'sequence',
) {
  if (isRuntimeEdge(edge, runtimeNodeId)) return '#d6a13b';
  const edgeId = `${edge.from || edge.source}-${edge.to || edge.target}`;
  if (selectedEdgeId === edgeId) return '#41e0c9';
  return kind === 'data' ? 'rgba(65, 224, 201, 0.46)' : 'rgba(113, 129, 151, 0.42)';
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    running: '运行中',
    success: '完成',
    warning: '告警',
    error: '异常',
    skipped: '跳过',
    idle: '等待',
  };
  return labels[status] || status;
}

function phaseLabel(phase: string) {
  const labels: Record<string, string> = {
    research: '研究',
    execution: '执行',
    evaluation: '评估',
    premarket: '盘前',
    intraday: '盘中',
    postclose: '盘后',
    system: '系统',
  };
  return labels[phase] || phase || '未分组';
}

function workflowEventStage(event: WorkflowEvent) {
  if (event.stage_id) return event.stage_id;
  if (['market_snapshot', 'agent_research', 'risk_validation', 'plan_writer', 'research'].includes(event.node_id)) return 'research';
  if (['daily_report', 'trade_attribution', 'strategy_feedback', 'evaluation'].includes(event.node_id)) return 'evaluation';
  if (['state_watcher', 'fastlane_tick', 'intraday_event_stream', 'execution'].includes(event.node_id)) return 'execution';
  return '';
}

function formatTraceTime(value?: string) {
  if (!value) return '--';
  return value.includes('T') ? value.split('T')[1]?.slice(0, 8) || value : value.slice(11, 19) || value;
}

function formatMaybeNumber(value?: number) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric !== 0 ? numeric.toFixed(2) : '--';
}

function artifactFileForKind(kind: 'input' | 'output') {
  return kind === 'input' ? 'input.json' : 'output.json';
}

function DataRefList({
  title,
  refs,
  event,
  onOpen,
  kind,
  emptyLabel,
}: {
  title: string;
  refs: string[];
  event?: WorkflowEvent;
  onOpen: (artifact: { title: string; content: string }) => void;
  kind: 'input' | 'output';
  emptyLabel?: string;
}) {
  const artifactName = artifactFileForKind(kind);
  return (
    <div className="workflow-ref-block">
      <header>
        <strong>{title}</strong>
        {event?.artifact_dir ? <button onClick={() => loadArtifact(event, artifactName, onOpen)}>查看 {artifactName}</button> : null}
      </header>
      {refs.length ? refs.map((ref) => (
        <div className="workflow-ref" key={`${title}-${ref}`}>
          <code>{ref}</code>
          <span>{describeRefSource(ref)}</span>
        </div>
      )) : <small>{emptyLabel || `暂无 ${title} 引用`}</small>}
    </div>
  );
}

function EdgeInspector({ edge }: { edge: Edge<FlowEdgeData> }) {
  const data = edge.data;
  if (!data) return null;
  const isDataEdge = data.kind === 'data';
  return (
    <section className="workflow-trace-panel">
      <div className="edge-route">
        <strong>{data.from.name}</strong>
        <span>{isDataEdge ? '数据依赖' : '流程顺序'}</span>
        <strong>{data.to.name}</strong>
      </div>
      <p>{data.label}</p>
      <DataRefList
        title={isDataEdge ? '传递引用' : '顺序说明'}
        refs={data.refs}
        kind="output"
        onOpen={() => undefined}
        emptyLabel={isDataEdge ? '未声明传递引用' : '仅表示执行顺序，不传递数据引用'}
      />
      <div className="trace-meta">
        <span>类型 <b>{isDataEdge ? '数据' : '顺序'}</b></span>
        <span>依赖 <b>{data.required ? '必需' : '可选'}</b></span>
        <span>上游 <b>{data.from.status}</b></span>
        <span>下游 <b>{data.to.status}</b></span>
      </div>
    </section>
  );
}

function describeRefSource(ref: string) {
  if (ref.startsWith('source.')) return '外部数据源';
  if (ref.startsWith('artifact.agent.')) return '自主 Agent 任务产物';
  if (ref.startsWith('artifact.market.')) return '市场快照产物';
  if (ref.startsWith('artifact.plan.')) return '计划落盘产物';
  if (ref.startsWith('artifact.fastlane.')) return '盘中快车道事件产物';
  if (ref.startsWith('artifact.shadow.')) return '影子账户诊断产物';
  if (ref.startsWith('account.state')) return '账户状态';
  if (ref.startsWith('account.ledger')) return '成交账本';
  if (ref.startsWith('review.')) return '盘后复盘产物';
  if (ref.startsWith('rule.')) return '本地规则/skills';
  if (ref.startsWith('memory.')) return '本地记忆与历史上下文';
  if (ref.endsWith('.json') || ref.endsWith('.jsonl')) return `本地运行目录 / ${ref}`;
  if (ref.startsWith('plan.')) return 'plan.json 中的结构化字段';
  if (ref.startsWith('premarket.')) return '盘前 Agent 阶段输出';
  if (ref.startsWith('market.') || ref.includes('quote')) return '行情/市场快照数据源';
  if (ref.startsWith('ledger')) return 'ledger.jsonl 成交账本';
  if (ref.startsWith('review/')) return '盘后复盘输出文件';
  if (ref.startsWith('fastlane.')) return '盘中快车道关键事件';
  if (ref.startsWith('state')) return 'state.json 当前账户状态';
  if (ref === 'skills') return '本地 skills 决策规则';
  if (ref === 'memory') return '本地记忆与历史上下文';
  return '流程内部数据引用';
}

async function loadArtifact(
  event: WorkflowEvent,
  name: string,
  setArtifact: (artifact: { title: string; content: string }) => void,
) {
  try {
    const result = await api.workflowArtifact(event.run_id || 'active', event.event_id, name);
    setArtifact({ title: `${event.node_name} / ${name}`, content: result.content });
  } catch (error) {
    setArtifact({
      title: `${event.node_name} / ${name}`,
      content: error instanceof Error ? error.message : 'artifact 读取失败',
    });
  }
}

async function loadAgentArtifact(
  runId: string,
  taskId: string,
  ref: string,
  setArtifact: (artifact: { title: string; content: string }) => void,
) {
  try {
    const result = await api.agentRunArtifact(runId, taskId, ref);
    setArtifact({ title: `Agent ${taskId} / ${ref}`, content: result.content });
  } catch (error) {
    setArtifact({
      title: `Agent ${taskId} / ${ref}`,
      content: error instanceof Error ? error.message : 'Agent artifact 读取失败',
    });
  }
}
