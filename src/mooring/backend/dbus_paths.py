"""Every KDE Connect D-Bus name in one place.

Verified against kdeconnectd 26.08.3 (Fedora 44) on 2026-11-18; see
docs/dbus-introspection.txt. "verified" = seen in live introspection; "binary" =
present in the plugin's Qt meta-object strings but not yet called on a live device.
"""

SERVICE = "org.kde.kdeconnect"
BASE_PATH = "/modules/kdeconnect"  # verified. (/org/kde/kdeconnect/daemon is only KDBusService)

# Daemon (verified). Note: Qt exports C++ overloads as duplicate D-Bus methods,
# which makes `busctl introspect` refuse; call with an explicit signature.
DAEMON_PATH = BASE_PATH
DAEMON_IFACE = "org.kde.kdeconnect.daemon"
#   devices(bb) -> as            onlyReachable, onlyPaired
#   deviceNames(bb) -> a{ss}
#   signals: deviceAdded(s), deviceRemoved(s), deviceVisibilityChanged(sb), deviceListChanged()
#   properties: pairingRequests (as), customDevices (as)

# Device: /modules/kdeconnect/devices/<id>  (verified live)
DEVICE_IFACE = "org.kde.kdeconnect.device"
#   properties: name, type, isReachable, isPaired, pairState, isPairRequestedByPeer, ...
#     (isTrusted does NOT exist in 26.08)
#   methods: requestPairing(), unpair(), acceptPairing(), cancelPairing(), hasPlugin(s)
#   signals: reachableChanged(b), pairStateChanged(i), nameChanged(s), typeChanged(s),
#            pairingFailed(s), pluginsChanged()
DEVICES_NAMESPACE = f"{BASE_PATH}/devices"


class PairState:
    NOT_PAIRED = 0
    REQUESTED = 1
    REQUESTED_BY_PEER = 2
    PAIRED = 3


# Conversations live on the device path itself (verified live). Struct layout: docs/dbus-introspection.txt
CONVERSATIONS_IFACE = "org.kde.kdeconnect.device.conversations"
#   activeConversations() -> av of ConversationMessage structs
#   requestConversation(x, i start, i end), requestAllConversationThreads(),
#   replyToConversation(x, s, av), sendWithoutConversation(av, s, av)
#   signals: conversationCreated(v), conversationUpdated(v), conversationLoaded(x, t count),
#            conversationRemoved(x), attachmentReceived(s, s)
#   A thread load = N x conversationUpdated, then conversationLoaded as end marker.

# Plugins live under the device path: /modules/kdeconnect/devices/<id>/<suffix>   (all: binary)
SMS_SUFFIX = "sms"
SMS_IFACE = "org.kde.kdeconnect.device.sms"
#   (verified live) requestAllConversations(), requestConversation(x[, x ts[, x n]]),
#   sendSms(av, s, av[, x]), getAttachment, requestAttachment, launchApp
NOTIFICATIONS_SUFFIX = "notifications"
NOTIFICATIONS_IFACE = "org.kde.kdeconnect.device.notifications"
#   activeNotifications(); signals notificationPosted/Removed/Updated, allNotificationsRemoved
NOTIFICATION_IFACE = "org.kde.kdeconnect.device.notifications.notification"
#   appName, title, text, ticker, dismissable, replyId, silent, iconPath, actions
#   dismiss(), reply(), sendReply(replyId, message), sendAction(action)
SFTP_SUFFIX = "sftp"
SFTP_IFACE = "org.kde.kdeconnect.device.sftp"
#   mountAndWait(), mountPoint(), isMounted(), getDirectories(), unmount(), getMountError()
SHARE_SUFFIX = "share"
SHARE_IFACE = "org.kde.kdeconnect.device.share"
#   shareUrl(s), shareUrls(as), shareText(s), openFile(s)
CLIPBOARD_SUFFIX = "clipboard"
CLIPBOARD_IFACE = "org.kde.kdeconnect.device.clipboard"
#   sendClipboard(), isAutoShareDisabled
BATTERY_SUFFIX = "battery"
BATTERY_IFACE = "org.kde.kdeconnect.device.battery"
#   charge (i), isCharging (b), hasBattery (b); signal refreshed(b isCharging, i charge)  (live)
FINDMYPHONE_SUFFIX = "findmyphone"
FINDMYPHONE_IFACE = "org.kde.kdeconnect.device.findmyphone"
#   ring()


def device_path(device_id: str) -> str:
    return f"{BASE_PATH}/devices/{device_id}"


def plugin_path(device_id: str, suffix: str) -> str:
    return f"{device_path(device_id)}/{suffix}"


def device_id_from_path(path: str) -> str | None:
    """'/modules/kdeconnect/devices/<id>[/plugin]' -> '<id>'."""
    prefix = DEVICES_NAMESPACE + "/"
    if not path.startswith(prefix):
        return None
    return path[len(prefix) :].split("/", 1)[0] or None
