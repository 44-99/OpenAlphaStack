import { useMemo, useState } from 'react';
import { api } from '../api';
import type { WorkflowEvent, WorkflowGraph, WorkflowGraphNode } from '../types';

export function WorkflowBoard({ graph, events }: { graph?: WorkflowGraph; events: WorkflowEvent[] }) {
  const [selectedNodeId, setSelectedNodeId] = useState('');
  const [artifact, setArtifact] = useState<{ title: string; content: string } | null>(null);
  const selectedNode = useMemo(() => {
    if (!graph?.nodes.length) return undefined;
    return graph.nodes.find((node) => node.id === selectedNodeId) || graph.nodes[0];
  }, [graph, selectedNodeId]);
  const selectedEvents = useMemo(() => {
    if (!selectedNode) return events;
    return events.filter((event) => event.node_id === selectedNode.id);
  }, [events, selectedNode]);

  if (!graph) return <div className="empty">暂无工作流数据</div>;

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
          {selectedNode?.locked ? <span className="lock-pill">锁定</span> : null}
        </header>
        <p>{selectedNode?.summary || '暂无摘要'}</p>
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
