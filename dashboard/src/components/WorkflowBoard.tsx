import { useEffect, useMemo, useState } from 'react';
import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { api } from '../api';
import type { WorkflowConfig, WorkflowConfigNode, WorkflowEvent, WorkflowGraph, WorkflowGraphNode } from '../types';

type FlowNodeData = {
  node: WorkflowGraphNode;
  active: boolean;
  eventCount: number;
  latest?: WorkflowEvent;
  inputRefs: string[];
  outputRefs: string[];
};

type FlowEdgeData = {
  from: WorkflowGraphNode;
  to: WorkflowGraphNode;
  label: string;
  refs: string[];
};

const nodeTypes = { workflowNode: WorkflowFlowNode };

export function WorkflowBoard({ graph, events, onSendToAgent }: {
  graph?: WorkflowGraph;
  events: WorkflowEvent[];
  onSendToAgent?: (text: string) => void;
}) {
  const [selectedNodeId, setSelectedNodeId] = useState('');
  const [selectedEdgeId, setSelectedEdgeId] = useState('');
  const [artifact, setArtifact] = useState<{ title: string; content: string } | null>(null);
  const [config, setConfig] = useState<WorkflowConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [configMessage, setConfigMessage] = useState('');
  const [rerunMessage, setRerunMessage] = useState('');
  const selectedNode = useMemo(() => {
    if (selectedEdgeId) return undefined;
    if (!graph?.nodes.length) return undefined;
    return graph.nodes.find((node) => node.id === selectedNodeId) || graph.nodes[0];
  }, [graph, selectedNodeId, selectedEdgeId]);
  const selectedEvents = useMemo(() => {
    if (!selectedNode) return events;
    return sortWorkflowEvents(events.filter((event) => event.node_id === selectedNode.id));
  }, [events, selectedNode]);
  const flow = useMemo(
    () => graph ? buildWorkflowFlow(graph, events, selectedNode?.id || '', selectedEdgeId) : { nodes: [], edges: [] },
    [graph, events, selectedNode?.id, selectedEdgeId],
  );
  const selectedEdge = useMemo(() => (
    flow.edges.find((edge) => edge.id === selectedEdgeId) as Edge<FlowEdgeData> | undefined
  ), [flow.edges, selectedEdgeId]);
  const selectedConfig = selectedNode ? config?.nodes?.[selectedNode.id] : undefined;

  useEffect(() => {
    if (!graph?.run_id) return;
    let active = true;
    api.workflowConfig(graph.run_id)
      .then((data) => {
        if (active) setConfig(data);
      })
      .catch((error: Error) => {
        if (active) setConfigMessage(error.message || '配置读取失败');
      });
    return () => {
      active = false;
    };
  }, [graph?.run_id]);

  if (!graph) return <div className="empty">暂无工作流数据</div>;

  async function saveConfig() {
    if (!graph?.run_id || !config) return;
    setSaving(true);
    setConfigMessage('');
    try {
      const next = await api.saveWorkflowConfig(graph.run_id, config);
      setConfig(next);
      setConfigMessage('配置已保存，并写入审计事件');
    } catch (error) {
      setConfigMessage(error instanceof Error ? error.message : '配置保存失败');
    } finally {
      setSaving(false);
    }
  }

  async function requestRerun() {
    if (!graph?.run_id || !selectedNode) return;
    setRerunMessage('');
    try {
      const result = await api.workflowNodeRerun(graph.run_id, selectedNode.id);
      setRerunMessage(`已入队: ${String(result.request.request_id || selectedNode.id)}`);
    } catch (error) {
      setRerunMessage(error instanceof Error ? error.message : '重跑请求失败');
    }
  }

  function updateSelectedNode(patch: Partial<WorkflowConfigNode>) {
    if (!selectedNode || !config) return;
    setConfig({
      ...config,
      nodes: {
        ...config.nodes,
        [selectedNode.id]: {
          ...config.nodes[selectedNode.id],
          ...patch,
        },
      },
    });
  }

  function updateParam(key: string, value: string) {
    if (!selectedConfig) return;
    updateSelectedNode({
      params: {
        ...selectedConfig.params,
        [key]: parseParamValue(value),
      },
    });
  }

  return (
    <section className="workflow-board">
      <div className="workflow-graph">
        <header>
          <strong>流程画布</strong>
          <span>{graph.run_id}</span>
        </header>
        <div className="workflow-flow-shell">
          <ReactFlowProvider>
            <ReactFlow
              nodes={flow.nodes}
              edges={flow.edges}
              nodeTypes={nodeTypes}
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
              <MiniMap
                className="workflow-minimap"
                pannable
                zoomable
                nodeColor={(node) => miniMapColor(String((node.data as FlowNodeData | undefined)?.node?.status || 'idle'))}
              />
              <Controls className="workflow-controls" showInteractive={false} />
            </ReactFlow>
          </ReactFlowProvider>
          <div className="workflow-lane-label premarket">盘前计划</div>
          <div className="workflow-lane-label intraday">盘中执行</div>
          <div className="workflow-lane-label postclose">盘后复盘</div>
        </div>
      </div>
      <aside className="workflow-inspector">
        <header>
          <strong>{selectedEdge ? '数据流详情' : selectedNode?.name || '节点详情'}</strong>
          <span className="workflow-inspector-actions">
            {!selectedEdge && selectedNode && onSendToAgent ? (
              <button onClick={() => onSendToAgent(buildNodeAgentPrompt(selectedNode, selectedEvents))}>发送到 Agent</button>
            ) : null}
            {!selectedEdge && selectedNode?.locked ? <span className="lock-pill">锁定</span> : null}
          </span>
        </header>
        {selectedEdge ? (
          <EdgeInspector edge={selectedEdge} />
        ) : (
          <>
            <p>{selectedNode?.summary || '暂无摘要'}</p>
            {selectedNode ? (
              <section className="workflow-trace-panel">
                <div className="trace-meta">
                  <span>状态 <b>{selectedNode.status}</b></span>
                  <span>开始 <b>{formatTraceTime(selectedNode.started_at)}</b></span>
                  <span>结束 <b>{formatTraceTime(selectedNode.ended_at)}</b></span>
                </div>
                <DataRefList title="输入" refs={selectedNode.input_refs || selectedEvents[0]?.input_refs || []} event={selectedEvents[0]} onOpen={setArtifact} kind="input" />
                <DataRefList title="输出" refs={selectedNode.output_refs || selectedEvents[0]?.output_refs || []} event={selectedEvents[0]} onOpen={setArtifact} kind="output" />
              </section>
            ) : null}
          </>
        )}
        {!selectedEdge && selectedNode && selectedConfig ? (
          <section className="workflow-config-editor">
            <div className="config-row">
              <span>节点开关</span>
              <button
                className={selectedConfig.enabled ? 'active' : ''}
                disabled={selectedConfig.locked}
                onClick={() => updateSelectedNode({ enabled: !selectedConfig.enabled })}
                title={selectedConfig.locked ? '风控、计划写入、执行和账本节点必须保持启用' : undefined}
              >
                {selectedConfig.enabled ? '启用' : '禁用'}{selectedConfig.locked ? ' / 锁定' : ''}
              </button>
            </div>
            <div className="config-params">
              <strong>安全参数</strong>
              {Object.keys(selectedConfig.params || {}).length ? Object.entries(selectedConfig.params).map(([key, value]) => (
                <label key={key}>
                  <span>{key}</span>
                  <input value={String(value)} onChange={(event) => updateParam(key, event.target.value)} disabled={selectedConfig.locked && key === 'enabled'} />
                </label>
              )) : <small>该节点暂无可调参数</small>}
            </div>
            <div className="config-actions">
              <button onClick={saveConfig} disabled={saving}>{saving ? '保存中' : '保存配置'}</button>
              {configMessage ? <span>{configMessage}</span> : null}
            </div>
            <div className="config-actions">
              <button onClick={requestRerun} disabled={isRerunBlocked(selectedNode.id)}>请求重跑</button>
              <span>{rerunMessage || (isRerunBlocked(selectedNode.id) ? '盘中执行/订单/账本节点暂不开放重跑' : '只登记请求，不直接执行')}</span>
            </div>
          </section>
        ) : null}
        {!selectedEdge ? (
          <>
            <h4>事件时间线</h4>
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
          </>
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

function parseParamValue(value: string) {
  const trimmed = value.trim();
  if (trimmed === 'true') return true;
  if (trimmed === 'false') return false;
  if (trimmed !== '' && Number.isFinite(Number(trimmed))) return Number(trimmed);
  return value;
}

export function buildNodeAgentPrompt(node: WorkflowGraphNode, events: WorkflowEvent[]) {
  const recent = events.slice(0, 3).map((event) => (
    `${event.status}/${event.phase || '--'}: ${event.summary || event.error || '--'}`
  )).join('；');
  return `请结合 AlphaClaude 当前流程节点分析：节点=${node.name}(${node.id})；状态=${node.status}；启用=${node.enabled}；锁定=${node.locked}；摘要=${node.summary || '--'}；最近事件=${recent || '暂无'}。`;
}

export function isRerunBlocked(nodeId: string) {
  return ['state_watcher', 'fastlane_tick', 'signal_scan', 'execution_check', 'order_simulator', 'ledger_writer', 'alert_router'].includes(nodeId);
}

function WorkflowFlowNode({ data }: NodeProps<Node<FlowNodeData>>) {
  const { node, active, eventCount, inputRefs, outputRefs } = data;
  return (
    <div className={`workflow-flow-node ${node.status} ${active ? 'active' : ''}`}>
      <Handle type="target" position={Position.Left} className="workflow-handle" />
      <div className="node-topline">
        <span>{phaseLabel(node.phase || inferNodePhase(node.id))}</span>
        <b>{statusLabel(node.status)}</b>
      </div>
      <strong>{node.name}</strong>
      <small>{node.summary || (node.enabled ? '等待输入' : '已禁用')}</small>
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
  activeNodeId: string,
  activeEdgeId = '',
): { nodes: Node<FlowNodeData>[]; edges: Edge<FlowEdgeData>[] } {
  const latestEvents = new Map<string, WorkflowEvent[]>();
  sortWorkflowEvents(events).forEach((event) => {
    const rows = latestEvents.get(event.node_id) || [];
    rows.push(event);
    latestEvents.set(event.node_id, rows);
  });
  const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
  const nodes = graph.nodes.map((node, index) => {
    const phase = inferNodePhase(node.id);
    const position = blueprintPosition(node.id, index);
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
        active: node.id === activeNodeId,
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
    label: edgeDataLabel(edge, nodeById, latestEvents),
    type: 'smoothstep',
    animated: isActiveEdge(edge, activeNodeId) || activeEdgeId === `${edge.from}-${edge.to}`,
    style: {
      stroke: isActiveEdge(edge, activeNodeId) || activeEdgeId === `${edge.from}-${edge.to}` ? '#41e0c9' : 'rgba(113, 129, 151, 0.48)',
      strokeWidth: isActiveEdge(edge, activeNodeId) || activeEdgeId === `${edge.from}-${edge.to}` ? 2.2 : 1.2,
    },
    labelStyle: { fill: '#9fb5c6', fontSize: 10, fontFamily: 'JetBrains Mono, Cascadia Code, monospace' },
    labelBgStyle: { fill: 'rgba(5, 9, 14, 0.82)', fillOpacity: 0.9 },
    data: {
      from: nodeById.get(edge.from) || emptyGraphNode(edge.from),
      to: nodeById.get(edge.to) || emptyGraphNode(edge.to),
      label: edgeDataLabel(edge, nodeById, latestEvents),
      refs: edgeRefs(edge, nodeById, latestEvents),
    },
    zIndex: isActiveEdge(edge, activeNodeId) ? 8 : index,
  }));
  return { nodes, edges };
}

function sortWorkflowEvents(events: WorkflowEvent[]) {
  return [...events].sort((left, right) => workflowEventStamp(right).localeCompare(workflowEventStamp(left)));
}

function workflowEventStamp(event: WorkflowEvent) {
  return `${event.started_at || event.ended_at || ''}:${event.event_id || ''}`;
}

function inferNodePhase(nodeId: string) {
  if (['market_snapshot', 'sub_agent_a', 'sub_agent_b', 'sub_agent_c', 'merge_decision', 'bull_bear_debate', 'risk_validation', 'plan_writer'].includes(nodeId)) {
    return 'premarket';
  }
  if (['daily_report', 'ledger_pairing', 'agent_reflection'].includes(nodeId)) return 'postclose';
  return 'intraday';
}

function blueprintPosition(nodeId: string, fallback: number) {
  const positions: Record<string, { x: number; y: number }> = {
    market_snapshot: { x: 70, y: 190 },
    sub_agent_a: { x: 310, y: 70 },
    sub_agent_b: { x: 310, y: 190 },
    sub_agent_c: { x: 310, y: 310 },
    merge_decision: { x: 560, y: 190 },
    bull_bear_debate: { x: 800, y: 120 },
    risk_validation: { x: 800, y: 260 },
    plan_writer: { x: 1040, y: 190 },
    state_watcher: { x: 70, y: 520 },
    fastlane_tick: { x: 310, y: 520 },
    signal_scan: { x: 550, y: 520 },
    execution_check: { x: 790, y: 520 },
    order_simulator: { x: 1030, y: 520 },
    ledger_writer: { x: 1270, y: 520 },
    alert_router: { x: 1510, y: 520 },
    daily_report: { x: 70, y: 830 },
    ledger_pairing: { x: 310, y: 830 },
    agent_reflection: { x: 550, y: 830 },
  };
  return positions[nodeId] || { x: 70 + (fallback % 5) * 240, y: 1010 + Math.floor(fallback / 5) * 130 };
}

function edgeLabel(from: string, to: string) {
  const labels: Record<string, string> = {
    'market_snapshot-sub_agent_a': '行情快照',
    'sub_agent_a-sub_agent_b': '候选视角',
    'sub_agent_b-sub_agent_c': '交叉验证',
    'sub_agent_c-merge_decision': '候选池',
    'merge_decision-bull_bear_debate': '多空论点',
    'bull_bear_debate-risk_validation': '计划草案',
    'risk_validation-plan_writer': '风控报告',
    'plan_writer-state_watcher': 'plan.json',
    'state_watcher-fastlane_tick': '状态快照',
    'fastlane_tick-signal_scan': 'tick行情',
    'signal_scan-execution_check': '交易信号',
    'execution_check-order_simulator': '执行指令',
    'order_simulator-ledger_writer': '成交回报',
    'ledger_writer-alert_router': 'ledger.jsonl',
    'alert_router-daily_report': '盘后事件',
    'daily_report-ledger_pairing': '日报',
    'ledger_pairing-agent_reflection': '复盘样本',
  };
  return labels[`${from}-${to}`] || '数据流';
}

function edgeDataLabel(
  edge: { from: string; to: string; label?: string; refs?: string[] },
  nodeById: Map<string, WorkflowGraphNode>,
  latestEvents: Map<string, WorkflowEvent[]>,
) {
  const refs = edgeRefs(edge, nodeById, latestEvents);
  if (refs.length) return refs.slice(0, 2).join(' / ');
  return edge.label || edgeLabel(edge.from, edge.to);
}

function edgeRefs(
  edge: { from: string; to: string; refs?: string[] },
  nodeById: Map<string, WorkflowGraphNode>,
  latestEvents: Map<string, WorkflowEvent[]>,
) {
  if (edge.refs?.length) return edge.refs;
  const from = nodeById.get(edge.from);
  const to = nodeById.get(edge.to);
  const fromLatest = latestEvents.get(edge.from)?.[0];
  const toLatest = latestEvents.get(edge.to)?.[0];
  const outputs = from?.output_refs?.length ? from.output_refs : fromLatest?.output_refs || [];
  const inputs = to?.input_refs?.length ? to.input_refs : toLatest?.input_refs || [];
  const overlap = outputs.filter((ref) => inputs.includes(ref));
  if (overlap.length) return overlap;
  return outputs.length ? outputs : inputs;
}

function emptyGraphNode(id: string): WorkflowGraphNode {
  return { id, name: id, enabled: true, locked: false, status: 'idle' };
}

function isActiveEdge(edge: { from?: string; to?: string; source?: string; target?: string }, activeNodeId: string) {
  const from = edge.from || edge.source;
  const to = edge.to || edge.target;
  return Boolean(activeNodeId && (from === activeNodeId || to === activeNodeId));
}

function miniMapColor(status: string) {
  if (status === 'success') return '#41e0c9';
  if (status === 'error') return '#ff3b30';
  if (status === 'running') return '#d6a13b';
  if (status === 'skipped') return '#708099';
  return '#263047';
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    running: '运行中',
    success: '完成',
    error: '异常',
    skipped: '跳过',
    idle: '等待',
  };
  return labels[status] || status;
}

function phaseLabel(phase: string) {
  const labels: Record<string, string> = {
    premarket: '盘前',
    intraday: '盘中',
    postclose: '盘后',
    system: '系统',
  };
  return labels[phase] || phase || '未分组';
}

function formatTraceTime(value?: string) {
  if (!value) return '--';
  return value.includes('T') ? value.split('T')[1]?.slice(0, 8) || value : value.slice(11, 19) || value;
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
}: {
  title: string;
  refs: string[];
  event?: WorkflowEvent;
  onOpen: (artifact: { title: string; content: string }) => void;
  kind: 'input' | 'output';
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
      )) : <small>暂无 {title} 引用</small>}
    </div>
  );
}

function EdgeInspector({ edge }: { edge: Edge<FlowEdgeData> }) {
  const data = edge.data;
  if (!data) return null;
  return (
    <section className="workflow-trace-panel">
      <div className="edge-route">
        <strong>{data.from.name}</strong>
        <span>流向</span>
        <strong>{data.to.name}</strong>
      </div>
      <p>{data.label}</p>
      <DataRefList title="流转引用" refs={data.refs} kind="output" onOpen={() => undefined} />
      <div className="trace-meta">
        <span>上游 <b>{data.from.status}</b></span>
        <span>下游 <b>{data.to.status}</b></span>
      </div>
    </section>
  );
}

function describeRefSource(ref: string) {
  if (ref.endsWith('.json') || ref.endsWith('.jsonl')) return `本地运行目录 / ${ref}`;
  if (ref.startsWith('plan.')) return 'plan.json 中的结构化字段';
  if (ref.startsWith('premarket.')) return '盘前 Agent 阶段输出';
  if (ref.startsWith('market.') || ref.includes('quote')) return '行情/市场快照数据源';
  if (ref.startsWith('ledger')) return 'ledger.jsonl 成交账本';
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
