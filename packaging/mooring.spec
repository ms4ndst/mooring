Name:           mooring
Version:        0.1.2
Release:        1%{?dist}
Summary:        Your Android phone on the Linux desktop

License:        GPL-3.0-or-later
URL:            https://github.com/ms4ndst/mooring
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  desktop-file-utils
BuildRequires:  appstream

# The phone connection (SMS, notifications, files, battery) is KDE Connect's daemon.
Requires:       kde-connect
Requires:       fuse-sshfs
# Mirroring and phone apps
Requires:       android-tools
Requires:       scrcpy
# The Mirror page decodes video with PyAV, which isn't packaged for Fedora:
#   pip install --user av
# (found even though the launcher runs `python3 -sP`, see mooring/backend/pyav.py).
# Without it, the Mirror page offers "Pop out" (a separate scrcpy window).

%description
Mooring brings your Android phone to the desktop in one window: text messages,
the phone's notifications, the newest photos, files and the clipboard, and the
phone's screen or a single phone app in its own window. It is a front end for
KDE Connect (phone connection) and scrcpy/adb (mirroring).

%prep
%autosetup -n %{name}-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files -l mooring
install -Dpm 0644 data/io.github.ms4ndst.Mooring.desktop \
    %{buildroot}%{_datadir}/applications/io.github.ms4ndst.Mooring.desktop
install -Dpm 0644 data/io.github.ms4ndst.Mooring.metainfo.xml \
    %{buildroot}%{_metainfodir}/io.github.ms4ndst.Mooring.metainfo.xml
install -Dpm 0644 src/mooring/resources/icons/app.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/io.github.ms4ndst.Mooring.svg

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/io.github.ms4ndst.Mooring.desktop
appstreamcli validate --no-net %{buildroot}%{_metainfodir}/io.github.ms4ndst.Mooring.metainfo.xml
# Import everything except what needs optional/runtime-only bits.
%pyproject_check_import -e 'mooring.__main__'

%files -f %{pyproject_files}
%doc README.md
%{_bindir}/mooring
%{_datadir}/applications/io.github.ms4ndst.Mooring.desktop
%{_metainfodir}/io.github.ms4ndst.Mooring.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/io.github.ms4ndst.Mooring.svg

%changelog
* Wed Nov 25 2026 ms4ndst - 0.1.2-1
- Run KDE Connect through the USB cable (adb) when the phone is on another
  network; the phone shows as connected "via USB cable"
- Photos/Files explain that they need a shared Wi-Fi while on the cable link

* Thu Nov 19 2026 ms4ndst - 0.1.1-1
- Repair a dead sshfs mount of the phone's storage (left behind after a network
  change or kdeconnectd restart) and retry, instead of failing to mount
- Photos/Files re-mount on every refresh; a failed mount or unreadable storage
  is reported instead of "No photos on the phone yet"
- Desktop category InstantMessaging instead of Telephony (calls are out of scope)

* Thu Nov 19 2026 ms4ndst - 0.1.0-2
- Find PyAV installed with `pip install --user av`: the RPM launcher's
  `python3 -s` hides user site-packages, so the Mirror page didn't see it

* Thu Nov 19 2026 ms4ndst - 0.1.0-1
- First release: messages, notifications, photos, files, clipboard,
  screen mirroring (embedded and scrcpy) and phone apps
