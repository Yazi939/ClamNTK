#!/bin/sh
# Двойной щелчок в файловом менеджере РЕД ОС открывает удаление.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
exec ./bin/cameav-uninstall "$@"
