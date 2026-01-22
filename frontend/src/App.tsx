import { useState, useRef } from 'react';
import axios from 'axios';
import { Mic, MicOff, Loader2 } from 'lucide-react';
import './App.css';

interface Message {
  role: 'user' | 'agent';
  text: string;
}

// Get API URL from env or default to localhost
const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function App() {
  const [isRecording, setIsRecording] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      mediaRecorder.onstop = async () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
        
        // Check if audio is too short (approx < 1KB)
        if (audioBlob.size < 1024) {
          console.warn("Audio recording too short, skipping upload.");
          // Optional: Alert user, but console warning is less intrusive if it was an accidental click
          return;
        }

        await sendAudioToBackend(audioBlob);
      };

      mediaRecorder.start();
      setIsRecording(true);
    } catch (error) {
      console.error("Error accessing microphone:", error);
      alert("Could not access microphone. Please ensure you have granted permission.");
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
      // Stop all tracks to release microphone
      mediaRecorderRef.current.stream.getTracks().forEach(track => track.stop());
    }
  };

  const sendAudioToBackend = async (audioBlob: Blob) => {
    setIsLoading(true);
    const formData = new FormData();
    formData.append('audio', audioBlob, 'recording.webm');
    // Using a static customer ID for this POC
    formData.append('customer_id', 'user_123');

    try {
      const response = await axios.post(`${API_URL}/chat`, formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });

      const { user_transcript, agent_response, audio_base64 } = response.data;

      // Add messages to chat
      setMessages(prev => [
        ...prev, 
        { role: 'user', text: user_transcript },
        { role: 'agent', text: agent_response }
      ]);

      // Play audio response if available
      if (audio_base64) {
        playAudioResponse(audio_base64);
      }

    } catch (error) {
      console.error("Error sending audio:", error);
      if (axios.isAxiosError(error) && error.response) {
         alert(`Error: ${error.response.data.detail || 'Server Error'}`);
      } else {
         alert("Error communicating with backend. Is it running?");
      }
    } finally {
      setIsLoading(false);
    }
  };

  const playAudioResponse = (base64Audio: string) => {
    const audio = new Audio(`data:audio/mp3;base64,${base64Audio}`);
    audio.play().catch(e => console.error("Error playing audio:", e));
  };

  return (
    <div className="app-container">
      <header className="header">
        <h1>Bank ABC Voice Agent</h1>
        <p>Press the microphone to start speaking</p>
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
          className={`mic-button ${isRecording ? 'recording' : ''}`}
          onMouseDown={startRecording}
          onMouseUp={stopRecording}
          onTouchStart={startRecording}
          onTouchEnd={stopRecording}
          disabled={isLoading}
        >
          {isRecording ? <MicOff size={32} /> : <Mic size={32} />}
        </button>
        <p className="instruction">
          {isRecording ? 'Release to Send' : 'Hold to Speak'}
        </p>
      </div>
    </div>
  );
}

export default App;
