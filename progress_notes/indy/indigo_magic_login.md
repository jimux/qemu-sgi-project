# Indigo Magic Login (clogin) Investigation and Fix

## Goal

Get the full SGI Indigo Magic graphical login screen — user picker with icons,
"IRIS" purple branding, "Log In" / "Help" buttons — instead of the bare
"X Window System" xdm login dialog.

## Background: The Two Login Paths

IRIX has two distinct login flows, gated by chkconfig flags:

| Flags | Result |
|-------|--------|
| `xdm on`, `windowsystem on` | X starts, xdm shows built-in login widget |
| + `visuallogin on` | xdm calls `/var/X11/xdm/Xlogin`, which exec's `/usr/Cadmin/bin/clogin` |
| + `desktop on` | After login, `Xsession` hands off to `Xsession.dt`, launching full 4Dwm desktop |

The Indigo Magic login (`clogin`) is a separate program from xdm's built-in
login widget. It shows user account icons loaded from `~/.face.icon` and reads
its configuration from `/var/sysadm/config/clogin.conf`.

## What Was Installed

All four chkconfig flags were already `on` in our install:

```
/etc/config/windowsystem  → on
/etc/config/xdm           → on
/etc/config/visuallogin   → on
/etc/config/desktop       → on
```

`clogin` was also already present at `/usr/Cadmin/bin/clogin` (installed
as part of `sysadmdesktop.sw`) and `clogin.conf` existed at
`/var/sysadm/config/clogin.conf` with valid content.

## Problem 1: Empty xdm-config

`/var/X11/xdm/xdm-config` (the file the xdm binary reads, confirmed via
`strings /usr/bin/X11/xdm | grep config`) was **0 bytes**. xdm therefore
used its compiled-in defaults, which use the built-in C login widget and
never call `Xlogin`.

### How Xlogin is supposed to be invoked

The `Xlogin` script at `/var/X11/xdm/Xlogin` is IRIX's loginProgram. When
set in xdm-config, xdm replaces its built-in login widget with this external
program. The script:

1. Does gamma correction (LG1 graphics only — skipped on Newport)
2. Runs `xlistscrns -i`
3. Sets up screensaver via `xset`
4. Checks `visuallogin` and if on, `exec /usr/Cadmin/bin/clogin -f $1`

### Wrong resource name

Our first attempt used `DisplayManager*loginProgram` (wildcard `*`). This did
not work. The correct syntax for IRIX xdm requires a display-specific resource:

```
DisplayManager._0.loginProgram:    /var/X11/xdm/Xlogin
```

The wildcard form is ignored by IRIX's xdm build.

## Problem 2: X Authorization Rejection at Boot

After writing the correct xdm-config, clogin appeared when xdm was started
**manually** from a root shell, but reverted to the plain login on a cold
**reboot**.

Diagnosis via `/var/adm/SYSLOG`:

```
Xsgi0[847]: AUDIT: client 2 rejected from local host
Xsgi0[847]: last message repeated 3 times
```

clogin was being called but the X server rejected its connection. This is an
X authorization issue: xdm sets up a magic cookie for its own child processes,
but `clogin` (exec'd from the `Xlogin` shell script) does not receive the
cookie correctly during a cold boot.

The `Xlogin` script sets `XAUTHORITY=/usr/lib/X11/xdm/xdm-auth-$dpy` but
xdm writes its auth file to `/var/X11/xdm/authdir/` — a path mismatch that
causes the cookie lookup to fail at boot but not when xdm is manually started
from an existing session (where auth state may already be in place).

### Fix

Disable X authorization for the login display:

```
DisplayManager._0.authorize: false
```

This is safe for a login screen — no session data is at risk during the
pre-login phase.

## Final xdm-config

Written to `/var/X11/xdm/xdm-config` (hardlinked to `/usr/lib/X11/xdm/xdm-config`):

```
DisplayManager._0.loginProgram:    /var/X11/xdm/Xlogin
DisplayManager.grabServer:      False
DisplayManager._0.authorize: false
```

## Troubleshooting Steps

1. Confirmed all four chkconfig flags were `on` via `cat /etc/config/<flag>`
2. Confirmed `clogin` binary exists at `/usr/Cadmin/bin/clogin` (ELF N32)
3. Found correct config path via `strings /usr/bin/X11/clogin | grep /var` → `/var/sysadm/config/clogin.conf`
4. Confirmed `clogin.conf` exists and is readable
5. Found `xdm-config` was 0 bytes — wrote loginProgram resource
6. Tried `DisplayManager*loginProgram` (wildcard `*`) → no effect
7. Switched to `DisplayManager._0.loginProgram` (display-specific) → clogin appeared on manual xdm restart
8. After cold reboot, plain xdm returned despite config being intact
9. Found `AUDIT: client 2 rejected from local host` in `/var/adm/SYSLOG` — X auth rejection
10. Added `DisplayManager._0.authorize: false` → clogin persists across cold reboots ✓

## What clogin Shows

With a default install, the user picker shows:
- **root** — generic workstation icon (no `~root/.face.icon`)
- **EZsetup** — SGI logo icon (installed by sysadmdesktop)
- **demos** — generic workstation icon
- **guest** — generic workstation icon

Custom per-user icons can be placed at `~username/.face.icon` (SGI RGB format).

## Key Files

| File | Purpose |
|------|---------|
| `/var/X11/xdm/xdm-config` | xdm resource config (hardlinked to `/usr/lib/X11/xdm/xdm-config`) |
| `/var/X11/xdm/Xlogin` | loginProgram script — checks visuallogin, exec's clogin |
| `/usr/Cadmin/bin/clogin` | Indigo Magic login binary (from `sysadmdesktop.sw`) |
| `/var/sysadm/config/clogin.conf` | clogin config: which users to show/hide |
| `/var/adm/SYSLOG` | Where xdm/Xsgi auth failures are logged |

## Relation to Session Desktop

After clogin authenticates, xdm runs `Xsession`. `Xsession` checks `desktop`
and if on, exec's `Xsession.dt` — the full Indigo Magic session that launches
4Dwm, toolchest, file manager, soundscheme, and all desktop daemons.

## Apply to Future Installs

`apply_xdm_fixes()` in `tools/install_irix.py` currently only writes
`grabServer: False`. It should also write the `loginProgram` and `authorize`
lines so fresh installs get the Indigo Magic login automatically.
