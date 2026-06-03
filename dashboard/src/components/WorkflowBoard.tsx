import { useMemo, useState } from 'react';
import type { WorkflowEvent, WorkflowGraph, WorkflowGraphNode } from '../types';

export function WorkflowBoard({ graph, events }: { graph?: WorkflowGraph; events: WorkflowEvent[] }) {
  const [selectedNodeId, setSelectedNodeId] = useState('');
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
            </article>
          )) : <div className="empty compact">该节点暂无事件</div>}
        </div>
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
