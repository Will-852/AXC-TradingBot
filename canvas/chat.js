/* ── AI Chat Widget ─────────────────────────────────────────────── */
(function () {
  'use strict';

  var fab = document.getElementById('chat-fab');
  var panel = document.getElementById('chat-panel');
  var closeBtn = document.getElementById('chat-close');
  var input = document.getElementById('chat-input');
  var sendBtn = document.getElementById('chat-send');
  var messagesEl = document.getElementById('chat-messages');
  var modelBadge = document.getElementById('chat-model-badge');
  var isOpen = false;
  var isSending = false;

  // ── Toggle ──
  function toggle() {
    isOpen = !isOpen;
    panel.style.display = isOpen ? 'flex' : 'none';
    fab.classList.toggle('chat-fab-hidden', isOpen);
    if (isOpen) input.focus();
  }

  fab.addEventListener('click', toggle);
  closeBtn.addEventListener('click', toggle);

  // Ctrl+/ shortcut
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === '/') {
      e.preventDefault();
      toggle();
    }
    // Escape to close
    if (e.key === 'Escape' && isOpen) {
      toggle();
    }
  });

  // ── Render message ──
  function addMessage(text, type) {
    var div = document.createElement('div');
    div.className = 'chat-msg chat-msg-' + type;

    if (type === 'ai' && typeof marked !== 'undefined') {
      div.innerHTML = marked.parse(text);
    } else if (type === 'error') {
      div.textContent = text;
    } else {
      div.textContent = text;
    }
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
  }

  function showTyping() {
    var div = document.createElement('div');
    div.className = 'chat-typing';
    div.id = 'chat-typing';
    div.innerHTML = '<span>思考中...</span>';
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function hideTyping() {
    var el = document.getElementById('chat-typing');
    if (el) el.remove();
  }

  // ── Send ──
  function send() {
    var msg = input.value.trim();
    if (!msg || isSending) return;

    addMessage(msg, 'user');
    input.value = '';
    isSending = true;
    sendBtn.disabled = true;
    showTyping();

    fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        hideTyping();
        if (data.error) {
          addMessage(data.error, 'error');
        } else {
          addMessage(data.reply, 'ai');
          if (modelBadge && data.model) {
            modelBadge.textContent = data.model;
          }
        }
      })
      .catch(function (err) {
        hideTyping();
        addMessage('網絡錯誤: ' + err.message, 'error');
      })
      .finally(function () {
        isSending = false;
        sendBtn.disabled = false;
        input.focus();
      });
  }

  sendBtn.addEventListener('click', send);
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  // ── Configure marked.js ──
  if (typeof marked !== 'undefined') {
    marked.setOptions({ breaks: true, gfm: true });
  }
})();
