// This is the file that should be uploaded to our server at /api/v3/common/js/servicehandler.js
// It should be the one to base our documentation on, and should be only cautiously changed.

const DEFAULT_TITLER_WS_PORT = 9023;
const DEFAULT_TITLER_HTTP_PORT = 9022;
const WS_TUNNEL_HOST = 'controllers.newbluefx.com';
const HAS_RTC = typeof window !== 'undefined' ? !!window.RTCPeerConnection : false;
const USE_RTC_WEBSOCKET_SIGNALING = true;

// to debug RTC connections, chrome users can visit this page
// chrome://webrtc-internals/

/** JSDOC TYPE DEFINITIONS */
/**
 * @typedef {(...args: any[]) => void} VariadicFunction
 * @typedef {(value?:any) => void} SingleValueCallback
 * @typedef {{send: SingleValueCallback, onmessage: MessageHandler|null}} Transport
 * @typedef {(ev: Event)=>void} EventHandler
 * @typedef {(ev: MessageEvent)=>void} MessageHandler
 * @typedef {(ev: string|Error)=>void} ErrorHandler
 */

/**
 * Returns a promise and a resolver function that can be used to resolve the promise.
 * If a timeout is provided, the promise will be rejected after the timeout has passed.
 *
 * @param {number} [timeout]
 * @returns {{promise:Promise<any>, resolver:SingleValueCallback}} an object containing a promise and its resolver function, which will resolve the promise when called. If timeout is provided, the resolver will be automatically called with null after the timeout has passed.
 */
function makePromise(timeout = undefined, rejectOnTimeout = false) {
  let done = false;

  /** @type {SingleValueCallback}  */
  let resolver = (_value = undefined) => {};
  const promise = new Promise((resolve, reject) => {
    resolver = (value = undefined) => {
      if (!done) {
        done = true;
        resolve(value);
      }
    };
    if (timeout) {
      setTimeout(() => {
        if (!done) {
          done = true;
          if (rejectOnTimeout) {
            reject(new Error('Promise timed out'));
          } else {
            resolver(null);
          }
        }
      }, timeout);
    }
  });
  return { promise, resolver };
}

/**
 * Remove all callbacks before closing a socket or data channel.
 *
 * @param {DataChannelTransport|WebSocket|RTCDataChannel} transport
 */
function _quietClose(transport) {
  transport.onopen = transport.onclose = transport.onmessage = transport.onerror = null;
  transport.close();
}

/**
 * Wraps an RTCDataChannel with a WebSocket-compatible API.
 * This allows code expecting a WebSocket to work with a DataChannel.
 */
class DataChannelTransport {
  // WebSocket readyState constants
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  static _defaultConfiguration() {
    return {
      iceServers: [{ urls: 'stun:stun.l.google.com:19302' }, { urls: 'stun:stun1.l.google.com:19302' }],
    };
  }

  static pc = HAS_RTC ? new RTCPeerConnection(DataChannelTransport._defaultConfiguration()) : null;

  /**
   * Maps RTCDataChannel readyState strings to WebSocket numeric constants
   * @returns {number} WebSocket-compatible readyState (0, 1, 2, or 3)
   */
  get readyState() {
    switch (this.#channel?.readyState) {
      case 'connecting':
        return DataChannelTransport.CONNECTING;
      case 'open':
        return DataChannelTransport.OPEN;
      case 'closing':
        return DataChannelTransport.CLOSING;
      case 'closed':
      default:
        return DataChannelTransport.CLOSED;
    }
  }

  /** @returns {string} The data channel label (analogous to WebSocket url) */
  get url() {
    return this.#channel?.label ?? '';
  }

  /** @returns {string} */
  get protocol() {
    return this.#channel?.protocol ?? '';
  }

  // these callbacks will be overridden by the user of the class

  /** @type {EventHandler}  */
  onopen() {}
  /** @type {EventHandler}  */
  onclose() {}
  /** @type {ErrorHandler}  */
  onerror() {}
  /** @type {MessageHandler}  */
  onmessage() {}

  /** @type {RTCDataChannel|null}  */
  #channel = null;

  /** @type {WebSocket|null} */
  #signalWs = null;

  /** @type {boolean}  */
  #answered = false;

  /**
   * @param {RTCConfiguration|null} configuration - The RTCConfiguration for the RTCPeerConnection
   */
  constructor(configuration = null) {
    if (!DataChannelTransport.pc) {
      console.log('RTCDataChannel not supported in this environment');
      return;
    }

    // if the configuration has changed, create a new peer connection
    if (configuration) {
      // access these statically
      DataChannelTransport.pc?.close();
      DataChannelTransport.pc = new RTCPeerConnection(configuration);
    }
    if (!DataChannelTransport.pc) return;

    // get a const reference to this object for use in callbacks
    const pc = DataChannelTransport.pc;

    /** @type {Promise<void>}}  */
    this.icePending = new Promise((resolve) => {
      if (pc.iceGatheringState === 'complete') {
        resolve();
      } else {
        pc.onicegatheringstatechange = () => {
          if (pc.iceGatheringState === 'complete') {
            resolve();
          }
        };
      }
    });

    pc.onconnectionstatechange = () => {
      console.log('Connection state:', pc.connectionState);
      if (pc.connectionState === 'failed') {
        this.onerror?.('Connection failed');
      }
    };

    // Set up to receive the data channel from the server (we are the responder)
    pc.ondatachannel = (event) => {
      // A new offer on the same peer connection delivers a new channel while
      // the previous one can still be open. Its events would reach this
      // transport's handlers, which now stand for the new channel, so detach
      // and close it first.
      const old = this.#channel;
      if (old && old !== event.channel) {
        _quietClose(old);
      }
      this.#channel = event.channel;
      this.finishDataChannelSetup();
    };

    // this is just for debugging
    pc.oniceconnectionstatechange = () => console.log('ICE connection state:', pc.iceConnectionState);
  }

  /**
   * @deprecated Use the connect(signalUrl) method instead.
   *
   * to use this older method:
   * ```javascript
   * const offerUrl = `https://${this.tunnelHost}/tunnel/${this.wsTunnel}`;
   * const answerUrl = `https://${this.tunnelHost}/tunnel/${this.wsTunnel}/answer`;
   * dt.connectWithUrls(offerUrl, answerUrl);
   * ```
   *
   * It was deprecated because CORS restrictions apply to fetch differently than they do to websockets
   *
   * @param {string} offerUrl the url where an offer can be requested
   * @param {string} acceptUrl the url where an answer can be posted
   */
  async connectWithUrls(offerUrl, acceptUrl) {
    console.log('Connecting with offerUrl:', offerUrl, 'acceptUrl:', acceptUrl);
    const pc = DataChannelTransport.pc;
    if (!pc) return;

    /**
     * @param {RTCSessionDescriptionInit} offer
     * @returns {Promise<RTCSessionDescription|null>} the answer, for the caller to send
     */
    const setOffer = async (offer) => {
      await pc.setRemoteDescription(offer);
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      await this.icePending;
      return pc.localDescription;
    };

    /**
     * @param {RTCSessionDescription} answer
     * @param {string} acceptUrl the url where an answer can be posted
     * @returns {Promise<any>} the response from the server
     */
    const sendAccept = async (answer, acceptUrl) => {
      return fetch(acceptUrl, {
        method: 'POST',
        body: JSON.stringify(answer),
      })
        .then((r) => r.json())
        .then((r) => {
          console.log('Accept response:', r);
        })
        .catch((e) => {
          console.warn('Error sending accept:', e);
        });
    };

    try {
      const { offer } = await fetch(offerUrl)
        .then((r) => r.json())
        .catch((e) => {
          console.warn('Error fetching offer:', e);
          throw e;
        });

      if (offer) {
        // Parse the offer and set as remote description
        const offerData = JSON.parse(offer);
        const answer = await setOffer(offerData);
        if (!answer) throw new Error('Setting the offer did not produce an answer.');
        // Send the answer back
        await sendAccept(answer, acceptUrl);
      }
    } catch (error) {
      console.warn('Error processing RTC offer:', error);
      if (this.onerror) this.onerror(error?.toString() || 'Error processing RTC offer');
    }
  }

  /**
   * Connect via WebSocket signaling. The signal server sends the offer on
   * connect (buffered replay), we answer, then close the signal WebSocket
   * once the data channel opens.
   *
   * @param {string} signalUrl WebSocket URL for signaling (wss://.../tunnel/{ID}/signal)
   */
  connect(signalUrl) {
    if (this.#signalWs) _quietClose(this.#signalWs); // already connecting or connected

    this.#answered = false;
    this.#signalWs = new WebSocket(signalUrl);
    this.#signalWs.onopen = () => {
      console.log('RTC signal WebSocket opened:');
    };
    this.#signalWs.onmessage = (event) => {
      if (this.#answered) return; // ignore any further offers after the first one

      try {
        const data = JSON.parse(event.data);
        if (data.type === 'offer' && !this.#answered) {
          this.#answered = true;
          this.#handleOffer(data);
        }
      } catch (e) {
        console.warn('Failed to parse signal message:', e);
      }
    };

    this.#signalWs.onerror = () => {
      console.warn('RTC signal WebSocket error');
      this.onerror?.('Signal WebSocket error');
    };

    this.#signalWs.onclose = (event) => {
      console.log('RTC signal WebSocket closed:', event);
      this.#signalWs = null;
    };
  }

  /**
   * @param {RTCSessionDescriptionInit} offer
   */
  async #handleOffer(offer) {
    const pc = DataChannelTransport.pc;
    if (!pc) {
      console.warn('No RTCPeerConnection available to handle offer');
      return;
    }
    try {
      await pc.setRemoteDescription(offer);
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      await this.icePending;

      // Send complete answer via signal WebSocket (raw JSON, no header)
      if (this.#signalWs && this.#signalWs.readyState === WebSocket.OPEN) {
        this.#signalWs.send(JSON.stringify(pc.localDescription));
      }
    } catch (error) {
      console.warn('Error processing RTC offer:', error);
      this.onerror?.(error?.toString() || 'Error processing RTC offer');
    }
  }

  finishDataChannelSetup() {
    if (!this.#channel) return;
    console.log('Data channel received:', this.#channel.label);

    // Map DataChannel events to WebSocket-style events
    this.#channel.onclose = (e) => this.onclose?.(e);
    this.#channel.onmessage = (e) => this.onmessage?.(e);
    this.#channel.onerror = (e) => this.onerror?.(e.toString());

    const openHandler = (/** @type {Event} */ e) => {
      // Signal WebSocket is no longer needed — data channel is peer-to-peer
      if (this.#signalWs) {
        this.#signalWs.close();
        this.#signalWs = null;
      }
      this.onopen?.(e);
    };

    // special case for when the channel is already open
    console.log('Data channel readyState:', this.#channel.readyState);
    if (this.#channel.readyState === 'open') {
      openHandler(new Event('open'));
    } else {
      this.#channel.onopen = openHandler;
    }
  }

  /**
   * Send data through the channel
   * @param {string} data The spec says this can be Blob, ArrayBuffer, etc. but we'll just support string for now.
   */
  send(data) {
    if (!this.#channel) return;
    if (this.#channel.readyState !== 'open') return; // can't send to a closed or not-yet-open channel
    this.#channel.send(data);
  }

  /**
   * Close the channel
   */
  close() {
    if (!this.#channel) return;
    _quietClose(this.#channel);
  }
}

/**
 * Helper for connecting to Titler Live server and providing API endpoints.
 */
class ServiceHandlerClient {
  _DEBUGMODE = true; // set to true to enable printing debugging information to console; set to false in release

  /** @type {boolean}  */
  get DEBUGMODE() {
    return this._DEBUGMODE;
  }

  /** @param {boolean} v */
  set DEBUGMODE(v) {
    this._DEBUGMODE = v;
    if (v) {
      this.debug = console.log;
    } else {
      this.debug = (/** @type {any[]}  */ ..._) => {};
    }
  }
  debug = (/** @type {any[]}  */ ..._) => {};

  /** @type {number}  */
  #capBaseTime = 0;
  get capTime() {
    return (Date.now() - this.#capBaseTime) / 1000;
  }

  /** @type {WebSocket|DataChannelTransport|null}  */
  socket = null;

  /**
   * Scheduler API object. Available after onready() callback fires.
   * @type {any} */
  scheduler = undefined;

  /**
   * The original scheduler instance as it comes from the QWebChannel.
   * (not private for debugging reasons mostly)
   * @type {any}
   */
  _scheduler = undefined;

  get connected() {
    return !!this._scheduler;
  }

  /**
   * Web socket server url used to communicate with the server (i.e. "ws://localhost:4321"
   * @type {string?}
   */
  serverUrl = null;

  /**
   * Name of this input, pulled from the url
   * @type {string?}
   */
  inputName = null;

  /**
   * Name of the host domain, pulled from the url
   * @type {string?}
   */
  hostName = null;

  /**
   * put the object here so clients can know it exists
   * @type {Analytics}
   */
  get analytics() {
    return Analytics;
  }

  /** @type {any}  */
  get sp() {
    return this.scheduler;
  }

  /**
   * domain of the websocket tunnel host
   * @type {string}
   */
  tunnelHost = WS_TUNNEL_HOST;

  /**
   * Which connection method is in use?
   */

  /** @type {boolean}  */
  usingDirect = false;
  /** @type {boolean}  */
  usingTunnel = false;
  /** @type {boolean}  */
  rtcReady = false;

  /** @type {Promise<void>|null}  */
  #initPromise = null;
  /** @type {null|SingleValueCallback}  */
  #initResolver = null;

  constructor() {
    // create a fake scheduler for now
    this.setScheduler(null);

    // type safe declarations for use in non-browser environments
    let w = { location: { search: '', hostname: '127.0.0.1' } };
    let d = { cookie: '' };
    if (typeof window !== 'undefined') w = window;
    if (typeof document !== 'undefined') d = document;

    const queryString = w.location.search;
    const query = new URLSearchParams(queryString);
    this.wsPort = query.get('port') ?? DEFAULT_TITLER_WS_PORT;
    this.queryVars = Object.fromEntries(query);
    this.inputName = query.get('inputName') ?? '';
    this.hostName = query.get('hostName') ?? query.get('ip') ?? w.location.hostname;
    this.wsTunnel = query.get('wsTunnel');
    this.httpPort = query.get('httpPort') ?? DEFAULT_TITLER_HTTP_PORT;
    this.preferRtc = query.get('rtc') === 'true';
    this.isProxiedThroughTunnel = false;
    if (w.location.hostname == WS_TUNNEL_HOST) {
      this.isProxiedThroughTunnel = true;
      this.preferDirect = false;
    }

    // backwards compatible fallback for older products
    if (!query.get('port')) {
      const cookie = d.cookie.match(/channel=([0-9]+)/);
      if (cookie) {
        this.wsPort = cookie[1];
      }
    }

    this.sameMachine = null; // will be set later when we do the check
    this.checkSameMachine(); // don't await this here, just let it run
  }

  /**
   * url for accessing certain Titler/Captivate resources directly over http */
  get httpUrl() {
    return `http://${this.hostName}:${this.httpPort}`;
  }

  /**
   * User callback to execute when connection to server is established.
   * @type {() => void}
   */
  onready() {}

  /**
   * User callback to execute when connection to server is terminated.
   * @type {() => void}
   */
  onclose() {}

  /**
   * User callback to execute when an error is encountered trying to communicate with the server.
   * @type {(error: string|Error) => void}
   */
  onerror(/** @type {string|Error}  */ error) {}

  /**
   * Reports the ready state.
   * @type {number}
   */
  get readyState() {
    return this.socket?.readyState ?? WebSocket.CLOSED;
  }

  // public functions that wrap internal functions for ease of use

  /**
   * Just calls scheduleAction on the scheduler object, but uses default parameters if not provided.
   *
   * @param {string} action the action to run
   * @param {string} input the name of the input (must exactly match an input connected to a title)
   * @param {string} title may be the title id or the title name (as it shows up in the project panel)
   * @param {any} variables a javascript object that maps variable names to values
   * @param {((reply:string) => void)|null} callback if not provided, will return a promise that resolves with the result of the action
   * @returns {Promise<string>|void} if a callback is provided, will return void, otherwise will return a promise that resolves with the result of the action
   */
  action(action, input = '', title = '', variables = {}, callback = null) {
    return callback
      ? this.scheduler?.scheduleAction(action, input, title, variables, callback)
      : this.scheduler?.scheduleAction(action, input, title, variables);
  }

  /**
   * Just calls scheduleCommand on the scheduler object, but uses default parameters if not provided.
   *
   * @param {string} command the command to send
   * @param {any} parameters the parameters to send to the command
   * @param {any} variables the variables to include with the command
   * @param {((reply:string) => void)|null} callback if not provided, will return a promise that resolves with the result of the action
   * @returns {Promise<any>|void} if a callback is provided, will return void, otherwise will return a promise that resolves with the result of the command
   */
  command(command, parameters = {}, variables = {}, callback = null) {
    return callback
      ? this.scheduler?.scheduleCommand(command, parameters, variables, callback)
      : this.scheduler?.scheduleCommand(command, parameters, variables);
  }

  async checkSameMachine() {
    if (typeof fetch !== 'function') return false;
    if (this.sameMachine != null) return this.sameMachine;
    try {
      const url = new URL(`http://${this.hostName}:${this.httpPort}/`);
      const r = await fetch(url, { method: 'HEAD' }).catch(() => null);
      if (r && r.ok) {
        if (r.headers.get('X-Same-Machine') === 'true') {
          this.debug(`We are on the same machine as ${this.hostName}, so we can prefer direct connections.`);
          this.trustedHost = this.hostName;
          this.sameMachine = true; // we are on the same machine as Captivate, so we don't need webrtc
          return true;
        }
      }
    } catch (e) {}
    this.sameMachine = false;
    return false;
  }

  /**
   * will attempt to connect to a websocket and resolve with the socket
   * or null if the timeout has passed
   *
   * @param {string} url
   * @param {number} timeout
   * @returns {Promise<WebSocket|null>}
   */
  async attemptSocket(url, timeout = 1000) {
    this.debug(`attemptSocket: ${url}`);
    let done = false;
    return new Promise((resolve, _reject) => {
      let socket = null;
      try {
        socket = new WebSocket(url);
      } catch (e) {
        resolve(null);
        done = true;
        return;
      }

      socket.onerror = () => {
        resolve(null);
        done = true;
      };
      socket.onopen = () => {
        resolve(socket);
        done = true;
      };
      setTimeout(() => {
        if (!done) {
          socket?.close(); // re-check null inside a callback
          resolve(null);
          done = true;
        }
      }, timeout);
    });
  }

  /**
   * Will attempt to set up a WebRTC connection
   * If this ever connects, it will take priority over the websocket proxy if that's in use
   *
   * @param {boolean} rtcPreferred set to true if you want to use rtc even when a local connection is available
   * @returns
   */
  async startRTC(rtcPreferred = false) {
    this.debug('attempting RTC');

    this.rtcReady = false;
    if (!DataChannelTransport.pc) {
      console.warn('WebRTC not supported in this environment, cannot establish RTC connection');
      return;
    }
    if (!this.wsTunnel) {
      console.warn('No tunnel ID available, cannot connect to signal server');
      return;
    }

    // get/create the data channel transport
    const dt = _dct ?? new DataChannelTransport();

    // Set up the onopen handler
    dt.onopen = async () => {
      this.rtcReady = true;
      this.debug('WebRTC connection established...');

      // upgrade a tunnel connection to a WebRTC connection
      // by waiting first for the findSocket promise to finish
      this.socketPromise && (await this.socketPromise);
      if (this.usingDirect && !rtcPreferred) {
        // we already have an established local connection, keep using it, discard the peer object
        this.debug('WebRTC connection ignored... direct connection already made');
        dt.close(); // closes only the data channel; the shared peer connection stays open for later offers
      } else {
        this.usingTunnel = this.usingDirect = false;
        this.debug('WebRTC connection is preferred and will be used...');
        Analytics.saveAction('this', { connection_method: 'WebRTC' });
        // this will also cause the this.onready callback to be called even if it has been called once
        this.wrapSocket(dt);
      }
    };

    // There are two kinds of signaling: WebSocket signaling and HTTP signaling
    // All versions of the Captivate engine should be able to use the websocket method,
    // but older versions may require HTTP signaling. Set the constant above
    // to specify which signaling method to use.
    if (USE_RTC_WEBSOCKET_SIGNALING) {
      const signalUrl = `wss://${this.tunnelHost}/tunnel/${this.wsTunnel}/signal`;
      dt.connect(signalUrl);
    } else {
      const offerUrl = `https://${this.tunnelHost}/tunnel/${this.wsTunnel}`;
      const answerUrl = `https://${this.tunnelHost}/tunnel/${this.wsTunnel}/answer`;
      dt.connectWithUrls(offerUrl, answerUrl);
    }
  }

  /**
   * Will attempt to find a workable socket connection to Captivate in the following order of priority:
   * - the IP address specified in the query string/cookie
   * - the localhost address (127.0.0.1)
   * - a WebRTC peer
   * - our websocket tunnel
   *
   * @returns {Promise<WebSocket|DataChannelTransport|null>} The returned socket should support all the transport functions of a WebSocket, see SimplePeerTransport
   */
  async findSocket(rtcOnly = false) {
    this.usingDirect = false; // reset, since we don't know what will be best

    // WebRTC is negotiated through the tunnel server, keyed by the tunnel id;
    // without one there is nothing to look up, and the lookup would fetch
    // .../tunnel/null on every retry.
    if (rtcOnly) {
      this.startRTC(true); // true to indicate RTC-only mode
      this.socketPromise = Promise.resolve(null);
      return this.socketPromise;
    }

    const { promise, resolver: resolveSocketPromise } = makePromise(5000);
    this.socketPromise = promise;

    // always try to start the rtc connection if we can
    this.startRTC(false);

    let url;

    // we want to try three socket types simultaneously and take the best one
    const promises = [];

    // try the hostname we computed earlier and also explicit ''127.0.0.1''
    const hosts = [this.hostName, '127.0.0.1'];
    for (const host of hosts) {
      // if the hostName (or ip) is provided, attempt it
      url = `ws://${host}:${this.wsPort}/`;
      promises.push(this.attemptSocket(url));
    }

    // also try the wsTunnel (if provided)
    if (this.wsTunnel) {
      url = `wss://${this.tunnelHost}/tunnel/${this.wsTunnel}/client`;
      promises.push(this.attemptSocket(url));
    } else {
      promises.push(Promise.resolve(null));
    }

    const sockets = await Promise.all(promises);
    let found = null;
    for (const [index, socket] of sockets.entries()) {
      if (socket == null) continue;

      // if we have already found one, close this one
      if (found) {
        _quietClose(socket);
        continue;
      }

      // the first two hosts were a direct connection
      // and that is always to be preferred if one of them won
      this.usingDirect = index < 2;

      found = socket; // will use the first found socket
      if (this.usingDirect) {
        this.debug('Found Direct WebSocket Connection... using it');
        Analytics.saveAction('this', { connection_method: 'websocket' });
        this.usingDirect = true;
      } else {
        // If we are here, the first resolved connection was the tunnel connection
        // Since that's sub-optimal, double-check the rtc connection.
        // It's possible that the rtc connection is already prepared.
        // If so, let that one take precedence
        if (this.rtcReady) {
          socket.close();
          found = null;
          continue;
        }
        this.usingTunnel = true;
        this.debug('Using WebSocket Tunnel Proxy... ');
        Analytics.saveAction('this', { connection_method: 'websocket tunnel proxy' });
      }
      this.serverUrl = socket.url;
    }

    resolveSocketPromise(null);
    this.socketPromise = null;
    return found;
  }

  async close() {
    if (this.socket) {
      _quietClose(this.socket);
      this.socket = null;
    }

    // reset the scheduler when closing the connection
    this.setScheduler(null);
  }

  /**
   * Initializes the connection to the Captivate API server.
   * Will only attempt one connection at a time.
   * The arguments are listed this way for backwards compatibility
   *
   * @param {string|null} [url] The URL of the websocket server to connect to.
   * @param {WebSocket|DataChannelTransport|null} [socket] A websocket object to use for the connection.
   * @param {boolean} [preferRtc] If true, will prioritize the use of WebRTC for the connection.
   * @returns {Promise<void>} A promise that resolves when the connection is established or fails.
   */
  async init(url = null, socket = null, preferRtc = false) {
    if (this.#initPromise) return this.#initPromise; // already initializing

    // create a promise that will reject after 10 seconds
    const { promise, resolver } = makePromise(10000, true);
    this.#initPromise = promise;
    this.#initResolver = () => {
      this.#initPromise = null;
      this.#initResolver = null;
      resolver();
    };

    // catch the rejection and resolve with null
    promise.catch(() => {
      this.onerror?.('Initialization timed out');
      this.#initResolver?.();
    });

    // starting from scratch
    this.close(); // remove listeners from any previous instances
    this.setScheduler(null); // reset the scheduler for a fresh connection

    // once rtc is set to preferred, remember that
    preferRtc = preferRtc || this.preferRtc;

    // the connection should happen like this...
    // if a socket is provided, use it
    // if a url is provided, use it
    // otherwise, attempt to construct a socket that will work for us
    if (socket !== null) {
      this.debug('Using provided socket.');
    } else if (url !== null) {
      // We've been passed the websocket url directly.
      this.debug('Using provided ws url.');
      this.debug(url);
      socket = await this.attemptSocket(url);
    } else {
      socket = await this.findSocket(preferRtc);
    }

    if (socket === null) {
      if (this.rtcReady) {
        // it's possible that the rtc connection is already established
        // if so, the resolver will have already been called
        // the resolver will have been resolved by now
        this.debug('WebRTC connection is ready.');
      } else if (DataChannelTransport.pc && preferRtc) {
        // we don't have any normal sockets, but we are specifically prioritizing
        // the WebRTC connection, so we can't say the connection has failed yet
        this.debug('Waiting on WebRTC connection.');
      } else {
        // we might have WebRTC capabilities, but the user didn't prioritize it
        // so consider this connection attempt to be done.
        if (this.DEBUGMODE) console.error('WebSocket error');
        this.#initResolver?.();
        this.onerror?.('could not determine a websocket to use');
      }
    } else {
      this.serverUrl = socket.url;
      this.wrapSocket(socket);
    }

    return promise; // will still exist even if we called #initResolver
  }

  /**
   *
   * @param {WebSocket|DataChannelTransport} socket
   */
  wrapSocket(socket) {
    // if the socket is already the one we're using, no need to wrap it again
    if (socket === this.socket) return;

    // incoming socket is new, so close any previous one.
    this.close();

    // keep track of this one
    this.socket = socket;

    socket.onclose = () => {
      if (this.DEBUGMODE) console.warn('this socket closed');
      this.onclose?.();
      this.#initResolver?.(); // if the channel never opened, this will still exist
    };

    socket.onerror = (/** @type {any}  */ error) => {
      if (this.DEBUGMODE) console.error('this socket error', error);
      this.onerror?.(error);
      this.#initResolver?.();
    };

    socket.onopen = () => {
      this.finishWrap(socket);
    };

    // the socket might have connected already, if so, finish the wrapping right away
    if (socket.readyState === WebSocket.OPEN) {
      this.finishWrap(socket);
    }
  }

  /**
   * @param {WebSocket|DataChannelTransport} socket
   */
  finishWrap(socket) {
    this._scheduler = null;
    this.debug('this socket connected, setting up QWebChannel');

    const doFail = () => {
      // if a different socket has come in while we were waiting here,
      // then, we have nothing to do and should not trigger errors
      if (socket !== this.socket) return; // this socket was replaced or closed
      this.#initResolver?.();
      this.onerror?.(new Error('Failed to establish QWebChannel connection'));
    };

    // the webchannel connection shouldn't take longer than 3 seconds to establish
    const failTimeout = setTimeout(doFail, 3000);

    // Establish API connection.
    try {
      new __QWebChannel(socket, async (channel) => {
        this.debug('QWebChannel connected');
        clearTimeout(failTimeout);

        Analytics.init(channel.objects.scheduler);
        this.setScheduler(channel.objects.scheduler);
        await this.checkCapTime();
        this.notifyReady();
        this.#initResolver?.();

        // if a tunnel was specified, we should attempt to notify the server that
        // a controller is publically available
        if (this.wsTunnel) {
          this.phoneHome();
        }
      });
    } catch (e) {
      clearTimeout(failTimeout);
      doFail();
    }
  }

  /**
   * wrap the scheduler functions in promises so we can use them with async/await paradigms
   * @param {any} scheduler
   */
  setScheduler(scheduler) {
    // save whatever we were given to the original scheduler object
    this._scheduler = scheduler;

    if (!scheduler || typeof scheduler !== 'object') scheduler = {}; // ensure we have an object to work with

    // the new method is to wrap the scheduler in a proxy object that will return promises if the final argument is not a function
    // this also allows us to return empty values for functions that don't exist on the scheduler, thus avoiding
    // version exceptions.

    // walk the scheduler object for signals and modify them to return cancel functions
    // whenever a signal is connected to.
    for (const key of Object.keys(scheduler)) {
      const prop = scheduler[key];
      if (typeof prop === 'object' && 'connect' in prop) {
        // prop is a signal object
        // replace the original connect function with a new one that returns a cancel function
        const { connect, disconnect } = prop;
        prop._connect = connect;
        prop.connect = (/** @type {*} */ callback) => {
          if (typeof callback !== 'function') {
            console.warn(`Signal ${key}.connect called without callback`);
            return;
          }

          // actually call the connect function
          connect(callback);

          const canceler = () => {
            disconnect(callback);
            console.log(`Signal ${key} disconnected`);
          };
          return canceler;
        };
      }
    }

    // make a proxy object for all scheduler functions
    // If the function is called with a callback as the last argument, it will be called as normal.
    // If the function is called without a callback, it will return a promise that resolves when the function completes.
    this.scheduler = new Proxy(scheduler, {
      get: (target, prop, _) => {
        const realprop = target[prop];
        if (!realprop) {
          return () => {
            console.warn('Method not found on scheduler: ', prop);
            return Promise.resolve({
              success: false,
              error:
                "Method not found. Either we have lost connection to Captivate, or the installed version doesn't support this API method.",
            });
          };
        }
        if (typeof realprop === 'function') {
          return (/** @type {any[]}  */ ...args) => {
            const lastarg = args[args.length - 1];
            if (typeof lastarg === 'function') {
              realprop(...args);
            } else {
              return new Promise((resolve, _) => {
                args.push((/** @type {string} */ reply) => {
                  try {
                    const parsed = JSON.parse(reply);
                    resolve(parsed);
                  } catch (e) {
                    resolve(reply);
                  }
                });
                try {
                  realprop(...args);
                } catch (e) {
                  // console.log(e);
                  console.warn('Error in scheduler call. Property found, but function failed: ', e);
                }
              });
            }
          };
        }
        return realprop;
      },
    });
    this.scheduler._wrapped = true;
  }

  notifyReady() {
    if (typeof this.onready === 'function') this.onready();
    if (typeof window !== 'undefined') {
      // also send a ready event for other code listening for the scheduler object
      const event = new CustomEvent('scheduler-ready', { detail: this.scheduler });
      window.dispatchEvent(event);
    }
  }

  checkCapTime() {
    if (!this.scheduler) return;
    return this.scheduler.getTime().then((/** @type {number}  */ time) => {
      this.#capBaseTime = Date.now() - time * 1000;
    });
  }

  /**
   * This function tells the newbluefx.com about the existence of this controller and the tunnel to reach it.
   * This allows users to view their currently active controllers from their account page and they can also reach the
   * controller UI by clicking on the link provided there.
   *
   * @returns void
   */
  async phoneHome() {
    // NOTE: the phone home operation only works if we are being locally hosted.
    await this.checkSameMachine(); // will be immediate if we've already checked
    if (!this.sameMachine) {
      console.warn('Phone home only works when running on the same machine as Captivate');
      return;
    }
    if (!this.scheduler) {
      console.warn('Phone home should be called after the scheduler is ready');
      return;
    }

    // check the backend for the real tunnel (don't trust the wsTunnel query variable)
    // It's almost certainly the same, but this request is fast and it will ensure that we have the correct tunnel id.
    let r = await this.scheduler.getTunnelInfo();
    let tunnelId = r?.tunnel_id ?? this.wsTunnel;
    if (tunnelId != this.wsTunnel) {
      // the backend has a real tunnel id, so use that instead of the one we were given
      // (we also update the current url)
      this.wsTunnel = tunnelId;
      console.log('Using backend tunnel id: ' + tunnelId);
      const searchParams = new URLSearchParams(window.location.search);
      searchParams.set('wsTunnel', tunnelId);
      const newUrl = `${window.location.pathname}?${searchParams.toString()}`;
      window.history.replaceState(null, '', newUrl);
    }
    if (!this.wsTunnel) {
      console.warn('Phone home requires a tunnel id, but none was found');
      return;
    }

    // get the user's machine id and possible api token
    const { user } = await this.command('getReport');
    const { machineId, apiToken } = user;
    if (!machineId || !apiToken) return;

    // get the current version
    const { api_version } = await this.command('getProductInfo');

    // get the current project name
    const { filePath = '' } = await this.command('getCurrentProject');
    const basename = filePath?.split(/[/\\]/g).pop() ?? '';

    // report public availability of this controller
    if (api_version >= '240910') {
      const inputName = `${basename} - ${this.inputName}`;
      if (typeof window === 'undefined') return;
      if (typeof fetch === 'undefined') return;
      const localUrl = window.location.href;
      if (localUrl.match(/\/tunnel\/.*?\/proxy/)) {
        console.log('Controller is already publicly available at: ' + localUrl);
        return;
      }

      const tunnelId = this.wsTunnel;
      const publicUrl = new URL(localUrl);
      publicUrl.hostname = WS_TUNNEL_HOST;
      publicUrl.protocol = 'https';
      publicUrl.port = '';
      const char = publicUrl.pathname.startsWith('/') ? '' : '/';
      publicUrl.pathname = `/tunnel/${tunnelId}/proxy${char}${publicUrl.pathname}`;

      // make sure it actually is available
      let r = {};
      try {
        r = await fetch(publicUrl).catch(() => ({ ok: false }));
      } catch (e) {
        console.log(e);
      }
      if (r instanceof Response && r.ok) {
        const reply = await fetch('https://newbluefx.com/api/register-remote-control.php', {
          method: 'POST',
          body: JSON.stringify({
            machineId,
            tunnelId,
            inputName,
            apiToken,
            localUrl,
            publicUrl: publicUrl.toString(),
          }),
        }).then((r) => r.json());
        if (reply.success) {
          console.log(`Controller is publicly available at: ${publicUrl.toString()}`);
        }
      }
    }
  }
}

// biome-ignore lint/complexity/noStaticOnlyClass:
class Analytics {
  /** @type {any} */
  static #capture = null;

  // if the analytics are called before the scheduler is set, they will be queued and sent when the scheduler is set
  /** @type {Array<{controllerName:string, eventProperties:any, isSettingsEvent:boolean, allowsMany:boolean}>} */
  static #queue = [];

  /**
   * @param {any} scheduler is the scheduler object from the Captivate / Titler / this library
   */
  static init(scheduler) {
    if (!('_analytics_capture' in scheduler)) {
      console.warn('Analytics not enabled in this version of Captivate');
      return;
    }
    console.log('Analytics initialized');
    Analytics.#capture = scheduler._analytics_capture;
    Analytics.dequeue();
  }

  static dequeue() {
    if (!Analytics.#capture) {
      return;
    }
    if (Analytics.#queue.length === 0) return;
    console.log('Analytics initialized. Sending previously queued events.');
    while (Analytics.#queue.length > 0) {
      const item = Analytics.#queue.shift();
      if (!item) continue;
      const { controllerName, eventProperties, isSettingsEvent, allowsMany } = item;
      Analytics.save(controllerName, eventProperties, isSettingsEvent, allowsMany);
    }
  }

  /**
   *
   * @param {string} controllerName the name of the data controller in charge of sending this event
   * @param {any} eventProperties javascript object that simply maps keys to values
   * @param {boolean} isSettingsEvent use to indicate that this event is tracking a user setting
   * @param {boolean} allowsMany when true, each event is tracked as a separate event, when false, only one event is tracked and a frequency is incremented
   */
  static save(controllerName, eventProperties, isSettingsEvent, allowsMany = false) {
    return new Promise((resolve, _) => {
      const capture = Analytics.#capture;
      if (!capture) {
        console.warn('Analytics not initialized. Call Analytics.init(scheduler) first. Event will be queued.');
        Analytics.#queue.push({ controllerName, eventProperties, isSettingsEvent, allowsMany });
        resolve({ error: 'Analytics not initialized. Call Analytics.init(scheduler) first. Event will be queued.' });
        return;
      }
      const r = capture(controllerName, eventProperties, isSettingsEvent, allowsMany);
      resolve({ success: true, response: r });
    });
  }

  /**
   *
   * @param {string} controllerName the name of the data controller in charge of sending this event
   * @param {string} eventName the name of the event sent by the controller
   * @param {any} eventProperties javascript object that simply maps keys to values
   * @param {boolean} allowsMany when true, each event is tracked as a separate event, when false, only one event is tracked and a frequency is incremented
   */
  static saveEvent(controllerName, eventName, eventProperties = {}, allowsMany = false) {
    return Analytics.save(controllerName, { ...eventProperties, event: eventName }, false, allowsMany);
  }

  /**
   *
   * @param {string} controllerName the name of the data controller in charge of sending this event
   * @param {any} eventProperties javascript object that simply maps keys to values
   * @param {boolean} allowsMany when true, each event is tracked as a separate event, when false, only one event is tracked and a frequency is incremented
   */
  static saveAction(controllerName, eventProperties, allowsMany = false) {
    return Analytics.save(controllerName, eventProperties, false, allowsMany);
  }

  /**
   *
   * @param {string} controllerName the name of the data controller in charge of sending this event
   * @param {any} eventProperties javascript object that simply maps keys to values
   * @param {boolean} allowsMany when true, each event is tracked as a separate event, when false, only one event is tracked and a frequency is incremented
   */
  static saveSetting(controllerName, eventProperties, allowsMany = false) {
    return Analytics.save(controllerName, eventProperties, true, allowsMany);
  }
}

// start the ice server lookup as soon as we can
const _dct = new DataChannelTransport();

// expose the service handler as a singleton
const ServiceHandler = new ServiceHandlerClient();

// expose the service handler to the global window object if available
if (typeof window !== 'undefined') {
  /** @type {any}  */
  const w = window;
  w.Analytics = Analytics;
  w.ServiceHandler = ServiceHandler;
}

// @deprecated: CommonJS imports with Node.js are deprecated in favor of ES6 module imports.
if (typeof module === 'object') {
  module.exports = {
    ServiceHandler,
    ServiceHandlerClient,
    Analytics,
    DataChannelTransport,
  };
}

/**
 * This is an exact copy of the qwebchannel.js from the Qt WebChannel repository
 *
 * But it has finally been embedded into our main library file. The need to import it separately
 * is now in the past. Additionally, putting it here allows us to make it into a proper ES6 module
 * and to add JSDoc types.
 */

/* === HERE IS THE ORIGINAL COPYRIGHT NOTICE FROM THE QT WEBCHANNEL REPOSITORY: ======= */

/****************************************************************************
 **
 ** Copyright (C) 2016 The Qt Company Ltd.
 ** Copyright (C) 2016 Klarälvdalens Datakonsult AB, a KDAB Group company, info@kdab.com, author Milian Wolff <milian.wolff@kdab.com>
 ** Contact: https://www.qt.io/licensing/
 **
 ** This file is part of the QtWebChannel module of the Qt Toolkit.
 **
 ** $QT_BEGIN_LICENSE:LGPL$
 ** Commercial License Usage
 ** Licensees holding valid commercial Qt licenses may use this file in
 ** accordance with the commercial license agreement provided with the
 ** Software or, alternatively, in accordance with the terms contained in
 ** a written agreement between you and The Qt Company. For licensing terms
 ** and conditions see https://www.qt.io/terms-conditions. For further
 ** information use the contact form at https://www.qt.io/contact-us.
 **
 ** GNU Lesser General Public License Usage
 ** Alternatively, this file may be used under the terms of the GNU Lesser
 ** General Public License version 3 as published by the Free Software
 ** Foundation and appearing in the file LICENSE.LGPL3 included in the
 ** packaging of this file. Please review the following information to
 ** ensure the GNU Lesser General Public License version 3 requirements
 ** will be met: https://www.gnu.org/licenses/lgpl-3.0.html.
 **
 ** GNU General Public License Usage
 ** Alternatively, this file may be used under the terms of the GNU
 ** General Public License version 2.0 or (at your option) the GNU General
 ** Public license version 3 or any later version approved by the KDE Free
 ** Qt Foundation. The licenses are as published by the Free Software
 ** Foundation and appearing in the file LICENSE.GPL2 and LICENSE.GPL3
 ** included in the packaging of this file. Please review the following
 ** information to ensure the GNU General Public License requirements will
 ** be met: https://www.gnu.org/licenses/gpl-2.0.html and
 ** https://www.gnu.org/licenses/gpl-3.0.html.
 **
 ** $QT_END_LICENSE$
 **
 ****************************************************************************/

/**
 * JSDoc Types
 * @typedef {{connect: (callback: Function)=>void, disconnect: (callback: Function)=>void}} __QSignal
 **/

const __QWebChannelMessageTypes = {
  signal: 1,
  propertyUpdate: 2,
  init: 3,
  idle: 4,
  debug: 5,
  invokeMethod: 6,
  connectToSignal: 7,
  disconnectFromSignal: 8,
  setProperty: 9,
  response: 10,
};

// rename the class in case the user has previously imported the qwebchannel library
class __QWebChannel {
  /** @type {number}  */
  execId = 0;

  /** @type {Record<string, SingleValueCallback>}  */
  execCallbacks = {};

  /** @type {Record<string, __QObject>}  */
  objects = {};

  // set the functions here for type checking, but they will be overridden below
  handleSignal(/** @type {any} */ data) {}
  handleResponse(/** @type {any} */ data) {}
  handlePropertyUpdate(/** @type {any} */ data) {}
  exec(/** @type {any} */ data, /** @type {SingleValueCallback|undefined} */ callback = undefined) {}
  debug(/** @type {string} */ message) {}

  /**
   * @param {*} data
   */
  send(data) {
    if (typeof data !== 'string') {
      data = JSON.stringify(data);
    }
    this.transport.send(data);
  }

  /**
   *
   * @param {Transport} transport
   * @param {(channel: __QWebChannel) => void} initCallback
   * @returns
   */
  constructor(transport, initCallback) {
    if (typeof transport !== 'object' || typeof transport.send !== 'function') {
      console.error(
        'The QWebChannel expects a transport object with a send function and onmessage callback property.' +
          ' Given is: transport: ' +
          typeof transport +
          ', transport.send: ' +
          typeof transport.send
      );
      return;
    }

    /** @type {__QWebChannel}  */
    let channel = this;

    /** @type {Transport}  */
    this.transport = transport;

    this.transport.onmessage = function (/** @type {any}  */ message) {
      let data = message.data;
      if (typeof data === 'string') {
        data = JSON.parse(data);
      }
      switch (data.type) {
        case __QWebChannelMessageTypes.signal:
          channel.handleSignal(data);
          break;
        case __QWebChannelMessageTypes.response:
          channel.handleResponse(data);
          break;
        case __QWebChannelMessageTypes.propertyUpdate:
          channel.handlePropertyUpdate(data);
          break;
        default:
          console.error('invalid message received:', message.data);
          break;
      }
    };

    this.execCallbacks = {};
    this.execId = 0;
    this.exec = function (
      /** @type {any}  */ data,
      /** @type {SingleValueCallback|undefined}  */ callback = undefined
    ) {
      if (!callback) {
        // if no callback is given, send directly
        channel.send(data);
        return;
      }
      if (channel.execId === Number.MAX_VALUE) {
        // wrap
        channel.execId = Number.MIN_VALUE;
      }
      if (data.hasOwnProperty('id')) {
        console.error('Cannot exec message with property id: ' + JSON.stringify(data));
        return;
      }
      data.id = channel.execId++;
      channel.execCallbacks[data.id] = callback;
      channel.send(data);
    };

    this.handleSignal = function (message) {
      let object = channel.objects[message.object];
      if (object) {
        object.signalEmitted(message.signal, message.args);
      } else {
        console.warn('Unhandled signal: ' + message.object + '::' + message.signal);
      }
    };

    this.handleResponse = function (message) {
      if (!message.hasOwnProperty('id')) {
        console.error('Invalid response message received: ', JSON.stringify(message));
        return;
      }
      channel.execCallbacks[message.id]?.(message.data);
      delete channel.execCallbacks[message.id];
    };

    this.handlePropertyUpdate = function (message) {
      for (var i in message.data) {
        let data = message.data[i];
        let object = channel.objects[data.object];
        if (object) {
          object.propertyUpdate(data.signals, data.properties);
        } else {
          console.warn('Unhandled property update: ' + data.object + '::' + data.signal);
        }
      }
      channel.exec({ type: __QWebChannelMessageTypes.idle });
    };

    this.debug = function (message) {
      channel.send({ type: __QWebChannelMessageTypes.debug, data: message });
    };

    channel.exec({ type: __QWebChannelMessageTypes.init }, function (/** @type {any}  */ data) {
      for (var objectName in data) {
        let object = new __QObject(objectName, data[objectName], channel);
      }
      // now unwrap properties, which might reference other registered objects
      for (var objectName in channel.objects) {
        channel.objects[objectName].unwrapProperties();
      }
      if (initCallback) {
        initCallback(channel);
      }
      channel.exec({ type: __QWebChannelMessageTypes.idle });
    });
  }
}

class __QObject {
  /** @type {__QSignal|undefined} */
  destroyed = undefined;

  /** @type {any}  */
  __objectSignals__ = {};
  /** @type {any}  */
  __propertyCache__ = {};

  /**
   *
   * @param {string} name
   * @param {any} data
   * @param {__QWebChannel} webChannel
   */
  constructor(name, data, webChannel) {
    this.__id__ = name;
    webChannel.objects[name] = this;

    // List of callbacks that get invoked upon signal emission
    this.__objectSignals__ = {};

    // Cache of all properties, updated when a notify signal is emitted
    this.__propertyCache__ = {};

    let self = this;

    // ----------------------------------------------------------------------

    this.unwrapQObject = function (/** @type {any}  */ response) {
      if (response instanceof Array) {
        // support list of objects
        let ret = new Array(response.length);
        for (var i = 0; i < response.length; ++i) {
          ret[i] = self.unwrapQObject(response[i]);
        }
        return ret;
      }
      if (!response || !response['__QObject*__'] || response.id === undefined) {
        return response;
      }

      let objectId = response.id;
      if (webChannel.objects[objectId]) return webChannel.objects[objectId];

      if (!response.data) {
        console.error('Cannot unwrap unknown QObject ' + objectId + ' without data.');
        return;
      }

      const qObject = new __QObject(objectId, response.data, webChannel);
      qObject.destroyed?.connect(function () {
        if (webChannel.objects[objectId] === qObject) {
          delete webChannel.objects[objectId];
          // reset the now deleted QObject to an empty {} object
          // just assigning {} though would not have the desired effect, but the
          // below also ensures all external references will see the empty map
          // NOTE: this detour is necessary to workaround QTBUG-40021
          let propertyNames = [];
          for (var propertyName in qObject) {
            propertyNames.push(propertyName);
          }
          for (var idx in propertyNames) {
            // @ts-ignore
            delete qObject[propertyNames[idx]];
          }
        }
      });
      // here we are already initialized, and thus must directly unwrap the properties
      qObject.unwrapProperties();
      return qObject;
    };

    this.unwrapProperties = function () {
      for (var propertyIdx in self.__propertyCache__) {
        // @ts-ignore
        self.__propertyCache__[propertyIdx] = self.unwrapQObject(self.__propertyCache__[propertyIdx]);
      }
    };

    function addSignal(/** @type {any}  */ signalData, /** @type {boolean}  */ isPropertyNotifySignal) {
      let signalName = signalData[0];
      let signalIndex = signalData[1];
      const signal = {
        connect: function (/** @type {any}  */ callback) {
          if (typeof callback !== 'function') {
            console.error('Bad callback given to connect to signal ' + signalName);
            return;
          }

          self.__objectSignals__[signalIndex] = self.__objectSignals__[signalIndex] || [];
          self.__objectSignals__[signalIndex].push(callback);

          if (!isPropertyNotifySignal && signalName !== 'destroyed') {
            // only required for "pure" signals, handled separately for properties in propertyUpdate
            // also note that we always get notified about the destroyed signal
            webChannel.exec({
              type: __QWebChannelMessageTypes.connectToSignal,
              object: self.__id__,
              signal: signalIndex,
            });
          }
        },
        disconnect: function (/** @type {any}  */ callback) {
          if (typeof callback !== 'function') {
            console.error('Bad callback given to disconnect from signal ' + signalName);
            return;
          }
          self.__objectSignals__[signalIndex] = self.__objectSignals__[signalIndex] || [];
          let idx = self.__objectSignals__[signalIndex].indexOf(callback);
          if (idx === -1) {
            console.error('Cannot find connection of signal ' + signalName + ' to ' + callback.name);
            return;
          }
          self.__objectSignals__[signalIndex].splice(idx, 1);
          if (!isPropertyNotifySignal && self.__objectSignals__[signalIndex].length === 0) {
            // only required for "pure" signals, handled separately for properties in propertyUpdate
            webChannel.exec({
              type: __QWebChannelMessageTypes.disconnectFromSignal,
              object: self.__id__,
              signal: signalIndex,
            });
          }
        },
      };
      Object.defineProperty(self, signalName, {
        configurable: true,
        enumerable: true,
        value: signal,
      });
    }

    /**
     * Invokes all callbacks for the given signalname. Also works for property notify callbacks.
     * @param {string} signalName
     * @param {any[]} signalArgs
     */
    function invokeSignalCallbacks(signalName, signalArgs) {
      let connections = self.__objectSignals__[signalName];
      if (connections) {
        connections.forEach(function (/** @type {VariadicFunction}  */ callback) {
          callback.apply(callback, signalArgs);
        });
      }
    }

    this.propertyUpdate = function (/** @type {any}  */ signals, /** @type {any}  */ propertyMap) {
      // update property cache
      for (var propertyIndex in propertyMap) {
        let propertyValue = propertyMap[propertyIndex];
        self.__propertyCache__[propertyIndex] = propertyValue;
      }

      for (var signalName in signals) {
        // Invoke all callbacks, as signalEmitted() does not. This ensures the
        // property cache is updated before the callbacks are invoked.
        invokeSignalCallbacks(signalName, signals[signalName]);
      }
    };

    this.signalEmitted = function (/** @type {string}  */ signalName, /** @type {any[]}  */ signalArgs) {
      invokeSignalCallbacks(signalName, this.unwrapQObject(signalArgs));
    };

    function addMethod(/** @type {any}  */ methodData) {
      let methodName = methodData[0];
      let methodIdx = methodData[1];
      const method = function () {
        let args = [];
        /** @type {CallableFunction|null}  */
        let callback = null;
        for (var i = 0; i < arguments.length; ++i) {
          let argument = arguments[i];
          if (typeof argument === 'function') callback = argument;
          else if (argument instanceof __QObject && webChannel.objects[argument.__id__] !== undefined)
            args.push({
              id: argument.__id__,
            });
          else args.push(argument);
        }

        webChannel.exec(
          {
            type: __QWebChannelMessageTypes.invokeMethod,
            object: self.__id__,
            method: methodIdx,
            args: args,
          },
          function (response) {
            if (response !== undefined) {
              let result = self.unwrapQObject(response);
              if (callback) {
                callback(result);
              }
            }
          }
        );
      };
      Object.defineProperty(self, methodName, {
        configurable: true,
        enumerable: true,
        value: method,
      });
    }

    function bindGetterSetter(/** @type {any}  */ propertyInfo) {
      let propertyIndex = propertyInfo[0];
      let propertyName = propertyInfo[1];
      let notifySignalData = propertyInfo[2];
      // initialize property cache with current value
      // NOTE: if this is an object, it is not directly unwrapped as it might
      // reference other QObject that we do not know yet
      self.__propertyCache__[propertyIndex] = propertyInfo[3];

      if (notifySignalData) {
        if (notifySignalData[0] === 1) {
          // signal name is optimized away, reconstruct the actual name
          notifySignalData[0] = propertyName + 'Changed';
        }
        addSignal(notifySignalData, true);
      }

      Object.defineProperty(self, propertyName, {
        configurable: true,
        get: function () {
          let propertyValue = self.__propertyCache__[propertyIndex];
          if (propertyValue === undefined) {
            // This shouldn't happen
            console.warn(
              'Undefined value in property cache for property "' + propertyName + '" in object ' + self.__id__
            );
          }

          return propertyValue;
        },
        set: function (value) {
          if (value === undefined) {
            console.warn('Property setter for ' + propertyName + ' called with undefined value!');
            return;
          }
          self.__propertyCache__[propertyIndex] = value;
          let valueToSend = value;
          if (valueToSend instanceof __QObject && webChannel.objects[valueToSend.__id__] !== undefined)
            valueToSend = { id: valueToSend.__id__ };
          webChannel.exec({
            type: __QWebChannelMessageTypes.setProperty,
            object: self.__id__,
            property: propertyIndex,
            value: valueToSend,
          });
        },
      });
    }

    // ----------------------------------------------------------------------

    data.methods.forEach(addMethod);

    data.properties.forEach(bindGetterSetter);

    data.signals.forEach(function (/** @type {any}  */ signal) {
      addSignal(signal, false);
    });

    for (const name of Object.keys(data.enums ?? [])) {
      Object.defineProperty(self, name, {
        configurable: true,
        enumerable: true,
        value: data.enums[name],
      });
    }
  }
}
