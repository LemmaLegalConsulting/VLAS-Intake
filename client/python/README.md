# Python Client for LLM-Powered Automated Server Testing

This Python client enables LLM-powered automated testing of the server via WebSocket without the need to make actual phone calls.

## Setup Instructions

### 1. **Start the Server:**

Follow the instructions in the main README to start the server.

### 2. **Run the Client:**

```sh
python client.py
```

- `-u`: Server's websocket URL (default is `ws://localhost:8765/ws`)
- `-c`: Number of concurrent client connections (default is `1`)
- `-s`: The script from `scripts.yml` that you want to use
