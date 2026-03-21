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
import { WebSocketTransport, } from '@pipecat-ai/websocket-transport';
class WebsocketClientApp {
    constructor() {
        this.rtviClient = null;
        this.connectBtn = null;
        this.disconnectBtn = null;
        this.refreshDevicesBtn = null;
        this.micSelect = null;
        this.speakerSelect = null;
        this.statusSpan = null;
        this.debugLog = null;
        this.currentBotTrack = null;
        this.currentMicTrack = null;
        this.selectedMicId = 'default';
        this.selectedSpeakerId = 'default';
        this.micRecoveryTimeoutId = null;
        this.keepAliveIntervalId = null;
        this.keepAliveAudioContext = null;
        this.keepAliveOscillator = null;
        this.keepAliveGainNode = null;
        this.isDisconnecting = false;
        this.audioPlaybackAttemptId = 0;
        const existingAudio = document.getElementById('bot-audio');
        this.botAudio =
            existingAudio ||
                document.createElement('audio');
        this.configureAudioElement();
        if (!existingAudio) {
            document.body.appendChild(this.botAudio);
        }
        this.setupDOMElements();
        this.setupEventListeners();
        void this.refreshDeviceOptions();
    }
    configureAudioElement() {
        this.botAudio.autoplay = true;
        this.botAudio.controls = false;
        this.botAudio.setAttribute('playsinline', 'true');
    }
    /**
     * Set up references to DOM elements and create necessary media elements
     */
    setupDOMElements() {
        this.connectBtn = document.getElementById('connect-btn');
        this.disconnectBtn = document.getElementById('disconnect-btn');
        this.refreshDevicesBtn = document.getElementById('refresh-devices-btn');
        this.micSelect = document.getElementById('mic-select');
        this.speakerSelect = document.getElementById('speaker-select');
        this.statusSpan = document.getElementById('connection-status');
        this.debugLog = document.getElementById('debug-log');
    }
    /**
     * Set up event listeners for connect/disconnect buttons
     */
    setupEventListeners() {
        var _a, _b, _c, _d, _e, _f, _g;
        (_a = this.connectBtn) === null || _a === void 0 ? void 0 : _a.addEventListener('click', () => this.connect());
        (_b = this.disconnectBtn) === null || _b === void 0 ? void 0 : _b.addEventListener('click', () => this.disconnect());
        (_c = this.refreshDevicesBtn) === null || _c === void 0 ? void 0 : _c.addEventListener('click', () => {
            void this.refreshDeviceOptions();
        });
        (_d = this.micSelect) === null || _d === void 0 ? void 0 : _d.addEventListener('change', (event) => {
            const target = event.target;
            void this.updateMic(target.value);
        });
        (_e = this.speakerSelect) === null || _e === void 0 ? void 0 : _e.addEventListener('change', (event) => {
            const target = event.target;
            void this.updateSpeakerOutput(target.value);
        });
        for (const eventName of ['stalled', 'emptied', 'error']) {
            this.botAudio.addEventListener(eventName, () => {
                this.log(`Audio element event: ${eventName}`);
                void this.recoverAudioPlayback(`audio-${eventName}`);
            });
        }
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) {
                void this.recoverAudioPlayback('document-visible');
                void this.resumeKeepAliveAudioContext('document-visible');
            }
        });
        window.addEventListener('pointerdown', () => {
            var _a;
            if ((_a = this.rtviClient) === null || _a === void 0 ? void 0 : _a.connected) {
                void this.ensureAudioPlayback('user-gesture');
                void this.resumeKeepAliveAudioContext('user-gesture');
            }
        });
        (_g = (_f = navigator.mediaDevices) === null || _f === void 0 ? void 0 : _f.addEventListener) === null || _g === void 0 ? void 0 : _g.call(_f, 'devicechange', () => {
            this.log('Media devices changed');
            void this.handleDeviceChange();
        });
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
    getErrorMessage(error) {
        if (error instanceof Error) {
            return error.message;
        }
        return String(error);
    }
    isInterruptedPlaybackError(error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
            return true;
        }
        const message = this.getErrorMessage(error).toLowerCase();
        return (message.includes('interrupted by a call to pause') ||
            message.includes('interrupted by a new load request'));
    }
    getAttachedBotTrack() {
        if (!this.botAudio.srcObject || !('getAudioTracks' in this.botAudio.srcObject)) {
            return null;
        }
        return this.botAudio.srcObject.getAudioTracks()[0] || null;
    }
    syncBotAudioTrack(forceReplace = false) {
        if (!this.currentBotTrack) {
            return false;
        }
        const attachedTrack = this.getAttachedBotTrack();
        if (!forceReplace && (attachedTrack === null || attachedTrack === void 0 ? void 0 : attachedTrack.id) === this.currentBotTrack.id) {
            return true;
        }
        this.botAudio.srcObject = new MediaStream([this.currentBotTrack]);
        return true;
    }
    buildWebsocketUrl() {
        const configuredUrl = import.meta.env.VITE_BACKEND_WS_URL || 'ws://localhost:8765/ws';
        const url = new URL(configuredUrl, window.location.href);
        const callId = this.buildCallId();
        const phoneNumber = import.meta.env.VITE_CALLER_PHONE_NUMBER || '8665345243';
        url.searchParams.set('call_id', callId);
        url.searchParams.set('caller_phone_number', phoneNumber);
        return url.toString();
    }
    buildCallId() {
        const now = new Date();
        const zeroPad = (value, width) => {
            const text = String(value);
            return text.length >= width
                ? text
                : `${'0'.repeat(width - text.length)}${text}`;
        };
        const year = now.getUTCFullYear();
        const month = zeroPad(now.getUTCMonth() + 1, 2);
        const day = zeroPad(now.getUTCDate(), 2);
        const hours = zeroPad(now.getUTCHours(), 2);
        const minutes = zeroPad(now.getUTCMinutes(), 2);
        const seconds = zeroPad(now.getUTCSeconds(), 2);
        const milliseconds = zeroPad(now.getUTCMilliseconds(), 3);
        return `${year}${month}${day}T${hours}${minutes}${seconds}${milliseconds}Z`;
    }
    /**
     * Check for available media tracks and set them up if present
     * This is called when the bot is ready or when the transport state changes to ready
     */
    setupMediaTracks() {
        var _a, _b;
        if (!this.rtviClient)
            return;
        const tracks = this.rtviClient.tracks();
        if ((_a = tracks.bot) === null || _a === void 0 ? void 0 : _a.audio) {
            this.setupAudioTrack(tracks.bot.audio);
        }
        if ((_b = tracks.local) === null || _b === void 0 ? void 0 : _b.audio) {
            this.setupMicTrack(tracks.local.audio);
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
            if (track.kind !== 'audio') {
                return;
            }
            if (participant === null || participant === void 0 ? void 0 : participant.local) {
                this.setupMicTrack(track);
                return;
            }
            // Only handle non-local (bot) tracks
            if (!(participant === null || participant === void 0 ? void 0 : participant.local)) {
                this.setupAudioTrack(track);
            }
        });
        // Listen for tracks stopping
        this.rtviClient.on(RTVIEvent.TrackStopped, (track, participant) => {
            var _a, _b;
            this.log(`Track stopped: ${track.kind} from ${(participant === null || participant === void 0 ? void 0 : participant.name) || 'unknown'}`);
            if ((participant === null || participant === void 0 ? void 0 : participant.local) && ((_a = this.currentMicTrack) === null || _a === void 0 ? void 0 : _a.id) === track.id) {
                void this.scheduleMicRecovery('local-track-stopped');
            }
            if (((_b = this.currentBotTrack) === null || _b === void 0 ? void 0 : _b.id) === track.id) {
                void this.recoverAudioPlayback('track-stopped');
            }
        });
        this.rtviClient.on(RTVIEvent.BotStartedSpeaking, () => {
            void this.ensureAudioPlayback('bot-started-speaking');
        });
        this.rtviClient.on(RTVIEvent.MicUpdated, (mic) => {
            this.selectedMicId = mic.deviceId || 'default';
            void this.refreshDeviceOptions();
            this.log(`Mic updated: ${mic.label || this.selectedMicId}`);
        });
        this.rtviClient.on(RTVIEvent.DeviceError, (error) => {
            this.log(`Device error: ${this.getErrorMessage(error)}`);
            void this.scheduleMicRecovery('device-error');
        });
    }
    setupMicTrack(track) {
        var _a;
        if (((_a = this.currentMicTrack) === null || _a === void 0 ? void 0 : _a.id) === track.id) {
            return;
        }
        this.currentMicTrack = track;
        this.log(`Tracking microphone: ${track.label || track.id}`);
        track.addEventListener('ended', () => {
            var _a;
            if (((_a = this.currentMicTrack) === null || _a === void 0 ? void 0 : _a.id) === track.id) {
                this.log(`Microphone track ended: ${track.label || track.id}`);
                void this.scheduleMicRecovery('local-track-ended');
            }
        });
    }
    /**
     * Set up an audio track for playback
     * Handles both initial setup and track updates
     */
    setupAudioTrack(track) {
        var _a;
        this.log('Setting up audio track');
        this.currentBotTrack = track;
        if (((_a = this.getAttachedBotTrack()) === null || _a === void 0 ? void 0 : _a.id) === track.id)
            return;
        this.syncBotAudioTrack(true);
        track.addEventListener('ended', () => {
            var _a;
            if (((_a = this.currentBotTrack) === null || _a === void 0 ? void 0 : _a.id) === track.id) {
                this.log(`Bot audio track ended: ${track.id}`);
                void this.recoverAudioPlayback('track-ended');
            }
        });
        void this.applySelectedSpeaker();
        void this.ensureAudioPlayback('track-attached');
    }
    ensureAudioPlayback(reason) {
        return __awaiter(this, void 0, void 0, function* () {
            var _a, _b;
            if (this.isDisconnecting || !((_a = this.rtviClient) === null || _a === void 0 ? void 0 : _a.connected)) {
                return;
            }
            if (!this.syncBotAudioTrack()) {
                return;
            }
            yield this.resumeKeepAliveAudioContext(reason);
            if (!this.botAudio.paused) {
                return;
            }
            const playbackAttemptId = ++this.audioPlaybackAttemptId;
            try {
                yield this.botAudio.play();
            }
            catch (error) {
                if (this.isDisconnecting ||
                    !((_b = this.rtviClient) === null || _b === void 0 ? void 0 : _b.connected) ||
                    playbackAttemptId !== this.audioPlaybackAttemptId ||
                    this.isInterruptedPlaybackError(error)) {
                    this.log(`Bot audio playback retry interrupted (${reason})`);
                    return;
                }
                this.log(`Unable to resume bot audio (${reason}): ${this.getErrorMessage(error)}`);
            }
        });
    }
    recoverAudioPlayback(reason) {
        return __awaiter(this, void 0, void 0, function* () {
            var _a;
            if (this.isDisconnecting || !((_a = this.rtviClient) === null || _a === void 0 ? void 0 : _a.connected) || !this.currentBotTrack) {
                return;
            }
            const attachedTrack = this.getAttachedBotTrack();
            const shouldReplaceTrack = !attachedTrack ||
                attachedTrack.readyState === 'ended' ||
                attachedTrack.id !== this.currentBotTrack.id;
            this.syncBotAudioTrack(shouldReplaceTrack);
            yield this.applySelectedSpeaker();
            yield this.ensureAudioPlayback(reason);
        });
    }
    startAudioKeepAlive() {
        return __awaiter(this, void 0, void 0, function* () {
            if (this.keepAliveAudioContext) {
                yield this.resumeKeepAliveAudioContext('start-keepalive');
                return;
            }
            try {
                const audioContext = new AudioContext();
                const oscillator = audioContext.createOscillator();
                const gainNode = audioContext.createGain();
                oscillator.type = 'sine';
                oscillator.frequency.value = 20;
                gainNode.gain.value = 0.00001;
                oscillator.connect(gainNode);
                gainNode.connect(audioContext.destination);
                oscillator.start();
                this.keepAliveAudioContext = audioContext;
                this.keepAliveOscillator = oscillator;
                this.keepAliveGainNode = gainNode;
                yield this.resumeKeepAliveAudioContext('start-keepalive');
                this.log('Audio keepalive started');
            }
            catch (error) {
                this.log(`Unable to start audio keepalive: ${this.getErrorMessage(error)}`);
            }
        });
    }
    resumeKeepAliveAudioContext(reason) {
        return __awaiter(this, void 0, void 0, function* () {
            if (!this.keepAliveAudioContext) {
                return;
            }
            if (this.keepAliveAudioContext.state === 'suspended') {
                try {
                    yield this.keepAliveAudioContext.resume();
                }
                catch (error) {
                    this.log(`Unable to resume keepalive audio (${reason}): ${this.getErrorMessage(error)}`);
                }
            }
        });
    }
    stopAudioKeepAlive() {
        return __awaiter(this, void 0, void 0, function* () {
            if (this.keepAliveOscillator) {
                try {
                    this.keepAliveOscillator.stop();
                }
                catch (_a) {
                    // Ignore double-stop during cleanup.
                }
            }
            this.keepAliveOscillator = null;
            this.keepAliveGainNode = null;
            if (this.keepAliveAudioContext) {
                try {
                    yield this.keepAliveAudioContext.close();
                }
                catch (_b) {
                    // Ignore close failures during cleanup.
                }
            }
            this.keepAliveAudioContext = null;
        });
    }
    startConnectionHealthMonitor() {
        if (this.keepAliveIntervalId !== null) {
            window.clearInterval(this.keepAliveIntervalId);
        }
        this.keepAliveIntervalId = window.setInterval(() => {
            void this.runConnectionHealthCheck();
        }, 2000);
    }
    stopConnectionHealthMonitor() {
        if (this.keepAliveIntervalId !== null) {
            window.clearInterval(this.keepAliveIntervalId);
            this.keepAliveIntervalId = null;
        }
    }
    runConnectionHealthCheck() {
        return __awaiter(this, void 0, void 0, function* () {
            var _a, _b;
            if (!((_a = this.rtviClient) === null || _a === void 0 ? void 0 : _a.connected)) {
                return;
            }
            yield this.resumeKeepAliveAudioContext('health-check');
            if (this.currentBotTrack && this.botAudio.paused) {
                yield this.ensureAudioPlayback('health-check');
            }
            if (!this.currentMicTrack ||
                this.currentMicTrack.readyState === 'ended' ||
                !this.currentMicTrack.enabled ||
                this.currentMicTrack.muted) {
                yield this.recoverMicrophone('health-check');
                return;
            }
            const liveTrack = (_b = this.rtviClient.tracks().local) === null || _b === void 0 ? void 0 : _b.audio;
            if (!liveTrack || liveTrack.readyState === 'ended') {
                yield this.recoverMicrophone('health-check-track-scan');
            }
        });
    }
    applySelectedSpeaker() {
        return __awaiter(this, void 0, void 0, function* () {
            if (!this.botAudio.setSinkId) {
                return;
            }
            const sinkId = this.selectedSpeakerId === 'default' ? '' : this.selectedSpeakerId;
            try {
                yield this.botAudio.setSinkId(sinkId);
            }
            catch (error) {
                this.log(`Unable to route audio to ${this.selectedSpeakerId}: ${this.getErrorMessage(error)}`);
            }
        });
    }
    handleDeviceChange() {
        return __awaiter(this, void 0, void 0, function* () {
            var _a;
            yield this.refreshDeviceOptions();
            const availableMics = yield this.getAvailableMicrophones();
            const selectedMicStillExists = this.selectedMicId === 'default' ||
                availableMics.some((device) => device.deviceId === this.selectedMicId);
            if (!selectedMicStillExists || ((_a = this.currentMicTrack) === null || _a === void 0 ? void 0 : _a.readyState) === 'ended') {
                this.log('Microphone route changed, attempting mic recovery');
                yield this.recoverMicrophone('device-change');
            }
            if (this.selectedSpeakerId !== 'default') {
                const availableSpeakers = yield this.getAvailableSpeakers();
                const selectedSpeakerStillExists = availableSpeakers.some((device) => device.deviceId === this.selectedSpeakerId);
                if (!selectedSpeakerStillExists) {
                    this.log(`Selected speaker ${this.selectedSpeakerId} is no longer available, falling back to system default`);
                    yield this.updateSpeakerOutput('default');
                }
            }
            yield this.recoverAudioPlayback('device-change');
        });
    }
    getAvailableMicrophones() {
        return __awaiter(this, void 0, void 0, function* () {
            const devices = yield navigator.mediaDevices.enumerateDevices();
            return devices.filter((device) => device.kind === 'audioinput');
        });
    }
    getAvailableSpeakers() {
        return __awaiter(this, void 0, void 0, function* () {
            const devices = yield navigator.mediaDevices.enumerateDevices();
            return devices.filter((device) => device.kind === 'audiooutput');
        });
    }
    refreshDeviceOptions() {
        return __awaiter(this, void 0, void 0, function* () {
            var _a, _b;
            if (!((_a = navigator.mediaDevices) === null || _a === void 0 ? void 0 : _a.enumerateDevices)) {
                this.log('Media device enumeration is not available in this browser');
                return;
            }
            const devices = yield navigator.mediaDevices.enumerateDevices();
            const microphones = devices.filter((device) => device.kind === 'audioinput');
            const speakers = devices.filter((device) => device.kind === 'audiooutput');
            const selectedMicId = this.getSelectedDeviceId((_b = this.rtviClient) === null || _b === void 0 ? void 0 : _b.selectedMic);
            this.selectedMicId = selectedMicId || 'default';
            this.populateDeviceSelect(this.micSelect, microphones, this.selectedMicId, 'System default');
            this.populateDeviceSelect(this.speakerSelect, speakers, this.selectedSpeakerId, 'System default');
        });
    }
    getSelectedDeviceId(device) {
        if (!device || !('deviceId' in device)) {
            return 'default';
        }
        return device.deviceId || 'default';
    }
    populateDeviceSelect(select, devices, selectedId, defaultLabel) {
        if (!select) {
            return;
        }
        select.innerHTML = '';
        const entries = new Map();
        entries.set('default', defaultLabel);
        for (const device of devices) {
            const label = device.label || `${device.kind} ${entries.size}`;
            entries.set(device.deviceId, label);
        }
        for (const [deviceId, label] of entries.entries()) {
            const option = document.createElement('option');
            option.value = deviceId;
            option.textContent = label;
            option.selected = deviceId === selectedId;
            select.appendChild(option);
        }
        if (![...entries.keys()].includes(selectedId)) {
            select.value = 'default';
        }
        select.disabled = entries.size <= 1;
    }
    updateMic(deviceId) {
        return __awaiter(this, void 0, void 0, function* () {
            if (!this.rtviClient) {
                return;
            }
            try {
                const normalizedDeviceId = deviceId === 'default' ? '' : deviceId;
                this.selectedMicId = deviceId || 'default';
                this.rtviClient.updateMic(normalizedDeviceId);
                this.log(`Microphone updated to ${this.selectedMicId}`);
                yield this.refreshDeviceOptions();
            }
            catch (error) {
                this.log(`Error updating microphone: ${this.getErrorMessage(error)}`);
            }
        });
    }
    scheduleMicRecovery(reason) {
        return __awaiter(this, void 0, void 0, function* () {
            if (this.micRecoveryTimeoutId !== null) {
                window.clearTimeout(this.micRecoveryTimeoutId);
            }
            this.micRecoveryTimeoutId = window.setTimeout(() => {
                this.micRecoveryTimeoutId = null;
                void this.recoverMicrophone(reason);
            }, 250);
        });
    }
    recoverMicrophone(reason) {
        return __awaiter(this, void 0, void 0, function* () {
            if (!this.rtviClient) {
                return;
            }
            const availableMics = yield this.getAvailableMicrophones();
            const preferredMicId = this.selectedMicId !== 'default' &&
                availableMics.some((device) => device.deviceId === this.selectedMicId)
                ? this.selectedMicId
                : 'default';
            try {
                this.log(`Recovering microphone (${reason}) using ${preferredMicId}`);
                this.rtviClient.enableMic(true);
                yield this.updateMic(preferredMicId);
                this.setupMediaTracks();
            }
            catch (error) {
                this.log(`Microphone recovery failed: ${this.getErrorMessage(error)}`);
            }
        });
    }
    updateSpeakerOutput(deviceId) {
        return __awaiter(this, void 0, void 0, function* () {
            this.selectedSpeakerId = deviceId || 'default';
            if (this.speakerSelect) {
                this.speakerSelect.value = this.selectedSpeakerId;
            }
            yield this.applySelectedSpeaker();
            yield this.ensureAudioPlayback('speaker-updated');
            this.log(`Speaker output updated to ${this.selectedSpeakerId}`);
        });
    }
    /**
     * Initialize and connect to the bot
     * This sets up the RTVI client, initializes devices, and establishes the connection
     */
    connect() {
        return __awaiter(this, void 0, void 0, function* () {
            try {
                const startTime = Date.now();
                this.isDisconnecting = false;
                const ws_opts = {
                    recorderSampleRate: 8000,
                    playerSampleRate: 8000,
                    wsUrl: this.buildWebsocketUrl(),
                };
                const pcConfig = {
                    transport: new WebSocketTransport(ws_opts),
                    enableMic: true,
                    enableCam: false,
                    callbacks: {
                        onConnected: () => {
                            this.updateStatus('Connected');
                            if (this.connectBtn)
                                this.connectBtn.disabled = true;
                            if (this.disconnectBtn)
                                this.disconnectBtn.disabled = false;
                            void this.refreshDeviceOptions();
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
                        onMessageError: (error) => this.log(`Message error: ${JSON.stringify(error)}`),
                        onError: (error) => this.log(`Error: ${JSON.stringify(error)}`),
                    },
                };
                this.rtviClient = new PipecatClient(pcConfig);
                this.setupTrackListeners();
                this.log('Initializing devices...');
                yield this.rtviClient.initDevices();
                yield this.refreshDeviceOptions();
                this.setupMediaTracks();
                this.log('Connecting to bot...');
                yield this.rtviClient.connect();
                yield this.applySelectedSpeaker();
                this.setupMediaTracks();
                yield this.startAudioKeepAlive();
                this.startConnectionHealthMonitor();
                const timeTaken = Date.now() - startTime;
                this.log(`Connection complete, timeTaken: ${timeTaken}`);
            }
            catch (error) {
                this.log(`Error connecting: ${this.getErrorMessage(error)}`);
                this.updateStatus('Error');
                // Clean up if there's an error
                if (this.rtviClient) {
                    try {
                        yield this.rtviClient.disconnect();
                    }
                    catch (disconnectError) {
                        this.log(`Error during disconnect: ${this.getErrorMessage(disconnectError)}`);
                    }
                }
                this.rtviClient = null;
                this.stopConnectionHealthMonitor();
                yield this.stopAudioKeepAlive();
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
                    this.isDisconnecting = true;
                    yield this.rtviClient.disconnect();
                    this.rtviClient = null;
                    this.currentBotTrack = null;
                    this.currentMicTrack = null;
                    this.stopConnectionHealthMonitor();
                    yield this.stopAudioKeepAlive();
                    this.botAudio.pause();
                    this.botAudio.srcObject = null;
                }
                catch (error) {
                    this.log(`Error disconnecting: ${this.getErrorMessage(error)}`);
                }
                finally {
                    this.isDisconnecting = false;
                }
            }
        });
    }
}
window.addEventListener('DOMContentLoaded', () => {
    window.WebsocketClientApp = WebsocketClientApp;
    new WebsocketClientApp();
});
