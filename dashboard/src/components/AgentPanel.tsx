import { useEffect, useRef, useState } from 'react';
import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';
import { Bot, Code2, PanelRightClose, PlugZap, TerminalSquare } from 'lucide-react';
import type { AgentProvider, KlinePeriod, OverlayKind } from '../types';

interface AgentPanelProps {
  collapsed: boolean;
  selectedCode: string;
  period: KlinePeriod;
  overlay: OverlayKind;
  onToggle: () => void;
}

const providers: Array<{ key: AgentProvider; label: string; command: string }> = [
  { key: 'claude', label: 'Claude Code', command: 'claude' },
  { key: 'codex', label: 'Codex CLI', command: 'codex --no-alt-screen' },
];

export function AgentPanel({ collapsed, selectedCode, period, overlay, onToggle }: AgentPanelProps) {
  const [provider, setProvider] = useState<AgentProvider>('claude');
  const [connected, setConnected] = useState(false);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (collapsed || !hostRef.current) return;

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
        red: '#f05b44',
        green: '#16a06b',
        yellow: '#d6a13b',
        blue: '#4d8dff',
        magenta: '#c082ff',
        cyan: '#41e0c9',
        white: '#d8e0ea',
        brightBlack: '#718197',
        brightRed: '#ff765f',
        brightGreen: '#28d08a',
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
    term.writeln(`\x1b[36mAlphaClaude Agent Terminal\x1b[0m`);
    term.writeln(`\x1b[90m${selectedCode} / ${period.toUpperCase()} / ${overlay} | launching ${currentProvider(provider).command}\x1b[0m`);

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const socket = new WebSocket(`${protocol}://${window.location.host}/api/agent/terminal/${provider}?cols=${term.cols}&rows=${term.rows}`);
    socketRef.current = socket;
    termRef.current = term;
    fitRef.current = fit;

    socket.addEventListener('open', () => {
      setConnected(true);
      socket.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
    });
    socket.addEventListener('message', (event) => {
      if (typeof event.data === 'string') term.write(event.data);
    });
    socket.addEventListener('close', () => {
      setConnected(false);
      term.writeln('\r\n\x1b[33m[Agent terminal disconnected]\x1b[0m');
    });
    socket.addEventListener('error', () => {
      setConnected(false);
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
      setConnected(false);
    };
  }, [collapsed, overlay, period, provider, selectedCode]);

  if (collapsed) {
    return (
      <aside className="agent-rail">
        <button onClick={onToggle} title="展开 Agent Terminal">
          <TerminalSquare size={20} />
          <span>Agent</span>
        </button>
      </aside>
    );
  }

  return (
    <aside className="agent-panel terminal-mode">
      <header className="agent-header">
        <div className="agent-title">
          <span className="agent-mark" aria-hidden="true">{provider === 'codex' ? <Code2 size={18} /> : <Bot size={18} />}</span>
          <div>
            <strong>Agent</strong>
            <span>{connected ? 'connected' : 'connecting'} / {selectedCode} / {period.toUpperCase()} / {overlay}</span>
          </div>
        </div>
        <button className="ghost-button agent-collapse" onClick={onToggle}><PanelRightClose size={15} />收起</button>
      </header>

      <div className="agent-switch">
        {providers.map((item) => (
          <button
            key={item.key}
            className={provider === item.key ? 'active' : ''}
            onClick={() => setProvider(item.key)}
            title={`启动 ${item.command}`}
          >
            {item.key === 'codex' ? <Code2 size={15} /> : <Bot size={15} />}
            {item.label}
          </button>
        ))}
      </div>

      <div className="agent-terminal-status">
        <span><PlugZap size={13} /> PowerShell</span>
        <span>{currentProvider(provider).command}</span>
      </div>

      <div className="agent-terminal" ref={hostRef} />
    </aside>
  );
}

function currentProvider(provider: AgentProvider) {
  return providers.find((item) => item.key === provider) || providers[0];
}
