import { useEffect, useRef, useState } from 'react';
import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';
import { Bot, Code2, PanelRightClose, TerminalSquare } from 'lucide-react';
import type { AgentProvider } from '../types';

interface AgentPanelProps {
  collapsed: boolean;
  onToggle: () => void;
  injection?: { id: number; text: string };
}

const providers: Array<{ key: AgentProvider; label: string; command: string }> = [
  { key: 'claude', label: 'Claude Code', command: 'claude' },
  { key: 'codex', label: 'Codex CLI', command: 'codex --no-alt-screen' },
];

export function AgentPanel({ collapsed, onToggle, injection }: AgentPanelProps) {
  const [provider, setProvider] = useState<AgentProvider>('claude');
  const [started, setStarted] = useState<Set<AgentProvider>>(() => new Set());

  function selectProvider(next: AgentProvider) {
    setProvider(next);
    setStarted((current) => {
      if (current.has(next)) return current;
      const copy = new Set(current);
      copy.add(next);
      return copy;
    });
  }

  return (
    <aside className={`agent-panel terminal-mode ${collapsed ? 'collapsed' : ''}`}>
      <button className="agent-collapsed-toggle" onClick={onToggle} title="展开 Agent 终端">
          <TerminalSquare size={20} />
          <span>Agent</span>
      </button>
      <header className="agent-header">
        <div className="agent-title">
          <span className="agent-mark" aria-hidden="true">{provider === 'codex' ? <Code2 size={18} /> : <Bot size={18} />}</span>
          <div>
            <strong>Agent</strong>
          </div>
        </div>
        <button className="ghost-button agent-collapse" onClick={onToggle}><PanelRightClose size={15} />收起</button>
      </header>

      <div className="agent-switch">
        {providers.map((item) => (
          <button
            key={item.key}
            className={provider === item.key && started.has(item.key) ? 'active' : ''}
            onClick={() => selectProvider(item.key)}
            title={started.has(item.key) ? `切换到 ${item.command}` : `启动 ${item.command}`}
          >
            {item.key === 'codex' ? <Code2 size={15} /> : <Bot size={15} />}
            {item.label}
          </button>
        ))}
      </div>

      <div className="agent-terminal-stack">
        {started.size === 0 ? (
          <div className="agent-terminal-empty">
            <strong>Agent 终端未启动</strong>
            <span>选择 Claude Code 或 Codex CLI 后才会创建终端连接。</span>
          </div>
        ) : null}
        {providers.map((item) => started.has(item.key) ? (
          <AgentTerminalPane
            key={item.key}
            active={provider === item.key}
            provider={item.key}
            command={item.command}
            injection={provider === item.key ? injection : undefined}
          />
        ) : null)}
      </div>
    </aside>
  );
}

function AgentTerminalPane({ active, provider, command, injection }: {
  active: boolean;
  provider: AgentProvider;
  command: string;
  injection?: { id: number; text: string };
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const lastInjectionIdRef = useRef(0);

  useEffect(() => {
    if (!hostRef.current) return;

    const term = new Terminal({
      cursorBlink: true,
      convertEol: true,
      fontFamily: '"JetBrains Mono", "Cascadia Code", Consolas, monospace',
      fontSize: 12,
      lineHeight: 1.18,
      theme: {
        background: '#05090e',
        foreground: '#d8e0ea',
        cursor: '#41e0c9',
        selectionBackground: '#2e4d55',
        black: '#05090e',
        red: '#ff3b30',
        green: '#22b573',
        yellow: '#d6a13b',
        blue: '#4d8dff',
        magenta: '#c082ff',
        cyan: '#41e0c9',
        white: '#d8e0ea',
        brightBlack: '#718197',
        brightRed: '#ff6b61',
        brightGreen: '#4fd08f',
        brightYellow: '#e6b95f',
        brightBlue: '#73a5ff',
        brightMagenta: '#d3a0ff',
        brightCyan: '#76f0dd',
        brightWhite: '#f4f7fb',
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(hostRef.current);
    fit.fit();
    term.focus();
    term.writeln(`\x1b[36mAlphaClaude Agent 终端\x1b[0m`);
    term.writeln(`\x1b[90m启动 ${command}\x1b[0m`);

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const socket = new WebSocket(`${protocol}://${window.location.host}/api/agent/terminal/${provider}?cols=${term.cols}&rows=${term.rows}`);
    termRef.current = term;
    fitRef.current = fit;
    socketRef.current = socket;

    socket.addEventListener('open', () => {
      socket.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
    });
    socket.addEventListener('message', (event) => {
      if (typeof event.data === 'string') term.write(event.data);
    });
    socket.addEventListener('close', () => {
      term.writeln('\r\n\x1b[33m[Agent terminal disconnected]\x1b[0m');
    });
    socket.addEventListener('error', () => {
      term.writeln('\r\n\x1b[31m[Agent terminal websocket error]\x1b[0m');
    });

    const inputDisposable = term.onData((data) => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'input', data }));
      }
    });
    const observer = new ResizeObserver(() => {
      fit.fit();
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
      }
    });
    observer.observe(hostRef.current);

    return () => {
      observer.disconnect();
      inputDisposable.dispose();
      socket.close();
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
      socketRef.current = null;
    };
  }, [command, provider]);

  useEffect(() => {
    if (!active) return;
    fitRef.current?.fit();
    termRef.current?.focus();
  }, [active]);

  useEffect(() => {
    if (!shouldApplyTerminalInjection(active, injection?.id, lastInjectionIdRef.current)) return;
    const socket = socketRef.current;
    const term = termRef.current;
    if (!injection) return;
    if (!socket || socket.readyState !== WebSocket.OPEN || !term) return;
    lastInjectionIdRef.current = injection.id;
    term.focus();
    socket.send(JSON.stringify({ type: 'input', data: injection.text }));
  }, [active, injection]);

  return <div className={`agent-terminal-pane ${active ? 'active' : ''}`} ref={hostRef} />;
}

export function shouldApplyTerminalInjection(active: boolean, injectionId: number | undefined, lastInjectionId: number) {
  return active && typeof injectionId === 'number' && injectionId !== lastInjectionId;
}
