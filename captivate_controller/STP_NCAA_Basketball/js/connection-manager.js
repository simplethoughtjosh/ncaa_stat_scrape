/**
 * ConnectionManager - Robust WebSocket connection handling for Captivate controllers
 *
 * Provides:
 * - Automatic reconnection with exponential backoff
 * - Connection lost modal after max retries
 * - Easy integration with ServiceHandler
 *
 * One failed connection counts as exactly one failure: a drop usually fires
 * both an error and a close, and only the first of those is acted on.
 *
 * Usage:
 *   ConnectionManager.init({
 *     onReady: function() { // your setup code (runs again on every reconnect) },
 *     onDisconnect: function(info) { // link is down; info: { attempt, delay } },
 *     onConnectionLost: function() { // optional cleanup }
 *   });
 */
const ConnectionManager = {
  // Configuration
  maxRetries: 5,
  baseDelay: 1000, // Start with 1 second
  maxDelay: 30000, // Cap at 30 seconds

  // State
  retryCount: 0,
  retryTimeout: null,
  isConnected: false,
  // True once the current attempt has been counted as failed, so the second
  // event of an error+close pair is ignored.
  attemptFailed: false,
  modalElement: null,

  // User callbacks
  onReady() {},
  onDisconnect() {},
  onConnectionLost() {},

  /**
   * Initialize the connection manager
   * @param {Object} options - Configuration options
   * @param {Function} options.onReady - Called when connection is established
   * @param {Function} [options.onDisconnect] - Called once per failed or dropped
   *   connection with { attempt, delay } (delay is null when giving up)
   * @param {Function} options.onConnectionLost - Called when connection is permanently lost
   * @param {number} options.maxRetries - Max reconnection attempts (default: 5)
   * @param {number} options.baseDelay - Initial delay for reconnection attempts (default: 1000 ms)
   * @param {number} options.maxDelay - Maximum delay for reconnection attempts (default: 30000 ms)
   */
  init(options) {
    if (options.onReady) this.onReady = options.onReady;
    if (options.onDisconnect) this.onDisconnect = options.onDisconnect;
    if (options.onConnectionLost) this.onConnectionLost = options.onConnectionLost;
    if (options.maxRetries !== undefined) this.maxRetries = options.maxRetries;
    if (options.baseDelay !== undefined) this.baseDelay = options.baseDelay;
    if (options.maxDelay !== undefined) this.maxDelay = options.maxDelay;

    this.createModal();
    this.connect();
  },

  /**
   * Attempt to connect to Captivate
   */
  connect() {
    this.attemptFailed = false;

    // Install the handlers before init() runs, so a failure it reports
    // straight away is not delivered to a stale or default handler.
    ServiceHandler.onready = () => {
      // A retry may still be pending if the link came up by another route
      // (e.g. WebRTC) after a failure; the link is live, so cancel it.
      if (this.retryTimeout) {
        clearTimeout(this.retryTimeout);
        this.retryTimeout = null;
      }
      this.isConnected = true;
      this.attemptFailed = false;
      this.retryCount = 0;
      this.hideModal();
      console.log('ConnectionManager: Connected to Captivate');
      this.onReady();
    };

    ServiceHandler.onclose = () => {
      this.handleDisconnect();
    };

    ServiceHandler.onerror = (e) => {
      console.error('ConnectionManager: WebSocket error', e);
      this.handleDisconnect();
    };

    ServiceHandler.init();
  },

  /**
   * Handle disconnection - attempt reconnect or show modal
   */
  handleDisconnect() {
    if (this.attemptFailed) return; // second event of the same failure
    this.attemptFailed = true;

    if (!this.isConnected && this.retryCount === 0) {
      // Initial connection failed
      console.warn('ConnectionManager: Initial connection failed');
    } else {
      console.warn('ConnectionManager: Disconnected from Captivate');
    }

    this.isConnected = false;

    if (this.retryCount < this.maxRetries) {
      const delay = this.scheduleReconnect();
      this.onDisconnect({ attempt: this.retryCount, delay: delay });
    } else {
      this.onDisconnect({ attempt: this.retryCount, delay: null });
      this.showModal();
      this.onConnectionLost();
    }
  },

  /**
   * Schedule a reconnection attempt with exponential backoff
   * @returns {number} the delay in ms before the attempt
   */
  scheduleReconnect() {
    // Clear any existing timeout
    if (this.retryTimeout) {
      clearTimeout(this.retryTimeout);
    }

    // Calculate delay with exponential backoff
    const delay = Math.min(this.baseDelay * Math.pow(2, this.retryCount), this.maxDelay);

    this.retryCount++;
    console.log(
      'ConnectionManager: Reconnecting in ' + delay + 'ms (attempt ' + this.retryCount + '/' + this.maxRetries + ')'
    );

    this.retryTimeout = setTimeout(() => {
      this.retryTimeout = null;
      this.connect();
    }, delay);
    return delay;
  },

  /**
   * Create the connection lost modal (injected into DOM)
   */
  createModal() {
    // Check if modal already exists
    if (document.getElementById('connection-lost-modal')) {
      this.modalElement = document.getElementById('connection-lost-modal');
      return;
    }

    // Create modal HTML
    const modal = document.createElement('div');
    modal.id = 'connection-lost-modal';
    modal.innerHTML = `
      <div class="connection-modal-backdrop">
        <div class="connection-modal-content">
          <div class="connection-modal-icon">⚠️</div>
          <h2 class="connection-modal-title">Connection Lost</h2>
          <p class="connection-modal-message">
            Unable to connect to Captivate after multiple attempts.
            <br>Please check that Captivate is running and try reloading.
          </p>
          <button class="connection-modal-btn" onclick="ConnectionManager.retry()">
            🔄 Retry Connection
          </button>
        </div>
      </div>
    `;

    // Create styles
    const style = document.createElement('style');
    style.textContent = `
      #connection-lost-modal {
        display: none;
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        z-index: 10000;
      }

      #connection-lost-modal.visible {
        display: block;
      }

      .connection-modal-backdrop {
        width: 100%;
        height: 100%;
        background: rgba(0, 0, 0, 0.85);
        display: flex;
        align-items: center;
        justify-content: center;
        animation: fadeIn 0.3s ease;
      }

      @keyframes fadeIn {
        from { opacity: 0; }
        to { opacity: 1; }
      }

      .connection-modal-content {
        background: #2a2a2a;
        border: 1px solid #4d4d4d;
        border-radius: 12px;
        padding: 32px 40px;
        text-align: center;
        max-width: 360px;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
        animation: slideIn 0.3s ease;
      }

      @keyframes slideIn {
        from {
          opacity: 0;
          transform: translateY(-20px);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }

      .connection-modal-icon {
        font-size: 48px;
        margin-bottom: 16px;
      }

      .connection-modal-title {
        color: #e53935;
        font-size: 20px;
        font-weight: 600;
        margin: 0 0 12px 0;
        font-family: Arial, sans-serif;
      }

      .connection-modal-message {
        color: #aaaaaa;
        font-size: 13px;
        line-height: 1.5;
        margin: 0 0 24px 0;
        font-family: Arial, sans-serif;
      }

      .connection-modal-btn {
        background: #4586e0;
        border: none;
        color: white;
        padding: 12px 24px;
        border-radius: 6px;
        font-size: 14px;
        font-weight: 600;
        cursor: pointer;
        font-family: Arial, sans-serif;
        transition: background 0.15s;
      }

      .connection-modal-btn:hover {
        background: #5a9aef;
      }

      .connection-modal-btn:active {
        background: #3a76d0;
      }
    `;

    document.head.appendChild(style);
    document.body.appendChild(modal);
    this.modalElement = modal;
  },

  /**
   * Show the connection lost modal
   */
  showModal: function () {
    if (this.modalElement) {
      this.modalElement.classList.add('visible');
    }
  },

  /**
   * Hide the connection lost modal
   */
  hideModal: function () {
    if (this.modalElement) {
      this.modalElement.classList.remove('visible');
    }
  },

  /**
   * Retry connection (called from modal button)
   */
  retry() {
    this.retryCount = 0;
    this.hideModal();
    this.connect();
  },
};
