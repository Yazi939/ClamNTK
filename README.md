# ClamNTK

ClamNTK — настольная программа для проверки файлов на РЕД ОС. Она ставит графическое окно поверх ClamAV и сама подтягивает нужные пакеты.

Проверено на РЕД ОС 7 и 8 с графическим сеансом.

## Скачать

Откройте страницу проекта и нажмите **Code → Download ZIP**, либо выполните в терминале:

```sh
git clone https://github.com/Yazi939/ClamNTK.git
cd ClamNTK
```

Если скачали ZIP, распакуйте его и откройте терминал в этой папке.

## Поставить в меню

Эти команды ставят программу так, чтобы она появилась в разделе **Администрирование**.

```sh
sudo dnf install -y python3 python3-qt5 python3-qt5-base clamav polkit
sudo mkdir -p /usr/share/cameav /usr/share/icons
sudo cp -a cameav /usr/share/cameav/cameav
sudo install -D -m 755 bin/cameav /usr/bin/cameav
sudo install -D -m 755 libexec/cameav-privileged /usr/libexec/cameav-privileged
sudo install -D -m 644 share/applications/cameav.desktop /usr/share/applications/cameav.desktop
sudo install -D -m 644 share/polkit/ru.redos.cameav.policy /usr/share/polkit-1/actions/ru.redos.cameav.policy
sudo cp -a share/icons/hicolor /usr/share/icons/
sudo update-desktop-database /usr/share/applications || true
sudo gtk-update-icon-cache -f -t /usr/share/icons/hicolor || true
```

После этого откройте меню **Администрирование** и запустите **ClamNTK**. В окне **Обновления** нажмите **Обновить базы**: без пакетов сигнатур проверка не начнётся. Для загрузки баз нужен интернет.

Запустить программу из папки, не ставя её в меню, можно так:

```sh
sudo dnf install -y python3 python3-qt5 python3-qt5-base clamav polkit
./bin/cameav
```

## Поставить готовый пакет

Если в [выпусках](https://github.com/Yazi939/ClamNTK/releases) есть файл `cameav-….rpm`, достаточно одной команды. `dnf` сам поставит ClamAV, Qt и polkit.

```sh
sudo dnf install -y ./cameav-*.rpm
```

Удаляется пакет так:

```sh
sudo dnf remove -y cameav
```

ClamAV при этом остаётся в системе.
