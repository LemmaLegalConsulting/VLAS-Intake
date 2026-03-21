# Typescript Client for Server Testing

This typescript client enables manual testing of the local websocket server without the need to make actual phone calls.

## Setup

1. Run the root websocket server. See the main [README](../../README).

2. Navigate to the `client/typescript` directory:

```bash
cd client/typescript
```

3. Install dependencies:

```bash
npm install
```

4. Run the client app:

```
npm run dev
```

5. Visit http://localhost:5173 in your browser.

The client connects directly to the local `/ws` endpoint and passes the
configured caller phone number as websocket query metadata.
