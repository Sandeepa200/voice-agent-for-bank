import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { Phone, PhoneOff, Loader2, Shield, ArrowLeft, Save, CheckCircle2 } from 'lucide-react';
import './App.css';

interface Message {
  role: 'user' | 'agent';
  text: string;
}

// Get API URL from env or default to localhost
const API_URL = import.meta.env.VITE_API_URL || '';
const ONNX_WASM_BASE_PATH = 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.22.0/dist/';
const VAD_BASE_ASSET_PATH = 'https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.29/dist/';
const ENV_KEY = 'dev';

type ToolFlag = { enabled: boolean; params?: unknown };
type ToolFlags = Record<string, ToolFlag>;

type MicVADInstance = {
  pause?: () => Promise<void>;
  destroy?: () => Promise<void>;
  start?: () => Promise<void>;
};

type MicVADNew = (args: unknown) => Promise<MicVADInstance>;

type MicVADModule = {
  MicVAD: {
    new: MicVADNew;
  };
};

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

function App() {
  const [isLoading, setIsLoading] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [callStatus, setCallStatus] = useState<'idle' | 'connecting' | 'active' | 'ending'>('idle');
  const [isListening, setIsListening] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isVerified, setIsVerified] = useState<boolean>(false);
  const [view, setView] = useState<'call' | 'admin'>('call');

  const [adminLoading, setAdminLoading] = useState(false);
  const [adminMessage, setAdminMessage] = useState<string | null>(null);

  const [baseSystemPrompt, setBaseSystemPrompt] = useState('');
  const [routerPrompt, setRouterPrompt] = useState('');
  const [toolFlags, setToolFlags] = useState<ToolFlags>({});
  const [routingRulesText, setRoutingRulesText] = useState('{}');

  const sessionIdRef = useRef<string | null>(null);
  const callActiveRef = useRef(false);

  const isPlayingRef = useRef(false);
  const busyRef = useRef(false);
  const vadRef = useRef<MicVADInstance | null>(null);
  const vadStreamRef = useRef<MediaStream | null>(null);
  const pendingAudioRef = useRef<Float32Array[]>([]);
  const pendingTimerRef = useRef<number | null>(null);

  const concatFloat32 = (chunks: Float32Array[]) => {
    let total = 0;
    for (const c of chunks) total += c.length;
    const out = new Float32Array(total);
    let offset = 0;
    for (const c of chunks) {
      out.set(c, offset);
      offset += c.length;
    }
    return out;
  };

  const float32ToWavBlob = (audio: Float32Array, sampleRate: number) => {
    const numChannels = 1;
    const bytesPerSample = 2;
    const blockAlign = numChannels * bytesPerSample;
    const byteRate = sampleRate * blockAlign;
    const dataSize = audio.length * bytesPerSample;
    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);

    const writeString = (offset: number, s: string) => {
      for (let i = 0; i < s.length; i += 1) view.setUint8(offset + i, s.charCodeAt(i));
    };

    writeString(0, 'RIFF');
    view.setUint32(4, 36 + dataSize, true);
    writeString(8, 'WAVE');
    writeString(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, byteRate, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, 16, true);
    writeString(36, 'data');
    view.setUint32(40, dataSize, true);

    let offset = 44;
    for (let i = 0; i < audio.length; i += 1) {
      const s = Math.max(-1, Math.min(1, audio[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      offset += 2;
    }

    return new Blob([buffer], { type: 'audio/wav' });
  };

  const stopCapture = async () => {
    if (pendingTimerRef.current) {
      window.clearTimeout(pendingTimerRef.current);
      pendingTimerRef.current = null;
    }
    pendingAudioRef.current = [];
    setIsListening(false);
    try {
      if (vadRef.current?.pause) await vadRef.current.pause();
    } catch {}
    try {
      if (vadRef.current?.destroy) await vadRef.current.destroy();
    } catch {}
    vadRef.current = null;
    if (vadStreamRef.current) {
      vadStreamRef.current.getTracks().forEach((t) => t.stop());
      vadStreamRef.current = null;
    }
  };

  const ensureVad = (): MicVADModule => {
    const v = (window as unknown as { vad?: { MicVAD?: { new?: MicVADNew } } }).vad;
    const micNew = v?.MicVAD?.new;
    if (!micNew) throw new Error('VAD not loaded');
    return { MicVAD: { new: micNew } };
  };

  const formatStartCallError = (error: unknown) => {
    if (axios.isAxiosError(error)) {
      if (error.response) {
        const status = error.response.status;
        const data: unknown = error.response.data;
        let detail: string | undefined;
        if (typeof data === 'string') {
          detail = data;
        } else if (isRecord(data)) {
          if (typeof data.detail === 'string') detail = data.detail;
          else if (typeof data.error === 'string') detail = data.error;
        }
        if (!detail) detail = JSON.stringify(data);
        return `Backend error (${status}): ${detail}`;
      }
      if (error.request) {
        return `Could not reach backend at ${API_URL}. Is it running?`;
      }
      return `Request error: ${error.message}`;
    }

    if (error instanceof Error) {
      const name = error.name;
      if (error.message === 'VAD not loaded') {
        return 'Voice activity detector failed to load (blocked CDN?). Disable ad blockers and refresh.';
      }
      if (name === 'NotAllowedError') {
        return 'Microphone permission was denied. Allow mic access in the browser and try again.';
      }
      if (name === 'NotFoundError') {
        return 'No microphone device found. Plug in a mic/headset and try again.';
      }
      if (name === 'NotReadableError') {
        return 'Microphone is already in use by another app. Close other apps and try again.';
      }
      if (name === 'SecurityError') {
        return 'Microphone access requires HTTPS or localhost.';
      }
      return error.message || 'Unknown error';
    }

    return 'Unknown error';
  };

  const startCapture = async () => {
    if (vadRef.current) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('Microphone API not available in this browser/context.');
    }
    if (
      !window.isSecureContext &&
      window.location.hostname !== 'localhost' &&
      window.location.hostname !== '127.0.0.1'
    ) {
      const err = new Error('Microphone access requires HTTPS or localhost.');
        (err as Error & { name: string }).name = 'SecurityError';
      throw err;
    }

    const v = ensureVad();
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    vadStreamRef.current = stream;
    vadRef.current = await v.MicVAD.new({
      stream,
      onnxWASMBasePath: ONNX_WASM_BASE_PATH,
      baseAssetPath: VAD_BASE_ASSET_PATH,
      onSpeechStart: () => {
        setIsListening(true);
      },
      onSpeechEnd: (audio: Float32Array) => {
        setIsListening(false);
        if (!callActiveRef.current) return;
        if (busyRef.current) return;
        pendingAudioRef.current.push(audio);
        if (pendingTimerRef.current) window.clearTimeout(pendingTimerRef.current);
        pendingTimerRef.current = window.setTimeout(async () => {
          pendingTimerRef.current = null;
          if (!callActiveRef.current) return;
          if (busyRef.current) {
            pendingAudioRef.current = [];
            return;
          }
          const merged = concatFloat32(pendingAudioRef.current);
          pendingAudioRef.current = [];
          if (merged.length < 1600) return;
          const wav = float32ToWavBlob(merged, 16000);
          await sendTurn(wav);
        }, 250);
      },
    });
  };

  const pauseCapture = async () => {
    if (pendingTimerRef.current) {
      window.clearTimeout(pendingTimerRef.current);
      pendingTimerRef.current = null;
    }
    pendingAudioRef.current = [];
    setIsListening(false);
    try {
      if (vadRef.current?.pause) await vadRef.current.pause();
    } catch {}
  };

  const resumeCapture = async () => {
    if (!callActiveRef.current) return;
    if (!vadRef.current) {
      await startCapture();
      return;
    }
    try {
      if (vadRef.current?.start) await vadRef.current.start();
    } catch {}
  };

  useEffect(() => {
    return () => {
      void stopCapture();
    };
  }, []);

  const playAudioResponse = async (base64Audio: string | null) => {
    if (!base64Audio) return;
    isPlayingRef.current = true;
    busyRef.current = true;
    await pauseCapture();
    try {
      const audio = new Audio(`data:audio/mp3;base64,${base64Audio}`);
      await new Promise<void>((resolve) => {
        audio.onended = () => resolve();
        audio.onerror = () => resolve();
        audio.play().catch(() => resolve());
      });
    } finally {
      isPlayingRef.current = false;
      busyRef.current = false;
    }
    await resumeCapture();
  };

  const sendTurn = async (audioBlob: Blob) => {
    if (!sessionIdRef.current) return;
    const pendingTranscript = '(transcribing...)';
    setMessages((prev) => [...prev, { role: 'user', text: pendingTranscript }]);
    setIsLoading(true);
    busyRef.current = true;
    try {
      const formData = new FormData();
      formData.append('audio', audioBlob, 'utterance.wav');
      formData.append('session_id', sessionIdRef.current);

      const response = await axios.post(`${API_URL}/call/turn`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });

      const { user_transcript, agent_response, audio_base64, is_verified } = response.data as {
        user_transcript: string;
        agent_response: string;
        audio_base64: string | null;
        is_verified?: boolean;
      };
      
      if (typeof is_verified === 'boolean') {
        setIsVerified(is_verified);
      }

      setMessages((prev) => {
        const next = [...prev];
        for (let i = next.length - 1; i >= 0; i -= 1) {
          if (next[i].role === 'user' && next[i].text === pendingTranscript) {
            next[i] = { role: 'user', text: user_transcript };
            break;
          }
        }
        next.push({ role: 'agent', text: agent_response });
        return next;
      });
      await playAudioResponse(audio_base64);
    } catch (error) {
      setMessages((prev) => {
        const next = [...prev];
        for (let i = next.length - 1; i >= 0; i -= 1) {
          if (next[i].role === 'user' && next[i].text === pendingTranscript) {
            next[i] = { role: 'user', text: '(could not transcribe)' };
            break;
          }
        }
        return next;
      });
      throw error;
    } finally {
      setIsLoading(false);
      busyRef.current = false;
    }
  };

  const startCall = async () => {
    setCallStatus('connecting');
    setMessages([]);
    setErrorMessage(null);
    try {
      const formData = new FormData();
      const resp = await axios.post(`${API_URL}/call/start`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });

      const { session_id, agent_response, audio_base64, is_verified } = resp.data as {
        session_id: string;
        agent_response: string;
        audio_base64: string | null;
        is_verified?: boolean;
      };

      sessionIdRef.current = session_id;
      callActiveRef.current = true;
      setCallStatus('active');
      setIsVerified(!!is_verified);
      setMessages([{ role: 'agent', text: agent_response }]);
      await playAudioResponse(audio_base64);
      await startCapture();
    } catch (error) {
      console.error(error);
      const msg = formatStartCallError(error);
      setErrorMessage(msg);
      setCallStatus('idle');
      callActiveRef.current = false;
      void stopCapture();
    }
  };

  const endCall = async () => {
    setErrorMessage(null);
    if (!sessionIdRef.current) {
      setCallStatus('idle');
      callActiveRef.current = false;
      void stopCapture();
      return;
    }
    setCallStatus('ending');
    callActiveRef.current = false;
    void stopCapture();
    try {
      const formData = new FormData();
      formData.append('session_id', sessionIdRef.current);
      const resp = await axios.post(`${API_URL}/call/end`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });

      const { agent_response, audio_base64 } = resp.data as { agent_response: string; audio_base64: string | null };
      setMessages((prev) => [...prev, { role: 'agent', text: agent_response }]);
      await playAudioResponse(audio_base64);
    } catch (error) {
      console.error(error);
    } finally {
      sessionIdRef.current = null;
      setCallStatus('idle');
    }
  };

  const loadAdmin = async () => {
    setAdminLoading(true);
    setAdminMessage(null);
    try {
      const cfgResp = await axios.get(`${API_URL}/admin/config`, { params: { env: ENV_KEY } });
      const cfg: unknown = cfgResp.data;
      if (!isRecord(cfg)) {
        setBaseSystemPrompt('');
        setRouterPrompt('');
        setToolFlags({});
        setRoutingRulesText('{}');
        return;
      }
      setBaseSystemPrompt(typeof cfg.base_system_prompt === 'string' ? cfg.base_system_prompt : '');
      setRouterPrompt(typeof cfg.router_prompt === 'string' ? cfg.router_prompt : '');
      setToolFlags(isRecord(cfg.tool_flags) ? (cfg.tool_flags as ToolFlags) : {});
      setRoutingRulesText(JSON.stringify(isRecord(cfg.routing_rules) ? cfg.routing_rules : {}, null, 2));
    } catch (e) {
      setAdminMessage(formatStartCallError(e));
    } finally {
      setAdminLoading(false);
    }
  };

  const openAdmin = async () => {
    if (callStatus !== 'idle') return;
    setView('admin');
    await loadAdmin();
  };

  const closeAdmin = () => {
    setAdminMessage(null);
    setView('call');
  };

  const savePrompts = async () => {
    setAdminLoading(true);
    setAdminMessage(null);
    try {
      await axios.put(
        `${API_URL}/admin/config`,
        { base_system_prompt: baseSystemPrompt, router_prompt: routerPrompt },
        { params: { env: ENV_KEY } }
      );
      setAdminMessage('Saved prompts.');
    } catch (e) {
      setAdminMessage(formatStartCallError(e));
    } finally {
      setAdminLoading(false);
    }
  };

  const saveTools = async () => {
    setAdminLoading(true);
    setAdminMessage(null);
    try {
      await axios.put(`${API_URL}/admin/tools`, { tool_flags: toolFlags }, { params: { env: ENV_KEY } });
      setAdminMessage('Saved tools.');
    } catch (e) {
      setAdminMessage(formatStartCallError(e));
    } finally {
      setAdminLoading(false);
    }
  };

  const saveRouting = async () => {
    setAdminLoading(true);
    setAdminMessage(null);
    try {
      const parsed = JSON.parse(routingRulesText || '{}');
      await axios.put(`${API_URL}/admin/routing`, { routing_rules: parsed }, { params: { env: ENV_KEY } });
      setAdminMessage('Saved routing rules.');
    } catch (e) {
      setAdminMessage(e instanceof Error ? e.message : formatStartCallError(e));
    } finally {
      setAdminLoading(false);
    }
  };

  const knownTools = [
    'verify_identity',
    'get_verification_status',
    'get_account_balance',
    'get_recent_transactions',
    'block_card',
    'get_customer_cards',
    'request_statement',
    'update_address',
    'report_cash_not_dispensed',
  ];

  const toggleTool = (name: string, enabled: boolean) => {
    setToolFlags((prev) => ({ ...prev, [name]: { ...(prev[name] || {}), enabled } }));
  };

  return (
    <div className="app-container">
      <header className="header">
        <div className="header-row">
          <div className="header-title">
            <h1>Bank ABC Voice Agent</h1>
            <p>
              {view === 'admin'
                ? 'Admin dashboard'
                : callStatus === 'active'
                ? isListening
                  ? 'Listening…'
                  : 'On call…'
                : 'Start a call to speak naturally'}
            </p>
          </div>
          <div className="header-actions">
            {callStatus === 'active' && isVerified && (
              <span className="verified-badge">
                <CheckCircle2 size={16} /> Verified
              </span>
            )}
            {view === 'call' ? (
              <button className="header-button" onClick={() => void openAdmin()} disabled={callStatus !== 'idle'}>
                <Shield size={16} /> Admin
              </button>
            ) : (
              <button className="header-button" onClick={closeAdmin}>
                <ArrowLeft size={16} /> Back
              </button>
            )}
          </div>
        </div>
        {errorMessage && <p className="error-banner">{errorMessage}</p>}
      </header>

      {view === 'call' ? (
        <>
          <div className="chat-window">
            {messages.length === 0 && (
              <div className="empty-state">
                <p>No conversation yet. Click call button below to start a call".</p>
              </div>
            )}
            {messages.map((msg, idx) => (
              <div key={idx} className={`message ${msg.role}`}>
                <div className="message-bubble">
                  <strong>{msg.role === 'user' ? 'You' : 'Agent'}:</strong> {msg.text}
                </div>
              </div>
            ))}
            {isLoading && (
              <div className="message agent">
                <div className="message-bubble loading">
                  <Loader2 className="animate-spin" size={16} /> Thinking...
                </div>
              </div>
            )}
          </div>

          <div className="controls">
            <button
              className={`mic-button ${callStatus === 'active' ? 'recording' : ''}`}
              onClick={callStatus === 'active' ? endCall : startCall}
              disabled={callStatus === 'connecting'}
            >
              {callStatus === 'active' ? <PhoneOff size={32} /> : <Phone size={32} />}
            </button>
            <p className="instruction">{callStatus === 'active' ? 'End Call' : 'Start Call'}</p>
          </div>
        </>
      ) : (
        <div className="admin-window">
          <div className="admin-toolbar">
            {adminMessage && <div className="admin-message">{adminMessage}</div>}
          </div>

          <div className="admin-section">
            <div className="admin-section-header">
              <h2>Prompts</h2>
              <button className="admin-save" onClick={() => void savePrompts()} disabled={adminLoading}>
                <Save size={16} /> Save
              </button>
            </div>
            <label className="admin-label">Base System Prompt</label>
            <textarea className="admin-textarea" value={baseSystemPrompt} onChange={(e) => setBaseSystemPrompt(e.target.value)} />
            <label className="admin-label">Router Prompt</label>
            <textarea className="admin-textarea" value={routerPrompt} onChange={(e) => setRouterPrompt(e.target.value)} />
          </div>

          <div className="admin-section">
            <div className="admin-section-header">
              <h2>Tools</h2>
              <button className="admin-save" onClick={() => void saveTools()} disabled={adminLoading}>
                <Save size={16} /> Save
              </button>
            </div>
            <div className="tools-grid">
              {knownTools.map((name) => (
                <label key={name} className="tool-item">
                  <input type="checkbox" checked={toolFlags[name]?.enabled ?? true} onChange={(e) => toggleTool(name, e.target.checked)} />
                  <span>{name}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="admin-section">
            <div className="admin-section-header">
              <h2>Routing Rules (JSON)</h2>
              <button className="admin-save" onClick={() => void saveRouting()} disabled={adminLoading}>
                <Save size={16} /> Save
              </button>
            </div>
            <textarea className="admin-textarea" value={routingRulesText} onChange={(e) => setRoutingRulesText(e.target.value)} />
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
