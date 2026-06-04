import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import type { WorkflowConfig, WorkflowConfigNode, WorkflowEvent, WorkflowGraph, WorkflowGraphNode } from '../types';

export function WorkflowBoard({ graph, events, onSendToAgent }: {
  graph?: WorkflowGraph;
  events: WorkflowEvent[];
  onSendToAgent?: (text: string) => void;
}) {
  const [selectedNodeId, setSelectedNodeId] = useState('');
  const [artifact, setArtifact] = useState<{ title: string; content: string } | null>(null);
  const [config, setConfig] = useState<WorkflowConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [configMessage, setConfigMessage] = useState('');
  const selectedNode = useMemo(() => {
    if (!graph?.nodes.length) return undefined;
    return graph.nodes.find((node) => node.id === selectedNodeId) || graph.nodes[0];
  }, [graph, selectedNodeId]);
  const selectedEvents = useMemo(() => {
    if (!selectedNode) return events;
    return events.filter((event) => event.node_id === selectedNode.id);
  }, [events, selectedNode]);
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
        <div className="workflow-node-grid">
          {graph.nodes.map((node) => (
            <WorkflowNode key={node.id} node={node} active={selectedNode?.id === node.id} onSelect={() => setSelectedNodeId(node.id)} />
          ))}
        </div>
      </div>
      <aside className="workflow-inspector">
        <header>
          <strong>{selectedNode?.name || '节点详情'}</strong>
          <span className="workflow-inspector-actions">
            {selectedNode && onSendToAgent ? (
              <button onClick={() => onSendToAgent(buildNodeAgentPrompt(selectedNode, selectedEvents))}>发送到 Agent</button>
            ) : null}
            {selectedNode?.locked ? <span className="lock-pill">锁定</span> : null}
          </span>
        </header>
        <p>{selectedNode?.summary || '暂无摘要'}</p>
        {selectedNode && selectedConfig ? (
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
          </section>
        ) : null}
        <h4>事件时间线</h4>
        <div className="workflow-events">
          {selectedEvents.length ? selectedEvents.map((event) => (
            <article className={`workflow-event ${event.status}`} key={event.event_id}>
              <span>{event.phase || '--'}</span>
              <strong>{event.node_name}</strong>
              <p>{event.summary || '--'}</p>
              {event.error ? <code>{event.error}</code> : null}
              {event.artifact_dir ? (
                <div className="artifact-actions">
                  {['input.json', 'output.json', 'error.txt'].map((name) => (
                    <button key={name} onClick={() => loadArtifact(event, name, setArtifact)}>{name}</button>
                  ))}
                </div>
              ) : null}
            </article>
          )) : <div className="empty compact">该节点暂无事件</div>}
        </div>
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

function buildNodeAgentPrompt(node: WorkflowGraphNode, events: WorkflowEvent[]) {
  const recent = events.slice(0, 3).map((event) => (
    `${event.status}/${event.phase || '--'}: ${event.summary || event.error || '--'}`
  )).join('；');
  return `请结合 AlphaClaude 当前流程节点分析：节点=${node.name}(${node.id})；状态=${node.status}；启用=${node.enabled}；锁定=${node.locked}；摘要=${node.summary || '--'}；最近事件=${recent || '暂无'}。`;
}

function WorkflowNode({ node, active, onSelect }: { node: WorkflowGraphNode; active: boolean; onSelect: () => void }) {
  return (
    <button className={`workflow-node ${node.status} ${active ? 'active' : ''}`} onClick={onSelect}>
      <span>{node.status}</span>
      <strong>{node.name}</strong>
      <small>{node.enabled ? '启用' : '禁用'}{node.locked ? ' / 锁定' : ''}</small>
    </button>
  );
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
