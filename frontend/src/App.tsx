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

  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafIdRef = useRef<number | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const isPlayingRef = useRef(false);
  const silenceSinceRef = useRef<number | null>(null);
  const recordingStartRef = useRef<number | null>(null);
  const resumeAtRef = useRef<number>(0);

  const stopLoop = () => {
    if (rafIdRef.current) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
    if (audioContextRef.current) {
      audioContextRef.current.close().catch(() => undefined);
      audioContextRef.current = null;
    }
    analyserRef.current = null;
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    setIsListening(false);
  };

  const pauseCapture = () => {
    if (rafIdRef.current) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
    if (audioContextRef.current) {
      audioContextRef.current.close().catch(() => undefined);
      audioContextRef.current = null;
    }
    analyserRef.current = null;
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    setIsListening(false);
  };

  useEffect(() => {
    return () => {
      stopLoop();
    };
  }, []);

  const resumeCapture = async () => {
    if (!callActiveRef.current) return;
    if (rafIdRef.current) return;
    resumeAtRef.current = Date.now() + 600;
    await startVadLoop();
  };

  const playAudioResponse = async (base64Audio: string | null) => {
    if (!base64Audio) return;
    isPlayingRef.current = true;
    pauseCapture();
    try {
      const audio = new Audio(`data:audio/mp3;base64,${base64Audio}`);
      await new Promise<void>((resolve) => {
        audio.onended = () => resolve();
        audio.onerror = () => resolve();
        audio.play().catch(() => resolve());
      });
    } finally {
      isPlayingRef.current = false;
    }
    await resumeCapture();
  };

  const sendTurn = async (audioBlob: Blob) => {
    if (!sessionIdRef.current) return;
    setIsLoading(true);
    try {
      const formData = new FormData();
      formData.append('audio', audioBlob, 'utterance.webm');
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
    }
  };

  const startVadLoop = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    streamRef.current = stream;

    const audioContext = new AudioContext();
    audioContextRef.current = audioContext;
    const source = audioContext.createMediaStreamSource(stream);
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 2048;
    analyserRef.current = analyser;
    source.connect(analyser);

    const data = new Uint8Array(analyser.fftSize);
    const startThreshold = 0.03;
    const stopThreshold = 0.02;
    const silenceMsToStop = 900;
    const minRecordMs = 450;

    const tick = () => {
      const a = analyserRef.current;
      if (!a) return;

      a.getByteTimeDomainData(data);
      let sumSquares = 0;
      for (let i = 0; i < data.length; i += 1) {
        const v = (data[i] - 128) / 128;
        sumSquares += v * v;
      }
      const rms = Math.sqrt(sumSquares / data.length);

      const now = Date.now();
      const recorder = mediaRecorderRef.current;
      const isRecordingNow = recorder?.state === 'recording';

      if (!isPlayingRef.current && !isLoading && now >= resumeAtRef.current) {
        if (!isRecordingNow && rms > startThreshold) {
          silenceSinceRef.current = null;
          recordingStartRef.current = now;
          chunksRef.current = [];
          const mr = new MediaRecorder(streamRef.current!);
          mediaRecorderRef.current = mr;
          mr.ondataavailable = (e) => {
            if (e.data.size > 0) chunksRef.current.push(e.data);
          };
          mr.onstop = async () => {
            const start = recordingStartRef.current;
            recordingStartRef.current = null;
            const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
            chunksRef.current = [];
            if (!start) return;
            if (Date.now() - start < minRecordMs) return;
            if (blob.size < 1024) return;
            await sendTurn(blob);
          };
          mr.start();
          setIsListening(true);
        }

        if (isRecordingNow) {
          if (rms < stopThreshold) {
            if (!silenceSinceRef.current) silenceSinceRef.current = now;
            if (now - silenceSinceRef.current > silenceMsToStop) {
              silenceSinceRef.current = null;
              recorder?.stop();
              setIsListening(false);
            }
          } else {
            silenceSinceRef.current = null;
          }
        }
      }

      rafIdRef.current = requestAnimationFrame(tick);
    };

    rafIdRef.current = requestAnimationFrame(tick);
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
      setCallStatus('in_call');
    } catch (error) {
      console.error(error);
      alert('Could not start the call. Check microphone permissions and backend status.');
      setCallStatus('idle');
      callActiveRef.current = false;
      stopLoop();
    }
  };

  const endCall = async () => {
    if (!sessionIdRef.current) {
      setCallStatus('idle');
      callActiveRef.current = false;
      stopLoop();
      return;
    }
    setCallStatus('ending');
    callActiveRef.current = false;
    stopLoop();
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
