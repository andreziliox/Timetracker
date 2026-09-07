const IDLE_DELAY_MS = 5000;
const STATUS_POLL_MS = 700;

let currentEmployeeId = null;
let currentState = 'idle';
let resetTimer = null;
let statusRequestInProgress = false;
let bookingInProgress = false;
let lastHandledStatusAt = '';

function $(id) {
  return document.getElementById(id);
}

function updateClock() {
  const clock = $('clock');
  if (!clock) return;

  clock.textContent = new Date().toLocaleTimeString('de-DE', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  });
}

function setVisible(id, visible) {
  const element = $(id);
  if (element) element.classList.toggle('hidden', !visible);
}

function setMessage(text = '', type = '') {
  const element = $('message');
  if (!element) return;

  element.textContent = text;
  element.className = `message ${type}`.trim();
}

function setButtonsDisabled(disabled) {
  document.querySelectorAll('[data-action]').forEach(button => {
    button.disabled = disabled;
  });
}

async function resetKioskState() {
  try {
    await fetch('/api/kiosk/reset', {
      method: 'POST',
      cache: 'no-store'
    });
  } catch {
    // Der Bildschirm wird trotzdem lokal zurückgesetzt.
  }
}

function showIdle() {
  currentEmployeeId = null;
  currentState = 'idle';
  bookingInProgress = false;
  clearTimeout(resetTimer);

  setVisible('welcome-panel', true);
  setVisible('employee-panel', false);
  setVisible('day-panel', false);
  setMessage('');
  setButtonsDisabled(false);

  const title = $('status-title');
  const text = $('status-text');
  if (title) title.textContent = 'Bitte Chip vorhalten';
  if (text) text.textContent = 'Halte deinen NFC-Chip kurz vor den Leser.';
}

function showEmployee(employee) {
  if (!employee || !employee.employee_id) return;

  clearTimeout(resetTimer);
  currentEmployeeId = Number(employee.employee_id);
  currentState = 'employee_detected';
  bookingInProgress = false;

  setVisible('welcome-panel', false);
  setVisible('employee-panel', true);
  setVisible('day-panel', true);
  setMessage('');
  setButtonsDisabled(false);

  const greeting = $('greeting');
  if (greeting) greeting.textContent = `Hallo ${employee.employee_name}`;

  loadTodayLog(currentEmployeeId);
  scheduleReset();
}

function scheduleReset() {
  clearTimeout(resetTimer);
  resetTimer = setTimeout(async () => {
    showIdle();
    await resetKioskState();
  }, IDLE_DELAY_MS);
}

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--:--';

  return date.toLocaleTimeString('de-DE', {
    hour: '2-digit',
    minute: '2-digit'
  });
}

function actionLabel(action) {
  const labels = {
    'Kommen': 'Kommen',
    'Pause Beginn': 'Pause begonnen',
    'Pause Ende': 'Pause beendet',
    'Gehen': 'Gehen'
  };

  return labels[action] || action;
}

function calculateWorkMinutes(bookings) {
  let workStart = null;
  let pauseStart = null;
  let workMinutes = 0;

  for (const booking of bookings) {
    const time = new Date(booking.created_at);
    if (Number.isNaN(time.getTime())) continue;

    if (booking.action === 'Kommen') {
      workStart = time;
    } else if (booking.action === 'Pause Beginn' && workStart) {
      workMinutes += (time - workStart) / 60000;
      pauseStart = time;
      workStart = null;
    } else if (booking.action === 'Pause Ende' && pauseStart) {
      pauseStart = null;
      workStart = time;
    } else if (booking.action === 'Gehen' && workStart) {
      workMinutes += (time - workStart) / 60000;
      workStart = null;
    }
  }

  if (workStart) {
    workMinutes += (Date.now() - workStart.getTime()) / 60000;
  }

  const minutes = Math.max(0, Math.round(workMinutes));
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return `${hours}:${String(remainder).padStart(2, '0')} h`;
}

async function loadTodayLog(employeeId) {
  try {
    const response = await fetch(`/api/kiosk/today/${employeeId}`, {
      cache: 'no-store'
    });
    const data = await response.json();

    if (!response.ok) {
      setMessage(data.detail || 'Tageslog konnte nicht geladen werden.', 'error');
      return;
    }

    const list = $('booking-list');
    if (!list) return;

    list.innerHTML = '';
    const bookings = data.bookings || [];

    if (bookings.length === 0) {
      const empty = document.createElement('li');
      empty.className = 'empty-log';
      empty.textContent = 'Heute noch keine Buchungen.';
      list.appendChild(empty);
    } else {
      bookings.forEach(booking => {
        const item = document.createElement('li');
        item.className = 'booking-item';

        const time = document.createElement('time');
        time.textContent = formatTime(booking.created_at);

        const label = document.createElement('span');
        label.textContent = actionLabel(booking.action);

        item.append(time, label);
        list.appendChild(item);
      });

      list.scrollTop = list.scrollHeight;
    }

    const total = $('day-total');
    if (total) total.textContent = calculateWorkMinutes(bookings);
  } catch {
    setMessage('Backend ist nicht erreichbar.', 'error');
  }
}

async function pollStatus() {
  if (statusRequestInProgress || bookingInProgress) return;
  statusRequestInProgress = true;

  try {
    const response = await fetch('/api/kiosk/status', {
      cache: 'no-store'
    });
    const status = await response.json();

    if (!response.ok) return;

    if (status.state === 'employee_detected' && status.employee_id) {
      const isNewScan = status.updated_at !== lastHandledStatusAt;
      if (isNewScan) {
        lastHandledStatusAt = status.updated_at;
        showEmployee(status);
      }
    } else if (status.state === 'unknown') {
      const isNewScan = status.updated_at !== lastHandledStatusAt;
      if (isNewScan) {
        lastHandledStatusAt = status.updated_at;
        showIdle();
        setMessage('Dieser NFC-Chip ist nicht registriert.', 'error');
        scheduleReset();
      }
    } else if (status.state === 'idle') {
      lastHandledStatusAt = '';
    }
  } catch {
    setMessage('Backend ist nicht erreichbar.', 'error');
  } finally {
    statusRequestInProgress = false;
  }
}

async function book(action) {
  if (!currentEmployeeId || bookingInProgress) {
    return;
  }

  bookingInProgress = true;
  setButtonsDisabled(true);
  clearTimeout(resetTimer);
  setMessage('Buchung wird gespeichert...');

  try {
    const response = await fetch('/api/kiosk/book', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        employee_id: currentEmployeeId,
        action
      })
    });

    const data = await response.json();

    if (!response.ok) {
      setMessage(data.detail || 'Buchung konnte nicht gespeichert werden.', 'error');
      bookingInProgress = false;
      setButtonsDisabled(false);
      scheduleReset();
      return;
    }

    setMessage(`${actionLabel(data.action)} wurde gespeichert.`, 'success');
    await loadTodayLog(currentEmployeeId);
    await resetKioskState();
    bookingInProgress = false;
    scheduleReset();
  } catch {
    setMessage('Backend ist nicht erreichbar.', 'error');
    bookingInProgress = false;
    setButtonsDisabled(false);
    scheduleReset();
  }
}

document.addEventListener('DOMContentLoaded', () => {
  updateClock();
  setInterval(updateClock, 1000);

  showIdle();
  pollStatus();
  setInterval(pollStatus, STATUS_POLL_MS);

  document.querySelectorAll('[data-action]').forEach(button => {
    button.addEventListener('click', () => book(button.dataset.action));
  });
});
