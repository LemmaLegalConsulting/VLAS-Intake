/**
 * Copyright (c) 2024–2025, Daily
 *
 * SPDX-License-Identifier: BSD 2-Clause License
 */
var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
import { PipecatClient, RTVIEvent, } from '@pipecat-ai/client-js';
import { WebSocketTransport, TwilioSerializer, } from '@pipecat-ai/websocket-transport';
class WebsocketClientApp {
    constructor() {
        this.rtviClient = null;
        this.connectBtn = null;
        this.disconnectBtn = null;
        this.statusSpan = null;
        this.debugLog = null;
        this.botAudio = document.createElement('audio');
        this.botAudio.autoplay = true;
        document.body.appendChild(this.botAudio);
        this.setupDOMElements();
        this.setupEventListeners();
    }
    /**
     * Set up references to DOM elements and create necessary media elements
     */
    setupDOMElements() {
        this.connectBtn = document.getElementById('connect-btn');
        this.disconnectBtn = document.getElementById('disconnect-btn');
        this.statusSpan = document.getElementById('connection-status');
        this.debugLog = document.getElementById('debug-log');
    }
    /**
     * Set up event listeners for connect/disconnect buttons
     */
    setupEventListeners() {
        var _a, _b;
        (_a = this.connectBtn) === null || _a === void 0 ? void 0 : _a.addEventListener('click', () => this.connect());
        (_b = this.disconnectBtn) === null || _b === void 0 ? void 0 : _b.addEventListener('click', () => this.disconnect());
    }
    /**
     * Add a timestamped message to the debug log
     */
    log(message) {
        if (!this.debugLog)
            return;
        const entry = document.createElement('div');
        entry.textContent = `${new Date().toISOString()} - ${message}`;
        if (message.startsWith('User: ')) {
            entry.style.color = '#2196F3';
        }
        else if (message.startsWith('Bot: ')) {
            entry.style.color = '#4CAF50';
        }
        this.debugLog.appendChild(entry);
        this.debugLog.scrollTop = this.debugLog.scrollHeight;
        console.log(message);
    }
    /**
     * Update the connection status display
     */
    updateStatus(status) {
        if (this.statusSpan) {
            this.statusSpan.textContent = status;
        }
        this.log(`Status: ${status}`);
    }
    emulateTwilioMessages() {
        return __awaiter(this, void 0, void 0, function* () {
            var _a;
            const connectedMessage = {
                event: 'connected',
                protocol: 'Call',
                version: '1.0.0',
            };
            const websocketTransport = (_a = this.rtviClient) === null || _a === void 0 ? void 0 : _a.transport;
            void (websocketTransport === null || websocketTransport === void 0 ? void 0 : websocketTransport.sendRawMessage(connectedMessage));
            const secretKey = import.meta.env.VITE_WEBSOCKET_SECURITY_TOKEN || '';
            const phoneNumber = import.meta.env.VITE_CALLER_PHONE_NUMBER || '';
            let authCode = '';
            if (secretKey) {
                authCode = yield this.generateAuthToken(WebsocketClientApp.CALL_SID, secretKey);
            }
            else {
                console.warn("VITE_WEBSOCKET_SECURITY_TOKEN not set");
            }
            const startMessage = {
                event: 'start',
                start: {
                    streamSid: WebsocketClientApp.STREAM_SID,
                    callSid: WebsocketClientApp.CALL_SID,
                    customParameters: {
                        websocket_auth_code: authCode,
                        caller_phone_number: phoneNumber
                    }
                },
            };
            void (websocketTransport === null || websocketTransport === void 0 ? void 0 : websocketTransport.sendRawMessage(startMessage));
        });
    }
    generateAuthToken(callId, secretKeyHex) {
        return __awaiter(this, void 0, void 0, function* () {
            const encoder = new TextEncoder();
            const keyBytes = new Uint8Array(secretKeyHex.match(/.{1,2}/g).map((byte) => parseInt(byte, 16)));
            const key = yield crypto.subtle.importKey("raw", keyBytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
            const signature = yield crypto.subtle.sign("HMAC", key, encoder.encode(callId));
            const base64 = btoa(String.fromCharCode(...new Uint8Array(signature)));
            return base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
        });
    }
    /**
     * Check for available media tracks and set them up if present
     * This is called when the bot is ready or when the transport state changes to ready
     */
    setupMediaTracks() {
        var _a;
        if (!this.rtviClient)
            return;
        const tracks = this.rtviClient.tracks();
        if ((_a = tracks.bot) === null || _a === void 0 ? void 0 : _a.audio) {
            this.setupAudioTrack(tracks.bot.audio);
        }
    }
    /**
     * Set up listeners for track events (start/stop)
     * This handles new tracks being added during the session
     */
    setupTrackListeners() {
        if (!this.rtviClient)
            return;
        // Listen for new tracks starting
        this.rtviClient.on(RTVIEvent.TrackStarted, (track, participant) => {
            // Only handle non-local (bot) tracks
            if (!(participant === null || participant === void 0 ? void 0 : participant.local) && track.kind === 'audio') {
                this.setupAudioTrack(track);
            }
        });
        // Listen for tracks stopping
        this.rtviClient.on(RTVIEvent.TrackStopped, (track, participant) => {
            this.log(`Track stopped: ${track.kind} from ${(participant === null || participant === void 0 ? void 0 : participant.name) || 'unknown'}`);
        });
    }
    /**
     * Set up an audio track for playback
     * Handles both initial setup and track updates
     */
    setupAudioTrack(track) {
        this.log('Setting up audio track');
        if (this.botAudio.srcObject &&
            'getAudioTracks' in this.botAudio.srcObject) {
            const oldTrack = this.botAudio.srcObject.getAudioTracks()[0];
            if ((oldTrack === null || oldTrack === void 0 ? void 0 : oldTrack.id) === track.id)
                return;
        }
        this.botAudio.srcObject = new MediaStream([track]);
    }
    /**
     * Initialize and connect to the bot
     * This sets up the RTVI client, initializes devices, and establishes the connection
     */
    connect() {
        return __awaiter(this, void 0, void 0, function* () {
            try {
                const startTime = Date.now();
                const ws_opts = {
                    serializer: new TwilioSerializer(),
                    recorderSampleRate: 8000,
                    playerSampleRate: 8000,
                    wsUrl: import.meta.env.VITE_BACKEND_WS_URL || 'ws://localhost:8765/ws',
                };
                const pcConfig = {
                    transport: new WebSocketTransport(ws_opts),
                    enableMic: true,
                    enableCam: false,
                    callbacks: {
                        onConnected: () => {
                            this.emulateTwilioMessages();
                            this.updateStatus('Connected');
                            if (this.connectBtn)
                                this.connectBtn.disabled = true;
                            if (this.disconnectBtn)
                                this.disconnectBtn.disabled = false;
                        },
                        onDisconnected: () => {
                            this.updateStatus('Disconnected');
                            if (this.connectBtn)
                                this.connectBtn.disabled = false;
                            if (this.disconnectBtn)
                                this.disconnectBtn.disabled = true;
                            this.log('Client disconnected');
                        },
                        onBotReady: (data) => {
                            this.log(`Bot ready: ${JSON.stringify(data)}`);
                            this.setupMediaTracks();
                        },
                        onUserTranscript: (data) => {
                            if (data.final) {
                                this.log(`User: ${data.text}`);
                            }
                        },
                        onBotTranscript: (data) => this.log(`Bot: ${data.text}`),
                        onMessageError: (error) => console.error('Message error:', error),
                        onError: (error) => console.error('Error:', error),
                    },
                };
                this.rtviClient = new PipecatClient(pcConfig);
                this.setupTrackListeners();
                this.log('Initializing devices...');
                yield this.rtviClient.initDevices();
                this.log('Connecting to bot...');
                yield this.rtviClient.connect();
                const timeTaken = Date.now() - startTime;
                this.log(`Connection complete, timeTaken: ${timeTaken}`);
            }
            catch (error) {
                this.log(`Error connecting: ${error.message}`);
                this.updateStatus('Error');
                // Clean up if there's an error
                if (this.rtviClient) {
                    try {
                        yield this.rtviClient.disconnect();
                    }
                    catch (disconnectError) {
                        this.log(`Error during disconnect: ${disconnectError}`);
                    }
                }
            }
        });
    }
    /**
     * Disconnect from the bot and clean up media resources
     */
    disconnect() {
        return __awaiter(this, void 0, void 0, function* () {
            if (this.rtviClient) {
                try {
                    yield this.rtviClient.disconnect();
                    this.rtviClient = null;
                    if (this.botAudio.srcObject &&
                        'getAudioTracks' in this.botAudio.srcObject) {
                        this.botAudio.srcObject
                            .getAudioTracks()
                            .forEach((track) => track.stop());
                        this.botAudio.srcObject = null;
                    }
                }
                catch (error) {
                    this.log(`Error disconnecting: ${error.message}`);
                }
            }
        });
    }
}
WebsocketClientApp.UUID = crypto.randomUUID();
WebsocketClientApp.STREAM_SID = WebsocketClientApp.UUID;
WebsocketClientApp.CALL_SID = WebsocketClientApp.UUID;
window.addEventListener('DOMContentLoaded', () => {
    window.WebsocketClientApp = WebsocketClientApp;
    new WebsocketClientApp();
});
