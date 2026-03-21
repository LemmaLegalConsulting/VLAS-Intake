/**
 * Copyright (c) 2024–2025, Daily
 *
 * SPDX-License-Identifier: BSD 2-Clause License
 */

import {
    BotLLMTextData,
    Participant,
    PipecatClient,
    PipecatClientOptions,
    RTVIEvent,
    RTVIMessage,
    TranscriptData,
} from '@pipecat-ai/client-js';
import {
    WebSocketTransport,
} from '@pipecat-ai/websocket-transport';

type SinkableAudioElement = HTMLAudioElement & {
    setSinkId?: (sinkId: string) => Promise<void>;
};

class WebsocketClientApp {
    private rtviClient: PipecatClient | null = null;
    private connectBtn: HTMLButtonElement | null = null;
    private disconnectBtn: HTMLButtonElement | null = null;
    private refreshDevicesBtn: HTMLButtonElement | null = null;
    private micSelect: HTMLSelectElement | null = null;
    private speakerSelect: HTMLSelectElement | null = null;
    private statusSpan: HTMLElement | null = null;
    private debugLog: HTMLElement | null = null;
    private botAudio: SinkableAudioElement;
    private currentBotTrack: MediaStreamTrack | null = null;
    private currentMicTrack: MediaStreamTrack | null = null;
    private selectedMicId = 'default';
    private selectedSpeakerId = 'default';
    private micRecoveryTimeoutId: number | null = null;
    private keepAliveIntervalId: number | null = null;
    private keepAliveAudioContext: AudioContext | null = null;
    private keepAliveOscillator: OscillatorNode | null = null;
    private keepAliveGainNode: GainNode | null = null;

    constructor() {
        const existingAudio = document.getElementById('bot-audio');
        this.botAudio =
            (existingAudio as SinkableAudioElement | null) ||
            (document.createElement('audio') as SinkableAudioElement);
        this.configureAudioElement();
        if (!existingAudio) {
            document.body.appendChild(this.botAudio);
        }
        this.setupDOMElements();
        this.setupEventListeners();
        void this.refreshDeviceOptions();
    }

    private configureAudioElement(): void {
        this.botAudio.autoplay = true;
        this.botAudio.controls = false;
        this.botAudio.setAttribute('playsinline', 'true');
    }

    /**
     * Set up references to DOM elements and create necessary media elements
     */
    private setupDOMElements(): void {
        this.connectBtn = document.getElementById(
            'connect-btn'
        ) as HTMLButtonElement;
        this.disconnectBtn = document.getElementById(
            'disconnect-btn'
        ) as HTMLButtonElement;
        this.refreshDevicesBtn = document.getElementById(
            'refresh-devices-btn'
        ) as HTMLButtonElement;
        this.micSelect = document.getElementById('mic-select') as HTMLSelectElement;
        this.speakerSelect = document.getElementById(
            'speaker-select'
        ) as HTMLSelectElement;
        this.statusSpan = document.getElementById('connection-status');
        this.debugLog = document.getElementById('debug-log');
    }

    /**
     * Set up event listeners for connect/disconnect buttons
     */
    private setupEventListeners(): void {
        this.connectBtn?.addEventListener('click', () => this.connect());
        this.disconnectBtn?.addEventListener('click', () => this.disconnect());
        this.refreshDevicesBtn?.addEventListener('click', () => {
            void this.refreshDeviceOptions();
        });
        this.micSelect?.addEventListener('change', (event) => {
            const target = event.target as HTMLSelectElement;
            void this.updateMic(target.value);
        });
        this.speakerSelect?.addEventListener('change', (event) => {
            const target = event.target as HTMLSelectElement;
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
            if (this.rtviClient?.connected) {
                void this.ensureAudioPlayback('user-gesture');
                void this.resumeKeepAliveAudioContext('user-gesture');
            }
        });

        navigator.mediaDevices?.addEventListener?.('devicechange', () => {
            this.log('Media devices changed');
            void this.handleDeviceChange();
        });
    }

    /**
     * Add a timestamped message to the debug log
     */
    private log(message: string): void {
        if (!this.debugLog) return;
        const entry = document.createElement('div');
        entry.textContent = `${new Date().toISOString()} - ${message}`;
        if (message.startsWith('User: ')) {
            entry.style.color = '#2196F3';
        } else if (message.startsWith('Bot: ')) {
            entry.style.color = '#4CAF50';
        }
        this.debugLog.appendChild(entry);
        this.debugLog.scrollTop = this.debugLog.scrollHeight;
        console.log(message);
    }

    /**
     * Update the connection status display
     */
    private updateStatus(status: string): void {
        if (this.statusSpan) {
            this.statusSpan.textContent = status;
        }
        this.log(`Status: ${status}`);
    }

    private getErrorMessage(error: unknown): string {
        if (error instanceof Error) {
            return error.message;
        }
        return String(error);
    }

    private buildWebsocketUrl(): string {
        const configuredUrl =
            import.meta.env.VITE_BACKEND_WS_URL || 'ws://localhost:8765/ws';
        const url = new URL(configuredUrl, window.location.href);
        const callId = this.buildCallId();
        const phoneNumber = import.meta.env.VITE_CALLER_PHONE_NUMBER || '8665345243';

        url.searchParams.set('call_id', callId);
        url.searchParams.set('caller_phone_number', phoneNumber);

        return url.toString();
    }

    private buildCallId(): string {
        const now = new Date();
        const zeroPad = (value: number, width: number): string => {
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
        if (!this.rtviClient) return;
        const tracks = this.rtviClient.tracks();
        if (tracks.bot?.audio) {
            this.setupAudioTrack(tracks.bot.audio);
        }
        if (tracks.local?.audio) {
            this.setupMicTrack(tracks.local.audio);
        }
    }

    /**
     * Set up listeners for track events (start/stop)
     * This handles new tracks being added during the session
     */
    setupTrackListeners() {
        if (!this.rtviClient) return;

        // Listen for new tracks starting
        this.rtviClient.on(
            RTVIEvent.TrackStarted,
            (track: MediaStreamTrack, participant?: Participant) => {
                if (track.kind !== 'audio') {
                    return;
                }

                if (participant?.local) {
                    this.setupMicTrack(track);
                    return;
                }

                // Only handle non-local (bot) tracks
                if (!participant?.local) {
                    this.setupAudioTrack(track);
                }
            }
        );

        // Listen for tracks stopping
        this.rtviClient.on(
            RTVIEvent.TrackStopped,
            (track: MediaStreamTrack, participant?: Participant) => {
                this.log(
                    `Track stopped: ${track.kind} from ${participant?.name || 'unknown'}`
                );
                if (participant?.local && this.currentMicTrack?.id === track.id) {
                    void this.scheduleMicRecovery('local-track-stopped');
                }
                if (this.currentBotTrack?.id === track.id) {
                    void this.recoverAudioPlayback('track-stopped');
                }
            }
        );

        this.rtviClient.on(RTVIEvent.BotStartedSpeaking, () => {
            void this.ensureAudioPlayback('bot-started-speaking');
        });

        this.rtviClient.on(RTVIEvent.MicUpdated, (mic: MediaDeviceInfo) => {
            this.selectedMicId = mic.deviceId || 'default';
            void this.refreshDeviceOptions();
            this.log(`Mic updated: ${mic.label || this.selectedMicId}`);
        });

        this.rtviClient.on(RTVIEvent.DeviceError, (error) => {
            this.log(`Device error: ${this.getErrorMessage(error)}`);
            void this.scheduleMicRecovery('device-error');
        });
    }

    private setupMicTrack(track: MediaStreamTrack): void {
        if (this.currentMicTrack?.id === track.id) {
            return;
        }

        this.currentMicTrack = track;
        this.log(`Tracking microphone: ${track.label || track.id}`);
        track.addEventListener('ended', () => {
            if (this.currentMicTrack?.id === track.id) {
                this.log(`Microphone track ended: ${track.label || track.id}`);
                void this.scheduleMicRecovery('local-track-ended');
            }
        });
    }

    /**
     * Set up an audio track for playback
     * Handles both initial setup and track updates
     */
    private setupAudioTrack(track: MediaStreamTrack): void {
        this.log('Setting up audio track');
        this.currentBotTrack = track;
        if (
            this.botAudio.srcObject &&
            'getAudioTracks' in this.botAudio.srcObject
        ) {
            const oldTrack = this.botAudio.srcObject.getAudioTracks()[0];
            if (oldTrack?.id === track.id) return;
        }
        this.botAudio.srcObject = new MediaStream([track]);
        track.addEventListener('ended', () => {
            if (this.currentBotTrack?.id === track.id) {
                this.log(`Bot audio track ended: ${track.id}`);
                void this.recoverAudioPlayback('track-ended');
            }
        });
        void this.applySelectedSpeaker();
        void this.ensureAudioPlayback('track-attached');
    }

    private async ensureAudioPlayback(reason: string): Promise<void> {
        if (this.currentBotTrack && !this.botAudio.srcObject) {
            this.botAudio.srcObject = new MediaStream([this.currentBotTrack]);
        }

        await this.resumeKeepAliveAudioContext(reason);

        try {
            await this.botAudio.play();
        } catch (error) {
            this.log(
                `Unable to resume bot audio (${reason}): ${this.getErrorMessage(error)}`
            );
        }
    }

    private async recoverAudioPlayback(reason: string): Promise<void> {
        if (!this.currentBotTrack) {
            return;
        }

        this.botAudio.srcObject = null;
        this.botAudio.srcObject = new MediaStream([this.currentBotTrack]);
        await this.applySelectedSpeaker();
        await this.ensureAudioPlayback(reason);
    }

    private async startAudioKeepAlive(): Promise<void> {
        if (this.keepAliveAudioContext) {
            await this.resumeKeepAliveAudioContext('start-keepalive');
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

            await this.resumeKeepAliveAudioContext('start-keepalive');
            this.log('Audio keepalive started');
        } catch (error) {
            this.log(`Unable to start audio keepalive: ${this.getErrorMessage(error)}`);
        }
    }

    private async resumeKeepAliveAudioContext(reason: string): Promise<void> {
        if (!this.keepAliveAudioContext) {
            return;
        }

        if (this.keepAliveAudioContext.state === 'suspended') {
            try {
                await this.keepAliveAudioContext.resume();
            } catch (error) {
                this.log(
                    `Unable to resume keepalive audio (${reason}): ${this.getErrorMessage(error)}`
                );
            }
        }
    }

    private async stopAudioKeepAlive(): Promise<void> {
        if (this.keepAliveOscillator) {
            try {
                this.keepAliveOscillator.stop();
            } catch {
                // Ignore double-stop during cleanup.
            }
        }

        this.keepAliveOscillator = null;
        this.keepAliveGainNode = null;

        if (this.keepAliveAudioContext) {
            try {
                await this.keepAliveAudioContext.close();
            } catch {
                // Ignore close failures during cleanup.
            }
        }

        this.keepAliveAudioContext = null;
    }

    private startConnectionHealthMonitor(): void {
        if (this.keepAliveIntervalId !== null) {
            window.clearInterval(this.keepAliveIntervalId);
        }

        this.keepAliveIntervalId = window.setInterval(() => {
            void this.runConnectionHealthCheck();
        }, 2000);
    }

    private stopConnectionHealthMonitor(): void {
        if (this.keepAliveIntervalId !== null) {
            window.clearInterval(this.keepAliveIntervalId);
            this.keepAliveIntervalId = null;
        }
    }

    private async runConnectionHealthCheck(): Promise<void> {
        if (!this.rtviClient?.connected) {
            return;
        }

        await this.resumeKeepAliveAudioContext('health-check');

        if (this.currentBotTrack && this.botAudio.paused) {
            await this.ensureAudioPlayback('health-check');
        }

        if (
            !this.currentMicTrack ||
            this.currentMicTrack.readyState === 'ended' ||
            !this.currentMicTrack.enabled ||
            this.currentMicTrack.muted
        ) {
            await this.recoverMicrophone('health-check');
            return;
        }

        const liveTrack = this.rtviClient.tracks().local?.audio;
        if (!liveTrack || liveTrack.readyState === 'ended') {
            await this.recoverMicrophone('health-check-track-scan');
        }
    }

    private async applySelectedSpeaker(): Promise<void> {
        if (!this.botAudio.setSinkId) {
            return;
        }

        const sinkId = this.selectedSpeakerId === 'default' ? '' : this.selectedSpeakerId;
        try {
            await this.botAudio.setSinkId(sinkId);
        } catch (error) {
            this.log(
                `Unable to route audio to ${this.selectedSpeakerId}: ${this.getErrorMessage(error)}`
            );
        }
    }

    private async handleDeviceChange(): Promise<void> {
        await this.refreshDeviceOptions();

        const availableMics = await this.getAvailableMicrophones();
        const selectedMicStillExists =
            this.selectedMicId === 'default' ||
            availableMics.some((device) => device.deviceId === this.selectedMicId);

        if (!selectedMicStillExists || this.currentMicTrack?.readyState === 'ended') {
            this.log('Microphone route changed, attempting mic recovery');
            await this.recoverMicrophone('device-change');
        }

        if (this.selectedSpeakerId !== 'default') {
            const availableSpeakers = await this.getAvailableSpeakers();
            const selectedSpeakerStillExists = availableSpeakers.some(
                (device) => device.deviceId === this.selectedSpeakerId
            );
            if (!selectedSpeakerStillExists) {
                this.log(
                    `Selected speaker ${this.selectedSpeakerId} is no longer available, falling back to system default`
                );
                await this.updateSpeakerOutput('default');
            }
        }

        await this.recoverAudioPlayback('device-change');
    }

    private async getAvailableMicrophones(): Promise<MediaDeviceInfo[]> {
        const devices = await navigator.mediaDevices.enumerateDevices();
        return devices.filter((device) => device.kind === 'audioinput');
    }

    private async getAvailableSpeakers(): Promise<MediaDeviceInfo[]> {
        const devices = await navigator.mediaDevices.enumerateDevices();
        return devices.filter((device) => device.kind === 'audiooutput');
    }

    private async refreshDeviceOptions(): Promise<void> {
        if (!navigator.mediaDevices?.enumerateDevices) {
            this.log('Media device enumeration is not available in this browser');
            return;
        }

        const devices = await navigator.mediaDevices.enumerateDevices();
        const microphones = devices.filter((device) => device.kind === 'audioinput');
        const speakers = devices.filter((device) => device.kind === 'audiooutput');
        const selectedMicId = this.getSelectedDeviceId(this.rtviClient?.selectedMic);
        this.selectedMicId = selectedMicId || 'default';

        this.populateDeviceSelect(
            this.micSelect,
            microphones,
            this.selectedMicId,
            'System default'
        );
        this.populateDeviceSelect(
            this.speakerSelect,
            speakers,
            this.selectedSpeakerId,
            'System default'
        );
    }

    private getSelectedDeviceId(device: MediaDeviceInfo | Record<string, never> | undefined): string {
        if (!device || !('deviceId' in device)) {
            return 'default';
        }

        return device.deviceId || 'default';
    }

    private populateDeviceSelect(
        select: HTMLSelectElement | null,
        devices: MediaDeviceInfo[],
        selectedId: string,
        defaultLabel: string
    ): void {
        if (!select) {
            return;
        }

        select.innerHTML = '';
        const entries = new Map<string, string>();
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

    private async updateMic(deviceId: string): Promise<void> {
        if (!this.rtviClient) {
            return;
        }

        try {
            const normalizedDeviceId = deviceId === 'default' ? '' : deviceId;
            this.selectedMicId = deviceId || 'default';
            this.rtviClient.updateMic(normalizedDeviceId);
            this.log(`Microphone updated to ${this.selectedMicId}`);
            await this.refreshDeviceOptions();
        } catch (error) {
            this.log(`Error updating microphone: ${this.getErrorMessage(error)}`);
        }
    }

    private async scheduleMicRecovery(reason: string): Promise<void> {
        if (this.micRecoveryTimeoutId !== null) {
            window.clearTimeout(this.micRecoveryTimeoutId);
        }

        this.micRecoveryTimeoutId = window.setTimeout(() => {
            this.micRecoveryTimeoutId = null;
            void this.recoverMicrophone(reason);
        }, 250);
    }

    private async recoverMicrophone(reason: string): Promise<void> {
        if (!this.rtviClient) {
            return;
        }

        const availableMics = await this.getAvailableMicrophones();
        const preferredMicId =
            this.selectedMicId !== 'default' &&
            availableMics.some((device) => device.deviceId === this.selectedMicId)
                ? this.selectedMicId
                : 'default';

        try {
            this.log(`Recovering microphone (${reason}) using ${preferredMicId}`);
            this.rtviClient.enableMic(true);
            await this.updateMic(preferredMicId);
            this.setupMediaTracks();
        } catch (error) {
            this.log(`Microphone recovery failed: ${this.getErrorMessage(error)}`);
        }
    }

    private async updateSpeakerOutput(deviceId: string): Promise<void> {
        this.selectedSpeakerId = deviceId || 'default';
        if (this.speakerSelect) {
            this.speakerSelect.value = this.selectedSpeakerId;
        }

        await this.applySelectedSpeaker();
        await this.ensureAudioPlayback('speaker-updated');
        this.log(`Speaker output updated to ${this.selectedSpeakerId}`);
    }

    /**
     * Initialize and connect to the bot
     * This sets up the RTVI client, initializes devices, and establishes the connection
     */
    public async connect(): Promise<void> {
        try {
            const startTime = Date.now();

            const ws_opts = {
                recorderSampleRate: 8000,
                playerSampleRate: 8000,
                wsUrl: this.buildWebsocketUrl(),
            };
            const pcConfig: PipecatClientOptions = {
                transport: new WebSocketTransport(ws_opts),
                enableMic: true,
                enableCam: false,
                callbacks: {
                    onConnected: () => {
                        this.updateStatus('Connected');
                        if (this.connectBtn) this.connectBtn.disabled = true;
                        if (this.disconnectBtn) this.disconnectBtn.disabled = false;
                        void this.refreshDeviceOptions();
                    },
                    onDisconnected: () => {
                        this.updateStatus('Disconnected');
                        if (this.connectBtn) this.connectBtn.disabled = false;
                        if (this.disconnectBtn) this.disconnectBtn.disabled = true;
                        this.log('Client disconnected');
                    },
                    onBotReady: (data: any) => {
                        this.log(`Bot ready: ${JSON.stringify(data)}`);
                        this.setupMediaTracks();
                    },
                    onUserTranscript: (data: TranscriptData) => {
                        if (data.final) {
                            this.log(`User: ${data.text}`);
                        }
                    },
                    onBotTranscript: (data: BotLLMTextData) =>
                        this.log(`Bot: ${data.text}`),
                    onMessageError: (error: RTVIMessage) =>
                        this.log(`Message error: ${JSON.stringify(error)}`),
                    onError: (error: RTVIMessage) =>
                        this.log(`Error: ${JSON.stringify(error)}`),
                },
            };
            this.rtviClient = new PipecatClient(pcConfig);
            this.setupTrackListeners();

            this.log('Initializing devices...');
            await this.rtviClient.initDevices();
            await this.refreshDeviceOptions();
            this.setupMediaTracks();

            this.log('Connecting to bot...');
            await this.rtviClient.connect();
            await this.applySelectedSpeaker();
            this.setupMediaTracks();
            await this.startAudioKeepAlive();
            this.startConnectionHealthMonitor();

            const timeTaken = Date.now() - startTime;
            this.log(`Connection complete, timeTaken: ${timeTaken}`);
        } catch (error) {
            this.log(`Error connecting: ${this.getErrorMessage(error)}`);
            this.updateStatus('Error');
            // Clean up if there's an error
            if (this.rtviClient) {
                try {
                    await this.rtviClient.disconnect();
                } catch (disconnectError) {
                    this.log(
                        `Error during disconnect: ${this.getErrorMessage(disconnectError)}`
                    );
                }
            }
            this.rtviClient = null;
            this.stopConnectionHealthMonitor();
            await this.stopAudioKeepAlive();
        }
    }

    /**
     * Disconnect from the bot and clean up media resources
     */
    public async disconnect(): Promise<void> {
        if (this.rtviClient) {
            try {
                await this.rtviClient.disconnect();
                this.rtviClient = null;
                this.currentBotTrack = null;
                this.currentMicTrack = null;
                this.stopConnectionHealthMonitor();
                await this.stopAudioKeepAlive();
                this.botAudio.pause();
                this.botAudio.srcObject = null;
            } catch (error) {
                this.log(`Error disconnecting: ${this.getErrorMessage(error)}`);
            }
        }
    }
}

declare global {
    interface Window {
        WebsocketClientApp: typeof WebsocketClientApp;
    }
}

window.addEventListener('DOMContentLoaded', () => {
    window.WebsocketClientApp = WebsocketClientApp;
    new WebsocketClientApp();
});
