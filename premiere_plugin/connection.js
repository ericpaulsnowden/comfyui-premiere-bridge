/*
 * connection.js -- persistent websocket client to the bridge server's
 * `GET /cprb/ws` route (docs/PROTOCOL.md sec. 10): hello -> hello_ack ->
 * ready handshake, then server-pushed `pr_result` messages for as long as
 * the socket lives, reconnecting forever on a fixed backoff schedule.
 *
 * A deliberately scoped-DOWN port of the Photoshop bridge's ConnectionManager
 * (comfyui-photoshop-bridge/photoshop_plugin/connection.js): same backoff
 * schedule, same failure taxonomy (constructor throw = the blocking
 * permission shape; close-before-open = server not up; close-after-connected
 * = connection lost), same host:port normalization + localStorage
 * persistence, and -- since the owner's 2026-07-24 parity pass -- the same
 * STANDBY model: an explicit user Disconnect (or being superseded by another
 * panel, close code 4000) parks the client with the reconnect loop OFF until
 * an explicit Connect. The 4000 half also fixes a protocol drift: PROTOCOL.md
 * §10.1 has always specified "the plugin treats 4000 as 'another panel took
 * over' and does not auto-reconnect", which the earlier
 * reconnect-on-schedule behavior here violated. Still dropped on purpose
 * because M1 needs none of it: base64 chunk transfer, request_file/upload
 * plumbing, and local/remote mode (pr_result carries same-machine paths).
 *
 * Wire messages (client -> server):
 *   {"type":"hello","plugin_version":"<manifest version>"}  on socket open
 *   {"type":"ready"}                                        after hello_ack
 *   {"type":"pong"}                                         reply to a server
 *     ping -- M1's server never pings, but §10.2 already accepts pong, so
 *     answering costs nothing now and makes the noted keepalive hardening a
 *     server-only change later (the same additive reasoning, mirrored).
 *   ({"type":"export_ready", ...} is RESERVED for M2's send-back direction
 *    -- nothing here sends it yet; the panel's S7 button only PROBES the
 *    Premiere-side export API that would feed it.)
 * (server -> client):
 *   {"type":"hello_ack","server_version":"..."}  handled internally
 *   {"type":"pr_result", ...}                    -> window.cprbHandleResult
 *   anything else                                -> debug-logged, ignored
 *
 * Depends on helpers.js (log/bad/logDebug). Defines the singleton
 * `cprbConnection`; main.js assigns `cprbConnection.onStateChange` and calls
 * `cprbConnection.start()` once at panel load.
 */
'use strict';

/** Default ComfyUI server base as `host:port`. 8188 is ComfyUI's default;
 * the manifest also allows 8199 (the dev test rig). */
const CPRB_DEFAULT_SERVER_BASE = 'localhost:8188';

/** localStorage key the configured server base persists under. */
const CPRB_SERVER_BASE_KEY = 'cprb.serverBase';

/** Backoff between reconnect attempts in ms, capping at 10s forever --
 * cpsb's proven schedule, unchanged. */
const CPRB_BACKOFF_STEPS_MS = [1000, 2000, 5000, 10000];

/** `WebSocket.readyState` value meaning "open". */
const CPRB_WS_OPEN = 1;

/** Application close code the server uses when a NEW panel connection
 * displaces this one (one panel slot per server). Receiving it means another
 * panel took over, so this client STANDS BY instead of auto-reconnecting --
 * fighting back would kick the other panel off and start an endless
 * tug-of-war (cpsb field lesson, and PROTOCOL.md §10.1's stated contract).
 * The user re-claims the slot with the panel's Connect button. */
const CPRB_WS_CLOSE_REPLACED = 4000;

/**
 * Normalizes a user-entered server address to a bare `host:port` base.
 * Accepts forgiving forms -- `localhost:8199`, `http://192.168.1.50:8188`,
 * `ws://host:8188/cprb/ws`, or a bare host (port defaults to ComfyUI's
 * 8188) -- by stripping any scheme and any path/query/fragment. Throws an
 * Error with a user-facing message on empty/malformed input; main.js
 * surfaces that message in the activity log. Ported from cpsb.
 */
function cprbNormalizeServerBase(input) {
  if (typeof input !== 'string') {
    throw new Error('Server address must be text like "localhost:8188"');
  }
  let s = input.trim();
  if (!s) throw new Error('Enter a server address, e.g. "localhost:8188"');
  // Strip a leading scheme (http://, ws://, ...) if present.
  s = s.replace(/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//, '');
  // Keep only the authority -- drop any /path, ?query, or #fragment.
  s = s.replace(/[/?#].*$/, '');
  if (!s) throw new Error(`"${input}" has no host part`);
  let host = s;
  let port = '8188';
  const colon = s.lastIndexOf(':');
  if (colon !== -1) {
    host = s.slice(0, colon);
    port = s.slice(colon + 1);
  }
  if (!host) throw new Error(`"${input}" has no host part`);
  if (!/^[A-Za-z0-9.\-]+$/.test(host)) {
    throw new Error(`"${host}" is not a valid host name or IP address`);
  }
  if (!/^[0-9]+$/.test(port)) {
    throw new Error(`Port "${port}" must be a number`);
  }
  const portNum = Number(port);
  if (portNum < 1 || portNum > 65535) {
    throw new Error(`Port ${portNum} is out of range (1-65535)`);
  }
  return `${host}:${portNum}`;
}

/**
 * Reads the persisted server base, falling back to the default.
 * PROVEN on Premiere UXP (owner live session 2026-07-24: the panel came back
 * with his previously-entered server address, so `localStorage` exists and
 * survives a restart -- the old VERIFY(spike-S6-followup) is retired). Still
 * guarded so an absent/broken localStorage just means the panel starts on
 * the default every session -- never a dead panel.
 */
function cprbLoadPersistedServerBase() {
  try {
    const stored =
      typeof localStorage !== 'undefined' && localStorage.getItem(CPRB_SERVER_BASE_KEY);
    if (stored) return cprbNormalizeServerBase(stored);
  } catch (_) { /* unavailable or a corrupt stored value -- use the default */ }
  return CPRB_DEFAULT_SERVER_BASE;
}

/** Persists the server base, best-effort (same PROVEN note as the loader). */
function cprbPersistServerBase(base) {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(CPRB_SERVER_BASE_KEY, base);
    }
  } catch (_) {
    logDebug('could not persist the server address -- it will reset next launch');
  }
}

/**
 * The plugin's manifest version, for the hello message.
 * PROVEN on Premiere UXP (owner live session 2026-07-24: the panel header
 * showed the real manifest version, and the layout log line carried
 * `uxp uxp-9.3.0-local` -- `uxp.versions.*` works here; the old
 * VERIFY(spike-S6-followup) is retired). Non-load-bearing -- the server only
 * logs it -- so the fallback stays a plain "unknown".
 */
function cprbPluginVersion() {
  try {
    const v = require('uxp').versions.plugin;
    if (v) return String(v);
  } catch (_) { /* fall through */ }
  return 'unknown';
}

/**
 * Manages the single websocket connection to the bridge server. Use the
 * shared `cprbConnection` singleton below rather than constructing this.
 *
 * getState() shape:
 *   status            'disconnected' | 'connecting' | 'connected'
 *   ready             true once hello_ack arrived and ready was sent (in
 *                     this client that is exactly status === 'connected')
 *   serverBase        'host:port'
 *   url               'ws://host:port/cprb/ws'
 *   serverVersion     string | null (from hello_ack)
 *   lastError         string | null (most recent failure, cleared on connect)
 *   lastErrorBlocking whether lastError needs USER action (a permission-
 *                     shaped constructor throw) vs transient (server not up
 *                     yet / retrying) -- the panel shouts only the former
 *   standby           'superseded' | 'manual' | null -- non-null when the
 *                     client is intentionally idle with the reconnect loop
 *                     OFF, awaiting an explicit Connect (another panel took
 *                     the slot, or the user pressed Disconnect)
 *   attempts          consecutive failed attempts since the last good handshake
 *   nextRetryAt       epoch ms of the next scheduled attempt, or null
 */
class CprbConnection {
  constructor() {
    this._serverBase = cprbLoadPersistedServerBase();
    this.status = 'disconnected';
    this.ready = false;
    this.serverVersion = null;
    this.lastError = null;
    this.attempts = 0;
    this.nextRetryAt = null;
    /** Panel UI subscribes here: called with getState() on every status
     * change. A single callback (not an EventTarget) on purpose -- the panel
     * is the only consumer, and main.js assigns it before start(). */
    this.onStateChange = null;
    this._socket = null;
    this._reconnectTimer = null;
    this._started = false;
    /** Last failure detail -- repeats stay out of the activity log so a
     * server that is down for an hour doesn't scroll 360 identical lines. */
    this._lastFailureDetail = null;
    /** Detail stashed by an `error` event, if the runtime provided any
     * (the close event that always follows carries it into the log). */
    this._socketErrorDetail = null;
    /** Whether the current lastError is BLOCKING (the user must act -- the
     * constructor-throw / manifest-permission shape) rather than transient
     * (just wait; the loop is retrying). Mirrors cpsb: the panel breaks a
     * blocking error out at the top and keeps transient ones calm. */
    this._lastErrorBlocking = false;
    /** Non-null when the client is intentionally NOT connected and NOT
     * auto-retrying, awaiting an explicit user Connect:
     *   'superseded' -- the server closed us (code 4000) because another
     *                   panel took the single plugin slot; reconnecting
     *                   would kick IT off and loop a two-panel tug-of-war.
     *   'manual'     -- the user pressed Disconnect.
     * null means normal auto-connect/auto-retry behavior. Ported from cpsb;
     * the 'superseded' half is PROTOCOL.md §10.1's stated plugin behavior. */
    this._standby = null;
  }

  /** Starts the manager; only the first call has any effect. */
  start() {
    if (this._started) return;
    this._started = true;
    log(`connecting to ${this.getWsUrl()} ...`);
    this._open();
  }

  /**
   * User-initiated Disconnect (the panel toggle): stop connecting, stop
   * auto-retrying, tear down any socket, and STAY off until an explicit
   * {@link connect}. The whole point (owner ask, 2026-07-24): a user who
   * presses Disconnect must not be reconnected by the retry loop a second
   * later. Ported from cpsb's ConnectionManager.disconnect().
   */
  disconnect() {
    this._standby = 'manual';
    this._teardownSocket();
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
    this.ready = false;
    this.attempts = 0;
    this.nextRetryAt = null;
    this.lastError = null;
    this._lastFailureDetail = null;
    this._lastErrorBlocking = false;
    this.serverVersion = null;
    log('disconnected by user -- standing by until Connect');
    this._setStatus('disconnected');
  }

  /**
   * User-initiated Connect / take-over: clears any standby (manual or
   * superseded) and (re)connects to the current server base, resuming the
   * auto-retry loop. When another panel currently holds the server's single
   * plugin slot, this reclaims it -- the server hands the slot to the newest
   * connector and the other panel stands by instead of fighting back.
   */
  connect() {
    this._standby = null;
    this._started = true;
    log(`connecting to ${this.getWsUrl()} ...`);
    this._reconnectNow();
  }

  /** @returns {string} `host:port` -- prefill the panel's server field with this. */
  getServerBase() {
    return this._serverBase;
  }

  /** @returns {string} the websocket URL derived from the server base. */
  getWsUrl() {
    return `ws://${this._serverBase}/cprb/ws`;
  }

  /**
   * Points the client at a (possibly different) server and reconnects NOW
   * (the panel's Advanced "Apply / Connect"). Throws (from the normalizer)
   * on malformed input -- the caller surfaces the message inline. Applying
   * the UNCHANGED address still forces a fresh attempt on purpose: it
   * doubles as "reconnect now" (skipping a backoff wait). While standing by
   * this is a single attempt -- see {@link _reconnectNow}; the Connect
   * toggle ({@link connect}) is what resumes the auto-retry loop.
   */
  setServerBase(value) {
    const normalized = cprbNormalizeServerBase(value);
    const changed = normalized !== this._serverBase;
    this._serverBase = normalized;
    cprbPersistServerBase(normalized);
    log(changed
      ? `server address set to ${normalized} -- connecting ...`
      : `reconnecting to ${normalized} ...`);
    this._reconnectNow();
  }

  /** @returns the full state snapshot (shape documented on the class). */
  getState() {
    return {
      status: this.status,
      ready: this.ready,
      serverBase: this._serverBase,
      url: this.getWsUrl(),
      serverVersion: this.serverVersion,
      lastError: this.lastError,
      lastErrorBlocking: this._lastErrorBlocking,
      standby: this._standby,
      attempts: this.attempts,
      nextRetryAt: this.nextRetryAt
    };
  }

  /**
   * Detaches handlers from the current socket, then closes it. Handlers are
   * removed BEFORE closing so the imminent close does not run `_onClose`
   * (which would record a failure and schedule a competing reconnect).
   */
  _teardownSocket() {
    if (!this._socket) return;
    const socket = this._socket;
    this._socket = null;
    socket.onopen = null;
    socket.onmessage = null;
    socket.onclose = null;
    socket.onerror = null;
    try { socket.close(); } catch (_) { /* already closing/closed */ }
  }

  /** Clean-slate reconnect against the current server base, immediately.
   * Deliberately does NOT clear `_standby` (cpsb-identical): while standing
   * by, an Advanced "Apply / Connect" gets exactly ONE attempt -- a success
   * clears standby in `_completeHandshake`, a failure schedules no retry, so
   * "Disconnected means disconnected" survives everything except the
   * explicit Connect button ({@link connect}, which clears standby first). */
  _reconnectNow() {
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
    this._teardownSocket();
    this.ready = false;
    this.attempts = 0;
    this.nextRetryAt = null;
    this.lastError = null;
    this._lastFailureDetail = null;
    this._lastErrorBlocking = false;
    this._socketErrorDetail = null;
    this.serverVersion = null;
    this._started = true;
    this._open();
  }

  _open() {
    this.nextRetryAt = null;
    this._socketErrorDetail = null;
    this._setStatus('connecting');
    let socket;
    try {
      // The constructor itself is PROVEN (S6-A): cleartext ws:// to
      // localhost opens from Premiere UXP under the manifest's scoped
      // network.domains. A throw here is the permission-denied shape (e.g. a
      // host:port outside manifest.json's domains list) -- cpsb ground truth.
      socket = new WebSocket(this.getWsUrl());
    } catch (error) {
      this._recordFailure(
        `WebSocket constructor threw: ${error && error.message ? error.message : error}` +
        ` (is ${this._serverBase} allowed by manifest.json network.domains?)`
      );
      // The one BLOCKING failure shape: the user must fix the address /
      // manifest permission -- the panel surfaces this prominently instead
      // of leaving it to the calm "retrying" line (cpsb's split).
      this._lastErrorBlocking = true;
      this._scheduleReconnect();
      this._setStatus('disconnected');
      return;
    }
    this._socket = socket;
    socket.onopen = () => this._onOpen();
    socket.onmessage = (event) => this._onMessage(event);
    socket.onclose = (event) => this._onClose(event);
    socket.onerror = (event) => {
      // Spec-wise `error` carries little detail and `close` always follows,
      // so all bookkeeping lives in `_onClose`; stash any detail this
      // runtime DID attach so the close line can include it.
      const detail = event && (event.message || (event.error && event.error.message));
      if (detail) this._socketErrorDetail = String(detail);
    };
  }

  _onOpen() {
    try {
      this.send({ type: 'hello', plugin_version: cprbPluginVersion() });
    } catch (error) {
      logDebug(`failed to send hello: ${error && error.message ? error.message : error}`);
    }
  }

  _onMessage(event) {
    let msg = null;
    try {
      msg = JSON.parse(event.data);
    } catch (_) {
      logDebug('ignoring a non-JSON websocket frame');
      return;
    }
    try {
      this._handleMessage(msg);
    } catch (error) {
      bad(`error handling a "${msg && msg.type}" message: ${error && error.message ? error.message : error}`);
    }
  }

  _handleMessage(msg) {
    if (!msg || typeof msg.type !== 'string') return;
    if (msg.type === 'hello_ack') {
      this._completeHandshake(msg);
      return;
    }
    if (msg.type === 'ping') {
      // M1's server never pings, but PROTOCOL.md §10.2 already accepts
      // `pong`, so answering now makes the noted keepalive hardening a
      // server-only change later (cpsb answers pings the same way).
      this.send({ type: 'pong' });
      return;
    }
    if (msg.type === 'pr_result') {
      // The import recipe registers itself as window.cprbHandleResult
      // (import_recipe.js); looked up at MESSAGE time, so script order can
      // never race message delivery.
      const handler = typeof window !== 'undefined' ? window.cprbHandleResult : null;
      if (typeof handler !== 'function') {
        bad('pr_result arrived but the import recipe is not loaded');
        return;
      }
      const result = handler(msg);
      if (result && typeof result.catch === 'function') {
        // The recipe queue catches its own errors; this is belt-and-braces
        // so a rejected handler can never surface as an unhandled rejection.
        result.catch((error) => {
          bad(`pr_result handling failed: ${error && error.message ? error.message : error}`);
        });
      }
      return;
    }
    // Unknown types are ignored, per the protocol's forward-compatibility
    // rule -- logged dim so a newer server's messages are visible, not silent.
    logDebug(`unhandled "${msg.type}" message -- ignored`);
  }

  _completeHandshake(msg) {
    if (this.ready) {
      // Duplicate hello_ack -- idempotent; just refresh the version.
      this.serverVersion = msg.server_version || this.serverVersion;
      return;
    }
    this.serverVersion = msg.server_version || null;
    this.send({ type: 'ready' });
    this.ready = true;
    this.attempts = 0;
    this.nextRetryAt = null;
    this.lastError = null;
    this._lastFailureDetail = null;
    this._lastErrorBlocking = false;
    // A completed handshake IS a connection -- whatever standby preceded it
    // (an Apply / Connect while manually disconnected) is over (cpsb rule).
    this._standby = null;
    this._setStatus('connected');
    ok(`connected -- server ${this.serverVersion || '(unversioned)'} at ${this._serverBase}`);
    // Plugin and server versions bump in lockstep (scripts/bump_version.py),
    // so a mismatch means one side is a stale copy -- say so, calmly: it is
    // an "update when convenient" heads-up, never a refusal (the Advanced
    // version line carries the same cue in amber; cpsb PROTOCOL §9 posture).
    const pluginVersion = cprbPluginVersion();
    if (this.serverVersion && pluginVersion !== 'unknown' && this.serverVersion !== pluginVersion) {
      log(`version mismatch: plugin v${pluginVersion} vs server v${this.serverVersion} -- update whichever is behind (connection is fine)`);
    }
  }

  _onClose(event) {
    const wasConnected = this.status === 'connected';
    this._socket = null;
    this.ready = false;
    this.serverVersion = null;
    // Every close-path failure is transient (server not up / refused /
    // dropped) -- "just wait, it's retrying". Only the constructor-throw
    // path in _open is blocking.
    this._lastErrorBlocking = false;
    const code = event ? event.code : undefined;
    const reason = event ? event.reason : '';
    // Match the server's replace-close by code OR reason text -- insurance
    // (from cpsb field experience) against a runtime that surfaces a coded
    // close as a bare 1006 but keeps the reason string.
    const replaced =
      code === CPRB_WS_CLOSE_REPLACED ||
      (typeof reason === 'string' && reason.indexOf('replaced by a new connection') !== -1);
    if (replaced) {
      // Another panel took the server's single plugin slot. STAND BY instead
      // of reconnecting (PROTOCOL.md §10.1, cpsb behavior) -- auto-reconnect
      // here would kick the other panel off and loop a tug-of-war. Not an
      // error, a state: the panel's standby line explains, and the user
      // re-claims with Connect.
      this._standby = 'superseded';
      this.lastError = null;
      log('another panel took over -- standing by until Connect', 'dim');
      this._setStatus('disconnected');
      return;
    }
    if (wasConnected) {
      // An established connection dropped -- not a failed attempt; the
      // attempt counter stays 0 so backoff restarts from the top.
      this.lastError = `connection lost (code ${code}${reason ? `, ${reason}` : ''})`;
      bad(`${this.lastError} -- reconnecting`);
    } else {
      // An attempt that never reached hello_ack. Connection-refused /
      // server-absent surface here (typically code 1006, no reason) --
      // distinguishable in the log from a permission denial, which throws
      // from the constructor instead.
      let detail = `connection failed (code ${code}${reason ? `, ${reason}` : ''})`;
      if (this._socketErrorDetail) detail += `; ${this._socketErrorDetail}`;
      this._recordFailure(detail);
    }
    // Schedule BEFORE announcing so the state snapshot already carries
    // nextRetryAt for the panel's status line.
    this._scheduleReconnect();
    this._setStatus('disconnected');
  }

  /**
   * Records one failed attempt. Only a NEW failure message reaches the
   * activity log -- reconnecting forever every 10s must not scroll the log
   * with identical lines. (The panel's status area shows the calm
   * "Waiting for ComfyUI -- retrying in Ns" countdown instead of a raw
   * attempt count, cpsb wording; `attempts` still drives the backoff step
   * and stays available in getState().)
   */
  _recordFailure(detail) {
    this.attempts += 1;
    this.lastError = detail;
    if (detail !== this._lastFailureDetail) {
      bad(`connect attempt ${this.attempts} failed: ${detail}`);
    }
    this._lastFailureDetail = detail;
  }

  _scheduleReconnect() {
    // Standing by (user-disconnected, or superseded by another panel) means
    // NO auto-retry -- only an explicit connect() resumes the loop. This is
    // the guarantee behind the Disconnect button.
    if (this._standby) return;
    if (this._reconnectTimer) return;
    const step = Math.min(Math.max(this.attempts - 1, 0), CPRB_BACKOFF_STEPS_MS.length - 1);
    const delay = CPRB_BACKOFF_STEPS_MS[step];
    this.nextRetryAt = Date.now() + delay;
    this._reconnectTimer = setTimeout(() => {
      this._reconnectTimer = null;
      this._open();
    }, delay);
  }

  /**
   * Sends one JSON control message. Drops (with a dim log line) if the
   * socket isn't open -- delivery is best-effort; the handshake re-derives
   * all state on the next connection anyway.
   */
  send(message) {
    if (!this._socket || this._socket.readyState !== CPRB_WS_OPEN) {
      logDebug(`dropped "${message && message.type}" message -- socket not open`);
      return;
    }
    this._socket.send(JSON.stringify(message));
  }

  _setStatus(status) {
    this.status = status;
    if (typeof this.onStateChange === 'function') {
      try {
        this.onStateChange(this.getState());
      } catch (error) {
        logDebug(`onStateChange callback threw: ${error && error.message ? error.message : error}`);
      }
    }
  }
}

/** The one websocket connection this panel maintains. */
const cprbConnection = new CprbConnection();
