Name:           cameav
Version:        1.0.5
Release:        1%{?dist}
Summary:        Графическая оболочка ClamAV для РЕД ОС
License:        MIT
URL:            https://www.clamav.net/
Group:          Applications/System
Vendor:         ClamNTK
BuildArch:      noarch

Requires:       python3 >= 3.9
Requires:       python3-qt5
Requires:       python3-qt5-base
Requires:       clamav
Requires:       polkit

%global debug_package %{nil}
%{!?cameav_root:%{error:Pass --define "cameav_root /path/to/ClameAV"}}

%description
ClamNTK — настольная графическая оболочка ClamAV для РЕД ОС.
Пакет ставит программу в меню и подтягивает ClamAV и библиотеки Qt.
Базы сигнатур загружаются из окна программы.

%install
install -d %{buildroot}/usr/share/cameav
cp -a %{cameav_root}/cameav %{buildroot}/usr/share/cameav/cameav
find %{buildroot} -type d -name __pycache__ -print0 | xargs -0 -r rm -rf
find %{buildroot} -name '*.pyc' -delete
install -D -m 755 %{cameav_root}/bin/cameav %{buildroot}/usr/bin/cameav
install -D -m 755 %{cameav_root}/bin/cameav-uninstall %{buildroot}/usr/bin/cameav-uninstall
install -D -m 755 %{cameav_root}/libexec/cameav-privileged %{buildroot}/usr/libexec/cameav-privileged
rm -rf %{buildroot}/usr/share/cameav/installer
cp -a %{cameav_root}/installer %{buildroot}/usr/share/cameav/installer
find %{buildroot}/usr/share/cameav/installer -type d -name __pycache__ -print0 | xargs -0 -r rm -rf
install -D -m 644 %{cameav_root}/share/applications/cameav.desktop %{buildroot}/usr/share/applications/cameav.desktop
install -D -m 644 %{cameav_root}/share/applications/cameav-uninstall.desktop %{buildroot}/usr/share/applications/cameav-uninstall.desktop
install -D -m 644 %{cameav_root}/share/polkit/ru.redos.cameav.policy %{buildroot}/usr/share/polkit-1/actions/ru.redos.cameav.policy
mkdir -p %{buildroot}/usr/share/icons
cp -a %{cameav_root}/share/icons/hicolor %{buildroot}/usr/share/icons/

%post
update-desktop-database /usr/share/applications &> /dev/null || :
gtk-update-icon-cache -f -t /usr/share/icons/hicolor &> /dev/null || :

%postun
update-desktop-database /usr/share/applications &> /dev/null || :
gtk-update-icon-cache -f -t /usr/share/icons/hicolor &> /dev/null || :

%files
/usr/bin/cameav
/usr/bin/cameav-uninstall
/usr/libexec/cameav-privileged
/usr/share/cameav
/usr/share/applications/cameav.desktop
/usr/share/applications/cameav-uninstall.desktop
/usr/share/polkit-1/actions/ru.redos.cameav.policy
/usr/share/icons/hicolor/*/apps/cameav.png
/usr/share/icons/hicolor/scalable/apps/cameav.svg

%changelog
* Mon Oct 26 2026 ClamNTK <cameav@localhost> - 1.0.5-1
- Выпуск ClamNTK 1.0.5
* Fri Oct 23 2026 ClamNTK <cameav@localhost> - 1.0.4-1
- Выпуск ClamNTK 1.0.4
* Fri Oct 23 2026 ClamNTK <cameav@localhost> - 1.0.3-1
- Выпуск ClamNTK 1.0.3
* Fri Oct 23 2026 ClamNTK <cameav@localhost> - 1.0.2-1
- Выпуск ClamNTK 1.0.2
* Fri Oct 23 2026 ClamNTK <cameav@localhost> - 1.0.1-1
- Выпуск ClamNTK 1.0.1
* Fri Oct 23 2026 ClamNTK <cameav@localhost> - 1.0.0-1
- Desktop GUI for ClamAV on RED OS
