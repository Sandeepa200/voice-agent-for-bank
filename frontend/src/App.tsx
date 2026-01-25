import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { Phone, PhoneOff, Loader2 } from 'lucide-react';
import './App.css';

interface Message {
  role: 'user' | 'agent';
  text: string;
}

// Get API URL from env or default to localhost
const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function App() {
  const [isLoading, setIsLoading] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [callStatus, setCallStatus] = useState<'idle' | 'connecting' | 'in_call' | 'ending'>('idle');
  const [isListening, setIsListening] = useState(false);

  const sessionIdRef = useRef<string | null>(null);
  const customerIdRef = useRef<string>('user_123');
  const callActiveRef = useRef(false);

  const isPlayingRef = useRef(false);
  const busyRef = useRef(false);
  const vadRef = useRef<any>(null);
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
    } catch (_e) {
      undefined;
    }
    try {
      if (vadRef.current?.destroy) await vadRef.current.destroy();
    } catch (_e) {
      undefined;
    }
    vadRef.current = null;
    if (vadStreamRef.current) {
      vadStreamRef.current.getTracks().forEach((t) => t.stop());
      vadStreamRef.current = null;
    }
  };

  const ensureVad = () => {
    const v = (window as any).vad;
    if (!v?.MicVAD?.new) throw new Error('VAD not loaded');
    return v;
  };

  const startCapture = async () => {
    if (vadRef.current) return;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    vadStreamRef.current = stream;
    const v = ensureVad();
    vadRef.current = await v.MicVAD.new({
      stream,
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
    } catch (_e) {
      undefined;
    }
  };

  const resumeCapture = async () => {
    if (!callActiveRef.current) return;
    if (!vadRef.current) {
      await startCapture();
      return;
    }
    try {
      if (vadRef.current?.start) await vadRef.current.start();
    } catch (_e) {
      undefined;
    }
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
    setIsLoading(true);
    busyRef.current = true;
    try {
      const formData = new FormData();
      formData.append('audio', audioBlob, 'utterance.wav');
      formData.append('session_id', sessionIdRef.current);
      formData.append('customer_id', customerIdRef.current);

      const response = await axios.post(`${API_URL}/call/turn`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });

      const { user_transcript, agent_response, audio_base64 } = response.data as {
        user_transcript: string;
        agent_response: string;
        audio_base64: string | null;
      };

      setMessages((prev) => [...prev, { role: 'user', text: user_transcript }, { role: 'agent', text: agent_response }]);
      await playAudioResponse(audio_base64);
    } finally {
      setIsLoading(false);
      busyRef.current = false;
    }
  };

  const startCall = async () => {
    setCallStatus('connecting');
    setMessages([]);
    try {
      const formData = new FormData();
      formData.append('customer_id', customerIdRef.current);
      const resp = await axios.post(`${API_URL}/call/start`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });

      const { session_id, agent_response, audio_base64 } = resp.data as {
        session_id: string;
        agent_response: string;
        audio_base64: string | null;
      };

      sessionIdRef.current = session_id;
      callActiveRef.current = true;
      setMessages([{ role: 'agent', text: agent_response }]);
      await playAudioResponse(audio_base64);
      await startCapture();
      setCallStatus('in_call');
    } catch (error) {
      console.error(error);
      alert('Could not start the call. Check microphone permissions and backend status.');
      setCallStatus('idle');
      callActiveRef.current = false;
      void stopCapture();
    }
  };

  const endCall = async () => {
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

  return (
    <div className="app-container">
      <header className="header">
        <h1>Bank ABC Voice Agent</h1>
        <p>{callStatus === 'in_call' ? (isListening ? 'Listening…' : 'On call…') : 'Start a call to speak naturally'}</p>
      </header>

      <div className="chat-window">
        {messages.length === 0 && (
          <div className="empty-state">
            <p>No conversation yet. Say "Hello" or "Check my balance".</p>
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
          className={`mic-button ${callStatus === 'in_call' ? 'recording' : ''}`}
          onClick={callStatus === 'in_call' ? endCall : startCall}
          disabled={callStatus === 'connecting' || callStatus === 'ending'}
        >
          {callStatus === 'in_call' ? <PhoneOff size={32} /> : <Phone size={32} />}
        </button>
        <p className="instruction">
          {callStatus === 'in_call' ? 'End Call' : 'Start Call'}
        </p>
      </div>
    </div>
  );
}

export default App;
