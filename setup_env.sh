#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1
if [ -t 1 ]; then
    B=$'\033[1;97m'; W=$'\033[97m'; P=$'\033[94m'; G=$'\033[92m'; R=$'\033[91m'; N=$'\033[0m'
    printf '\033[2J\033[3J\033[H'
else
    B=; W=; P=; G=; R=; N=
fi
fail() {
    echo "${R}Установка не удалась. Нужен Python 3.12.${N}"
    exit 1
}
echo "${B}Окружение Python${N}"
echo "${W}Зависимости бота устанавливаются в виртуальное окружение.${N}"
echo
if [ -x .venv/bin/python ]; then
    PY=.venv/bin/python
    echo "${G}Найдено окружение .venv.${N}"
else
    printf '%s' "${P}Создать .venv? Enter - создать, n - сделаю сам: ${W}"
    read -r ANS || ANS=
    printf '%s' "$N"
    case "$ANS" in
        n|N|т|Т)
            OWN="$VIRTUAL_ENV$CONDA_PREFIX"
            if [ -z "$OWN" ]; then
                echo "${W}Создайте и активируйте своё окружение Python 3.12, затем запустите setup_env.sh снова.${N}"
                exit 1
            fi
            PY=python
            echo "${G}Используется окружение: $OWN${N}"
            ;;
        *)
            echo "${W}Создание .venv...${N}"
            "${PYTHON:-$(command -v python3.12 || command -v python3)}" -m venv .venv || fail
            PY=.venv/bin/python
            ;;
    esac
fi
TOTAL=$(grep -c . requirements.txt)
COUNT=0
echo
echo "${B}Установка пакетов${N}"
while IFS= read -r PACKAGE; do
    [ -n "$PACKAGE" ] || continue
    COUNT=$((COUNT + 1))
    echo "${W}[$COUNT/$TOTAL] $PACKAGE${N}"
    "$PY" -m pip install --disable-pip-version-check -q "$PACKAGE" </dev/null || fail
done < requirements.txt
echo "${G}Пакеты установлены.${N}"
exec "$PY" setup_env.py
