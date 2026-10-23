#!/bin/sh
# Двойной щелчок в файловом менеджере РЕД ОС открывает мастер установки.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
exec ./cameav-setup "$@"
