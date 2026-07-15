// Shared WebSocket client for the live targets feed (separate server on :8766,
// reached over the WireGuard tunnel via window.location.hostname).
// Single resilient connection: exponential-backoff reconnect, heartbeat/staleness
// watchdog, reconnect on tab-visible / online, and a small outbound send queue.
// Public API: window.TargetsWS.
(function () {
    'use strict';
    const RECONNECT_DELAY = 2000, MAX_RECONNECT_DELAY = 30000;
    const HEARTBEAT_INTERVAL = 5000, MESSAGE_TIMEOUT = 10000, SEND_QUEUE_LIMIT = 64;

    // The targets server lives on a separate VM (default 10.31.0.100), reached
    // over the wg-targets WireGuard tunnel — NOT the cockpit host. Host/port are
    // injected by the server (window.__TARGETS_WS_HOST__ / __TARGETS_WS_PORT__);
    // an empty host falls back to the page host.
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const targetsHost = (window.__TARGETS_WS_HOST__ && String(window.__TARGETS_WS_HOST__).trim()) || window.location.hostname;
    const targetsPort = window.__TARGETS_WS_PORT__ || 8766;
    const wsUrl = `${wsProtocol}//${targetsHost}:${targetsPort}`;

    let socket = null, reconnectTimeout = null, heartbeatInterval = null;
    let lastMessageTime = Date.now(), isReconnecting = false;
    let currentReconnectDelay = RECONNECT_DELAY;

    const textHandlers = new Map();   // type -> Set<fn>
    const binaryHandlers = new Set();
    const openHandlers = new Set(), closeHandlers = new Set();
    const pendingSends = [];

    function on(type, handler) {
        if (!textHandlers.has(type)) textHandlers.set(type, new Set());
        textHandlers.get(type).add(handler);
        return () => textHandlers.get(type).delete(handler);
    }
    function onBinary(h) { binaryHandlers.add(h); return () => binaryHandlers.delete(h); }
    function onOpen(h) { openHandlers.add(h); if (isOpen()) { try { h(); } catch (e) { console.error(e); } } return () => openHandlers.delete(h); }
    function onClose(h) { closeHandlers.add(h); return () => closeHandlers.delete(h); }
    function isOpen() { return socket !== null && socket.readyState === WebSocket.OPEN; }

    function send(jsonObj) {
        const payload = JSON.stringify(jsonObj);
        if (isOpen()) { try { socket.send(payload); } catch (e) { console.error('[WS] send failed:', e); } }
        else if (pendingSends.length < SEND_QUEUE_LIMIT) pendingSends.push(payload);
    }
    function sendBinary(buf) {
        if (!isOpen()) return false;
        try { socket.send(buf); return true; } catch (e) { console.error('[WS] sendBinary failed:', e); return false; }
    }
    function flushPending() {
        if (!isOpen()) return;
        while (pendingSends.length > 0) {
            try { socket.send(pendingSends.shift()); } catch (e) { console.error(e); break; }
        }
    }
    function closeSocket() {
        if (heartbeatInterval) { clearInterval(heartbeatInterval); heartbeatInterval = null; }
        if (reconnectTimeout) { clearTimeout(reconnectTimeout); reconnectTimeout = null; }
        if (socket) { socket.onclose = null; try { socket.close(); } catch (e) {} socket = null; }
    }
    function scheduleReconnect() {
        if (isReconnecting) return;
        isReconnecting = true; closeSocket();
        reconnectTimeout = setTimeout(() => {
            isReconnecting = false; connect();
            currentReconnectDelay = Math.min(currentReconnectDelay * 1.5, MAX_RECONNECT_DELAY);
        }, currentReconnectDelay);
    }
    function startHeartbeat() {
        if (heartbeatInterval) clearInterval(heartbeatInterval);
        heartbeatInterval = setInterval(() => {
            const stale = Date.now() - lastMessageTime > MESSAGE_TIMEOUT;
            if (stale || !isOpen()) scheduleReconnect();
        }, HEARTBEAT_INTERVAL);
    }
    function dispatchText(raw) {
        let data; try { data = JSON.parse(raw); } catch (e) { return; }
        const hs = textHandlers.get(data.type);
        if (!hs || hs.size === 0) return;
        hs.forEach(h => { try { h(data); } catch (e) { console.error(e); } });
    }
    function dispatchBinary(buf) { binaryHandlers.forEach(h => { try { h(buf); } catch (e) { console.error(e); } }); }

    function connect() {
        closeSocket();
        try {
            socket = new WebSocket(wsUrl);
            socket.binaryType = 'arraybuffer';
            socket.onopen = () => {
                lastMessageTime = Date.now(); currentReconnectDelay = RECONNECT_DELAY; isReconnecting = false;
                send({ type: 'subscribe', mode: 'targets_only' });
                flushPending(); startHeartbeat();
                openHandlers.forEach(h => { try { h(); } catch (e) {} });
            };
            socket.onmessage = (event) => {
                lastMessageTime = Date.now();
                if (typeof event.data === 'string') dispatchText(event.data);
                else if (event.data instanceof ArrayBuffer) dispatchBinary(event.data);
                else if (event.data && typeof event.data.arrayBuffer === 'function') event.data.arrayBuffer().then(dispatchBinary);
            };
            socket.onerror = (err) => console.error('[WS] Error:', err);
            socket.onclose = () => { closeHandlers.forEach(h => { try { h(); } catch (e) {} }); scheduleReconnect(); };
        } catch (e) { scheduleReconnect(); }
    }

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) return;
        if (Date.now() - lastMessageTime > MESSAGE_TIMEOUT || !isOpen()) scheduleReconnect();
    });
    window.addEventListener('online', () => {
        if (Date.now() - lastMessageTime > MESSAGE_TIMEOUT || !isOpen()) scheduleReconnect();
    });

    window.TargetsWS = { connect, send, sendBinary, on, onBinary, onOpen, onClose, isOpen };
    connect();
})();
