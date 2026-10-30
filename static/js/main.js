// SmartStudy.ai - Main JavaScript

// ─── Flash Messages ──────────────────────────────────────
function initFlashMessages() {
  const msgs = document.querySelectorAll('.flash-msg');
  msgs.forEach((msg, i) => {
    // Auto-dismiss after 4s
    setTimeout(() => dismissFlash(msg), 4000 + i * 300);
    const closeBtn = msg.querySelector('.flash-close');
    if (closeBtn) closeBtn.addEventListener('click', () => dismissFlash(msg));
  });
}

function dismissFlash(el) {
  if (!el || el.classList.contains('hide')) return;
  el.classList.add('hide');
  setTimeout(() => el.remove(), 300);
}

function showFlash(message, type = 'info') {
  const container = document.querySelector('.flash-container') || createFlashContainer();
  const el = document.createElement('div');
  el.className = `flash-msg ${type}`;
  const icons = { success: '✓', error: '✕', info: 'ℹ', warning: '⚠' };
  el.innerHTML = `<span>${icons[type] || 'ℹ'}</span><span>${message}</span><button class="flash-close" aria-label="Close">✕</button>`;
  container.appendChild(el);
  el.querySelector('.flash-close').addEventListener('click', () => dismissFlash(el));
  setTimeout(() => dismissFlash(el), 4500);
}

function createFlashContainer() {
  const div = document.createElement('div');
  div.className = 'flash-container';
  document.body.appendChild(div);
  return div;
}

// ─── Sidebar ─────────────────────────────────────────────
function initSidebar() {
  const mobileBtn = document.getElementById('mobile-menu-btn');
  const sidebar = document.querySelector('.sidebar');
  const overlay = document.querySelector('.sidebar-overlay');

  if (!mobileBtn || !sidebar) return;

  mobileBtn.addEventListener('click', () => {
    sidebar.classList.toggle('open');
    overlay?.classList.toggle('open');
    document.body.style.overflow = sidebar.classList.contains('open') ? 'hidden' : '';
  });

  overlay?.addEventListener('click', () => {
    sidebar.classList.remove('open');
    overlay.classList.remove('open');
    document.body.style.overflow = '';
  });
}

// ─── Modals ───────────────────────────────────────────────
function openModal(id) {
  const el = document.getElementById(id);
  if (el) {
    el.classList.add('open');
    document.body.style.overflow = 'hidden';
    el.addEventListener('click', (e) => { if (e.target === el) closeModal(id); }, { once: true });
  }
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (el) {
    el.classList.remove('open');
    document.body.style.overflow = '';
  }
}

function initModals() {
  document.querySelectorAll('[data-modal-open]').forEach(btn => {
    btn.addEventListener('click', () => openModal(btn.dataset.modalOpen));
  });
  document.querySelectorAll('[data-modal-close]').forEach(btn => {
    btn.addEventListener('click', () => closeModal(btn.dataset.modalClose));
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      document.querySelectorAll('.modal-overlay.open').forEach(m => closeModal(m.id));
    }
  });
}

// ─── Tabs ─────────────────────────────────────────────────
function initTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.tab;
      const container = btn.closest('[data-tabs]') || btn.parentElement.closest('.tabs-container') || document;
      container.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      container.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      const pane = container.querySelector(`[data-pane="${target}"]`) || document.getElementById(target);
      if (pane) pane.classList.add('active');
    });
  });
}

// ─── Confirm Dialogs ─────────────────────────────────────
function confirmAction(message, formOrCallback) {
  if (!confirm(message)) return false;
  if (typeof formOrCallback === 'function') formOrCallback();
  else if (formOrCallback && formOrCallback.submit) formOrCallback.submit();
  return true;
}

function initDeleteConfirm() {
  document.querySelectorAll('[data-confirm]').forEach(el => {
    el.addEventListener('click', function(e) {
      const msg = this.dataset.confirm || 'Are you sure you want to delete this?';
      if (!confirm(msg)) e.preventDefault();
    });
  });
}

// ─── Password Strength ────────────────────────────────────
function initPasswordStrength() {
  const input = document.getElementById('password');
  const fill = document.getElementById('strength-fill');
  const text = document.getElementById('strength-text');
  if (!input || !fill) return;

  input.addEventListener('input', () => {
    const v = input.value;
    let score = 0;
    if (v.length >= 8) score++;
    if (/[A-Z]/.test(v)) score++;
    if (/[a-z]/.test(v)) score++;
    if (/\d/.test(v)) score++;
    if (/[!@#$%^&*(),.?":{}|<>]/.test(v)) score++;

    const levels = [
      { w: '0%', c: '#EF4444', t: '' },
      { w: '20%', c: '#EF4444', t: 'Very Weak' },
      { w: '40%', c: '#F59E0B', t: 'Weak' },
      { w: '60%', c: '#F59E0B', t: 'Fair' },
      { w: '80%', c: '#10B981', t: 'Strong' },
      { w: '100%', c: '#10B981', t: 'Very Strong' },
    ];
    const l = levels[score];
    fill.style.width = l.w;
    fill.style.background = l.c;
    if (text) { text.textContent = l.t; text.style.color = l.c; }
  });
}

// ─── Loading Overlay ──────────────────────────────────────
function showLoading(message = 'Processing...') {
  let overlay = document.getElementById('loading-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'loading-overlay';
    overlay.className = 'loading-overlay';
    overlay.innerHTML = `<div class="spinner"></div><p style="font-weight:600;color:var(--primary)">${message}</p>`;
    document.body.appendChild(overlay);
  } else {
    overlay.querySelector('p').textContent = message;
  }
  overlay.style.display = 'flex';
}

function hideLoading() {
  const overlay = document.getElementById('loading-overlay');
  if (overlay) overlay.style.display = 'none';
}

// ─── API Helper ───────────────────────────────────────────
async function apiCall(url, method = 'GET', data = null) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }
  };
  if (data) opts.body = JSON.stringify(data);
  const res = await fetch(url, opts);
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || 'Request failed');
  return json;
}

// ─── Character Counter ────────────────────────────────────
function initCharCounters() {
  document.querySelectorAll('[data-maxlength]').forEach(el => {
    const max = parseInt(el.dataset.maxlength);
    const counter = document.getElementById(el.dataset.counter);
    if (!counter) return;
    el.addEventListener('input', () => {
      counter.textContent = `${el.value.length}/${max}`;
      counter.style.color = el.value.length > max * 0.9 ? 'var(--error)' : 'var(--text-muted)';
    });
  });
}

// ─── Copy to Clipboard ────────────────────────────────────
function copyText(text) {
  navigator.clipboard.writeText(text).then(() => {
    showFlash('Copied to clipboard!', 'success');
  }).catch(() => {
    showFlash('Could not copy text.', 'error');
  });
}

// ─── Format Dates ─────────────────────────────────────────
function formatRelativeTime(dateStr) {
  const date = new Date(dateStr);
  const now = new Date();
  const diff = Math.floor((now - date) / 1000);
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff/86400)}d ago`;
  return date.toLocaleDateString();
}

// ─── Theme / Active Nav ───────────────────────────────────
function highlightActiveNav() {
  const path = window.location.pathname;
  document.querySelectorAll('.nav-item[href]').forEach(el => {
    const href = el.getAttribute('href');
    if (href && path.startsWith(href) && href !== '/') {
      el.classList.add('active');
    }
  });
}

// ─── Auto-resize Textarea ─────────────────────────────────
function autoResizeTextarea(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 200) + 'px';
}

function initAutoResize() {
  document.querySelectorAll('textarea[data-autoresize]').forEach(ta => {
    ta.addEventListener('input', () => autoResizeTextarea(ta));
    autoResizeTextarea(ta);
  });
}

// ─── Progress Bar Animations ─────────────────────────────
function animateProgressBars() {
  document.querySelectorAll('.progress-bar[data-value]').forEach(bar => {
    const val = bar.dataset.value;
    setTimeout(() => { bar.style.width = val + '%'; }, 100);
  });
}

// ─── Init All ─────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initFlashMessages();
  initSidebar();
  initModals();
  initTabs();
  initDeleteConfirm();
  initPasswordStrength();
  initCharCounters();
  initAutoResize();
  highlightActiveNav();
  animateProgressBars();
});
