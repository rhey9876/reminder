/**
 * Reminder PWA - Service Worker
 */

const CACHE_NAME = 'reminder-v7';
const VERSION_CACHE_KEY = 'app-version';
const STATIC_ASSETS = [
    '/',
    '/index.html',
    '/app.js',
    '/manifest.json',
    '/icon-192.png',
    '/icon-512.png',
    '/favicon.ico'
];

const CHECK_INTERVAL = 5 * 60 * 1000; // 5 minutes
let cachedVersion = null;

/**
 * Install event - cache static assets
 */
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => {
                console.log('Caching static assets');
                return cache.addAll(STATIC_ASSETS);
            })
            .then(() => self.skipWaiting())
    );
});

/**
 * Activate event - clean up old caches and check version
 */
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((cacheNames) => {
                return Promise.all(
                    cacheNames
                        .filter((name) => name !== CACHE_NAME)
                        .map((name) => caches.delete(name))
                );
            })
            .then(() => fetchAndStoreVersion())
            .then(() => self.clients.claim())
    );
});

/**
 * Fetch version from server and store it
 */
async function fetchAndStoreVersion() {
    try {
        const response = await fetch('/api/version');
        if (response.ok) {
            const data = await response.json();
            cachedVersion = data.version;
            console.log('Stored app version:', cachedVersion);
        }
    } catch (error) {
        console.log('Failed to fetch version:', error);
    }
}

/**
 * Check if version has changed and trigger update if needed
 */
async function checkVersionAndUpdate() {
    try {
        const response = await fetch('/api/version');
        if (!response.ok) return;

        const data = await response.json();
        const serverVersion = data.version;

        if (cachedVersion && cachedVersion !== serverVersion) {
            console.log(`Version changed: ${cachedVersion} -> ${serverVersion}`);
            // Clear all caches
            const cacheNames = await caches.keys();
            await Promise.all(cacheNames.map(name => caches.delete(name)));
            // Update service worker
            await self.registration.update();
            // Notify clients to reload
            const clients = await self.clients.matchAll();
            clients.forEach(client => {
                client.postMessage({ type: 'VERSION_CHANGED', version: serverVersion });
            });
        }

        cachedVersion = serverVersion;
    } catch (error) {
        console.log('Version check failed:', error);
    }
}

/**
 * Fetch event - serve from cache, fallback to network
 */
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // Don't cache API requests
    if (url.pathname.startsWith('/api/')) {
        event.respondWith(fetch(event.request));
        return;
    }

    event.respondWith(
        caches.match(event.request)
            .then((cachedResponse) => {
                if (cachedResponse) {
                    // Return cached version but fetch update in background
                    fetch(event.request)
                        .then((response) => {
                            if (response.ok) {
                                caches.open(CACHE_NAME)
                                    .then((cache) => cache.put(event.request, response));
                            }
                        })
                        .catch(() => {});
                    return cachedResponse;
                }

                return fetch(event.request)
                    .then((response) => {
                        if (response.ok) {
                            const responseClone = response.clone();
                            caches.open(CACHE_NAME)
                                .then((cache) => cache.put(event.request, responseClone));
                        }
                        return response;
                    });
            })
    );
});

/**
 * Background sync for checks
 */
async function checkStatus() {
    try {
        // Check authentication first
        const authResponse = await fetch('/api/auth/check');
        if (!authResponse.ok) return;

        const authData = await authResponse.json();
        if (authData.auth_enabled && !authData.authenticated) {
            // Not authenticated, skip status check
            return;
        }

        // Check for version changes
        await checkVersionAndUpdate();

        const response = await fetch('/api/status');
        if (!response.ok) return;

        const data = await response.json();

        // Check for overdue or due items
        const urgent = [...(data.overdue || []), ...(data.due || [])];

        // Update badge
        if ('setAppBadge' in navigator) {
            if (urgent.length > 0) {
                await navigator.setAppBadge(urgent.length);
            } else {
                await navigator.clearAppBadge();
            }
        }

        if (urgent.length > 0) {
            const item = urgent[0];
            await showReminderNotification(item, urgent.length);
        }
    } catch (error) {
        console.log('Background check failed:', error);
    }
}

/**
 * Show notification
 */
async function showReminderNotification(item, totalCount) {
    await self.registration.showNotification(
        '⏰ Erinnerung',
        {
            body: totalCount > 1
                ? `${item.medication} + ${totalCount - 1} weitere`
                : `${item.medication} - ${item.time}`,
            icon: 'icon-192.png',
            badge: 'icon-192.png',
            tag: 'mrem',
            renotify: false,
            requireInteraction: true,
            actions: [
                { action: 'open', title: 'Öffnen' },
                { action: 'dismiss', title: 'Später' }
            ]
        }
    );
}

/**
 * Handle notification click
 */
self.addEventListener('notificationclick', (event) => {
    event.notification.close();

    if (event.action === 'dismiss') {
        return;
    }

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then((clientList) => {
                // Focus existing window if available
                for (const client of clientList) {
                    if (client.url.includes(self.location.origin) && 'focus' in client) {
                        return client.focus();
                    }
                }
                // Open new window
                if (clients.openWindow) {
                    return clients.openWindow('/');
                }
            })
    );
});

/**
 * Periodic background sync (if supported)
 */
self.addEventListener('periodicsync', (event) => {
    if (event.tag === 'reminder-check') {
        event.waitUntil(checkStatus());
    }
});

/**
 * Message handler for manual checks
 */
self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'CHECK_STATUS') {
        checkStatus();
    }
});

/**
 * Set up periodic checks using setInterval as fallback
 */
let checkIntervalId = null;

function startPeriodicChecks() {
    if (checkIntervalId) {
        clearInterval(checkIntervalId);
    }
    checkIntervalId = setInterval(checkStatus, CHECK_INTERVAL);
    // Initial check
    checkStatus();
}

// Start periodic checks when service worker activates
self.addEventListener('activate', () => {
    startPeriodicChecks();
});
