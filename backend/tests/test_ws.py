import asyncio
import websockets
import json
import base64
import sys

# Mock Audio Data (Silence)
MOCK_AUDIO = b'\x00' * 16000  # 1 second of silence (fake)

async def test_websocket_client():
    uri = "ws://localhost:8000/ws/chat/test_session_123"
    print(f"Connecting to {uri}...")
    
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected!")
            
            # 1. Send Config (Optional test)
            print("Sending config...")
            await websocket.send(json.dumps({"type": "config", "env": "dev"}))
            
            # 2. Send Audio (Fake Turn)
            print("Sending audio bytes...")
            await websocket.send(MOCK_AUDIO)
            
            # 3. Receive Loop
            while True:
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                    
                    if isinstance(message, str):
                        data = json.loads(message)
                        print(f"Received JSON: {data.get('type')} -> {data.get('text', '')[:50]}...")
                        
                        if data.get("type") == "response":
                            print("Turn complete (Text received). Waiting for audio...")
                            
                    elif isinstance(message, bytes):
                        print(f"Received Audio Bytes: {len(message)} bytes")
                        print("Test Successful: Full cycle completed.")
                        break
                        
                except asyncio.TimeoutError:
                    print("Timeout waiting for response.")
                    break
                    
    except Exception as e:
        print(f"Connection failed: {e}")
        print("Ensure the backend server is running on localhost:8000")

if __name__ == "__main__":
    # Check if websockets is installed
    try:
        import websockets
        asyncio.run(test_websocket_client())
    except ImportError:
        print("Please install websockets: pip install websockets")
