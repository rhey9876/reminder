/**
 * Reminder PWA - Frontend Application
 */

const API_BASE = '';
const REFRESH_INTERVAL = 60000; // 60 seconds

let refreshTimer = null;
let lastNotifiedKey = '';  // Track last notification to avoid spam

/**
 * Initialize the application
 */
async function init() {
    // Check authentication first
    if (!await checkAuth()) {
        return;
    }

    updateCurrentDate();
    checkNotificationPermission();
    await fetchStatus();
    startAutoRefresh();
    registerServiceWorker();
}

/**
 * Check if user is authenticated
 */
async function checkAuth() {
    try {
        const response = await fetch('/api/auth/check');
        const data = await response.json();

        if (!data.auth_enabled) {
            return true; // Auth disabled, proceed
        }

        if (!data.authenticated) {
            window.location.href = '/login.html';
            return false;
        }

        return true;
    } catch (error) {
        console.error('Auth check failed:', error);
        showError('Verbindung zum Server fehlgeschlagen. Bitte neu laden.');
        hideLoading();
        return false; // Don't proceed on error
    }
}

/**
 * Update the current date display
 */
function updateCurrentDate() {
    const now = new Date();
    const options = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
    document.getElementById('currentDate').textContent = now.toLocaleDateString('de-DE', options);
}

/**
 * Check if notifications are supported and show banner if needed
 */
function checkNotificationPermission() {
    if (!('Notification' in window)) {
        return;
    }

    if (Notification.permission === 'default') {
        document.getElementById('notificationBanner').classList.remove('hidden');
    }
}

/**
 * Request notification permission
 */
async function requestNotificationPermission() {
    if (!('Notification' in window)) {
        alert('Benachrichtigungen werden von diesem Browser nicht unterstützt.');
        return;
    }

    const permission = await Notification.requestPermission();

    if (permission === 'granted') {
        document.getElementById('notificationBanner').classList.add('hidden');
        showNotification('Aktiviert', 'Erinnerungen sind jetzt aktiv.');
    } else {
        document.getElementById('notificationBanner').classList.add('hidden');
    }
}

/**
 * Show a notification
 */
function showNotification(title, body) {
    if (Notification.permission === 'granted') {
        const notification = new Notification(title, {
            body: body,
            icon: 'icon-192.png',
            badge: 'icon-192.png',
            tag: 'reminder',
            requireInteraction: true
        });

        notification.onclick = () => {
            window.focus();
            notification.close();
        };
    }
}

/**
 * Update badge count on app icon
 */
async function updateBadge(count) {
    // Update document title with count
    if (count > 0) {
        document.title = `(${count}) Erinnerung`;
    } else {
        document.title = 'Erinnerung';
    }

    // Use Badge API if available
    if ('setAppBadge' in navigator) {
        try {
            if (count > 0) {
                await navigator.setAppBadge(count);
            } else {
                await navigator.clearAppBadge();
            }
        } catch (error) {
            console.log('Badge API error:', error);
        }
    }
}

/**
 * Fetch medication status from API
 */
async function fetchStatus() {
    try {
        const response = await fetch(`${API_BASE}/api/status`);

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();
        hideError();
        renderMedications(data);
        updateLastUpdate();

        // Update badge with overdue + due count
        const urgentCount = (data.overdue?.length || 0) + (data.due?.length || 0);
        updateBadge(urgentCount);

        // Show notification for urgent items
        checkAndNotify(data);
    } catch (error) {
        console.error('Error fetching status:', error);
        hideLoading();
        showError('Verbindungsfehler. Versuche erneut...');
    }
}

/**
 * Check for urgent items and show notification
 */
function checkAndNotify(data) {
    const urgent = [...(data.overdue || []), ...(data.due || [])];

    if (urgent.length === 0) {
        lastNotifiedKey = '';
        return;
    }

    // Create key from current urgent items
    const currentKey = urgent.map(m => `${m.medication}-${m.time}`).join('|');

    // Only notify if items changed (avoid spam)
    if (currentKey === lastNotifiedKey) {
        return;
    }

    lastNotifiedKey = currentKey;

    // Show notification
    const item = urgent[0];
    const title = '⏰ Erinnerung';
    const body = urgent.length > 1
        ? `${item.medication} + ${urgent.length - 1} weitere`
        : `${item.medication} - ${item.time}`;

    showNotification(title, body);
}

/**
 * Confirm medication intake
 */
async function confirmIntake(medication, time, buttonElement) {
    buttonElement.disabled = true;
    buttonElement.textContent = '...';

    try {
        const response = await fetch(`${API_BASE}/api/confirm`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ medication, time })
        });

        const data = await response.json();

        if (response.ok && data.success) {
            buttonElement.textContent = '✓';
            buttonElement.classList.add('success');

            // Refresh after short delay
            setTimeout(() => {
                fetchStatus();
            }, 500);
        } else if (data.duplicate) {
            buttonElement.textContent = 'Bereits erledigt';
            setTimeout(() => fetchStatus(), 1000);
        } else {
            throw new Error(data.error || 'Unbekannter Fehler');
        }
    } catch (error) {
        console.error('Error confirming intake:', error);
        buttonElement.disabled = false;
        buttonElement.textContent = '✓ Erledigt';
        showError('Fehler beim Speichern. Bitte erneut versuchen.');
    }
}

/**
 * Snooze medication for 5 minutes
 */
async function snoozeIntake(medication, time, buttonElement) {
    buttonElement.disabled = true;
    buttonElement.textContent = '...';

    try {
        const response = await fetch(`${API_BASE}/api/snooze`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ medication, time })
        });

        const data = await response.json();

        if (response.ok && data.success) {
            buttonElement.textContent = '⏰ 5min';
            buttonElement.classList.add('snoozed');

            // Refresh after short delay
            setTimeout(() => {
                fetchStatus();
            }, 500);
        } else {
            throw new Error(data.error || 'Unbekannter Fehler');
        }
    } catch (error) {
        console.error('Error snoozing intake:', error);
        buttonElement.disabled = false;
        buttonElement.textContent = '⏰ +5min';
        showError('Fehler beim Snooze. Bitte erneut versuchen.');
    }
}

/**
 * Render medications to the DOM
 */
function renderMedications(data) {
    const loading = document.getElementById('loading');
    const content = document.getElementById('content');
    const emptyState = document.getElementById('emptyState');

    loading.classList.add('hidden');
    content.classList.remove('hidden');

    // Render overdue
    const overdueSection = document.getElementById('overdueSection');
    const overdueList = document.getElementById('overdueList');
    const overdueCount = document.getElementById('overdueCount');

    if (data.overdue && data.overdue.length > 0) {
        overdueSection.classList.remove('hidden');
        overdueCount.textContent = data.overdue.length;
        overdueList.innerHTML = data.overdue.map(med => createMedicationCard(med, 'overdue')).join('');
    } else {
        overdueSection.classList.add('hidden');
    }

    // Render due
    const dueSection = document.getElementById('dueSection');
    const dueList = document.getElementById('dueList');
    const dueCount = document.getElementById('dueCount');

    if (data.due && data.due.length > 0) {
        dueSection.classList.remove('hidden');
        dueCount.textContent = data.due.length;
        dueList.innerHTML = data.due.map(med => createMedicationCard(med, 'due')).join('');
    } else {
        dueSection.classList.add('hidden');
    }

    // Render upcoming
    const upcomingSection = document.getElementById('upcomingSection');
    const upcomingList = document.getElementById('upcomingList');
    const upcomingCount = document.getElementById('upcomingCount');

    if (data.upcoming && data.upcoming.length > 0) {
        upcomingSection.classList.remove('hidden');
        upcomingCount.textContent = data.upcoming.length;
        upcomingList.innerHTML = data.upcoming.map(med => createMedicationCard(med, 'upcoming')).join('');
    } else {
        upcomingSection.classList.add('hidden');
    }

    // Show empty state if all sections are empty
    const hasItems = (data.overdue?.length > 0) || (data.due?.length > 0) || (data.upcoming?.length > 0);
    if (!hasItems) {
        emptyState.classList.remove('hidden');
    } else {
        emptyState.classList.add('hidden');
    }
}

/**
 * Create a medication card HTML
 */
function createMedicationCard(med, status) {
    const statusLabels = {
        overdue: 'Überfällig',
        due: 'Jetzt',
        upcoming: 'Später'
    };

    let timeInfo = '';
    if (status === 'overdue' || (status === 'due' && med.minutes_late)) {
        const minutes = med.minutes_late || 0;
        if (minutes >= 60) {
            const hours = Math.floor(minutes / 60);
            const mins = minutes % 60;
            timeInfo = `+${hours}h ${mins}min`;
        } else {
            timeInfo = `+${minutes} min`;
        }
    } else if (med.minutes_until) {
        const minutes = med.minutes_until;
        if (minutes >= 60) {
            const hours = Math.floor(minutes / 60);
            const mins = minutes % 60;
            timeInfo = `in ${hours}h ${mins}min`;
        } else {
            timeInfo = `in ${minutes} min`;
        }
    }

    const showButton = status === 'overdue' || status === 'due';
    const escapedMed = escapeHtml(med.medication);
    const escapedTime = escapeHtml(med.time);

    return `
        <div class="card">
            <div class="card-info">
                <div class="card-name">${escapedMed}</div>
                <div class="card-time">🕐 ${escapedTime} ${timeInfo ? `• ${timeInfo}` : ''}</div>
                <span class="card-status status-${status}">${statusLabels[status]}</span>
            </div>
            ${showButton ? `
                <div class="card-buttons">
                    <button class="btn-snooze" onclick="snoozeIntake('${escapedMed}', '${escapedTime}', this)">
                        ⏰ +5min
                    </button>
                    <button class="btn-confirm" onclick="confirmIntake('${escapedMed}', '${escapedTime}', this)">
                        ✓ Erledigt
                    </button>
                </div>
            ` : ''}
        </div>
    `;
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Update last update timestamp
 */
function updateLastUpdate() {
    const now = new Date();
    const timeStr = now.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
    document.getElementById('lastUpdate').textContent = `Aktualisiert: ${timeStr}`;
}

/**
 * Show error message
 */
function showError(message) {
    const banner = document.getElementById('errorBanner');
    banner.textContent = message;
    banner.classList.remove('hidden');
}

/**
 * Hide error message
 */
function hideError() {
    document.getElementById('errorBanner').classList.add('hidden');
}

/**
 * Hide loading spinner
 */
function hideLoading() {
    document.getElementById('loading').classList.add('hidden');
}

/**
 * Start auto-refresh timer
 */
function startAutoRefresh() {
    if (refreshTimer) {
        clearInterval(refreshTimer);
    }
    refreshTimer = setInterval(fetchStatus, REFRESH_INTERVAL);
}

/**
 * Register service worker
 */
async function registerServiceWorker() {
    if ('serviceWorker' in navigator) {
        try {
            const registration = await navigator.serviceWorker.register('service-worker.js');
            console.log('ServiceWorker registered:', registration.scope);
        } catch (error) {
            console.log('ServiceWorker registration failed:', error);
        }
    }
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', init);

// Refresh when page becomes visible again
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        fetchStatus();
    }
});
