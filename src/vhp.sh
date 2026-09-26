#!/usr/bin/env bash
set -euo pipefail

HELPER=/home/.vhp/bin/vhp-root
if [[ ! -x "$HELPER" ]]; then
  echo 'VHP is not installed. Run ./setup.sh from the checkout first.' >&2
  exit 1
fi

POWER_SUPPLY_ROOT=/sys/class/power_supply
battery_percent='--'
battery_status='Unavailable'
clock_time='--:--'
local_ip='Unavailable'
client_ips='Unavailable'
client_count=0
client_status='Status unavailable'
ui_active=false
ui_dirty=true
shutdown_shown=false
last_display=''
rows=24
cols=80
settings_available=false
settings_pid=''
terminal_state=''

# BEGIN DASHBOARD_FUNCTIONS
sample_battery() {
  local supply kind scope value state
  battery_percent='--'
  battery_status='Unavailable'
  for supply in "$POWER_SUPPLY_ROOT"/*; do
    [[ -r "$supply/type" ]] || continue
    kind=$(<"$supply/type") || continue
    [[ $kind == Battery ]] || continue
    scope=''
    if [[ -r "$supply/scope" ]]; then scope=$(<"$supply/scope") || continue; fi
    [[ $scope != Device && -r "$supply/capacity" ]] || continue
    value=$(<"$supply/capacity") || continue
    if [[ ! $value =~ ^[0-9]{1,3}$ ]] || ((10#$value > 100)); then continue; fi
    battery_percent=$((10#$value))
    state='Unknown'
    if [[ -r "$supply/status" ]]; then state=$(<"$supply/status") || state='Unknown'; fi
    case "$state" in
      Charging | Discharging | Full | 'Not charging') battery_status=$state ;;
      *) battery_status='Unknown' ;;
    esac
    return 0
  done
}

sample_network() {
  local route='' sockets='' field previous='' _recv _send _local_endpoint peer _rest
  local -A seen=()
  local -a peers=()
  local_ip='Unavailable'
  # Kernel route lookups only: no packets are sent to these destinations.
  route=$(timeout 1 ip -o -4 route get 1.1.1.1 2>/dev/null) ||
    route=$(timeout 1 ip -o -6 route get 2606:4700:4700::1111 2>/dev/null) || route=''
  for field in $route; do
    if [[ $previous == src && $field =~ ^[0-9A-Fa-f:.]+(%[a-zA-Z0-9_.-]+)?$ ]]; then
      local_ip=$field
      break
    fi
    previous=$field
  done
  client_count=0
  client_ips='Unavailable'
  client_status='Status unavailable'
  # Unprivileged TCP connection metadata; never inspect payloads or private config.
  if ! sockets=$(timeout 1 ss -Hnt state established '( sport = :7575 )' 2>/dev/null); then
    return 0
  fi
  while read -r _recv _send _local_endpoint peer _rest; do
    [[ -n $peer ]] || continue
    peer=${peer%:*}
    peer=${peer#\[}
    peer=${peer%\]}
    # Reject unexpected output, especially control sequences, rather than render it.
    if [[ ! $peer =~ ^[0-9A-Fa-f:.]+(%[a-zA-Z0-9_.-]+)?$ ]]; then continue; fi
    if [[ -z ${seen[$peer]+present} ]]; then
      seen[$peer]=1
      peers+=("$peer")
    fi
  done <<<"$sockets"
  client_count=${#peers[@]}
  client_ips='None'
  client_status='Waiting for client'
  if ((client_count > 0)); then
    # Stable ordering prevents redraws when ss changes socket enumeration order.
    client_ips=''
    while IFS= read -r peer; do
      client_ips+="${client_ips:+, }$peer"
    done < <(printf '%s\n' "${peers[@]}" | LC_ALL=C sort)
    client_status="TCP clients: $client_count"
  fi
}

text_at() {
  local row=$1 column=$2 text=$3
  if ((row < 1 || row > rows || column < 1 || column > cols)); then return 0; fi
  printf '\033[%s;%sH%s' "$row" "$column" "${text:0:cols-column+1}"
}

# Uses the glyph table local to paint_dashboard (Bash dynamic scope).
draw_block() {
  local text=$1 top=$2 left=$3 numerator=$4 denominator=$5
  local row char x source_row line scaled height=$(((5 * numerator + denominator - 1) / denominator))
  local -a cells
  for ((row = 0; row < height; row++)); do
    source_row=$((row * denominator / numerator))
    line=''
    for ((char = 0; char < ${#text}; char++)); do
      IFS='|' read -r -a cells <<<"${glyph[${text:char:1}]}"
      line+="${cells[source_row]} "
    done
    line=${line% }
    scaled=''
    for ((x = 0; x < (${#line} * numerator + denominator - 1) / denominator; x++)); do
      scaled+=${line:x*denominator/numerator:1}
    done
    text_at "$((top + row))" "$left" "$scaled"
  done
}

paint_dashboard() {
  local left top title='VIRTUALHEREPAD' battery_color=37 connection_color=33
  local numerator=1 denominator=1 height=5 width=64 clock_width=27 title_width=55 battery_left
  local key="$battery_percent|$battery_status|$clock_time|$local_ip|$client_ips|$client_status|$rows|$cols"
  if [[ $key == "$last_display" && $ui_dirty == false ]]; then return 0; fi
  local -A glyph=(
    [0]=' ### |#   #|#   #|#   #| ### '
    [1]='  #  | ##  |  #  |  #  | ### '
    [2]='#### |    #| ### |#    |#####'
    [3]='#### |    #| ### |    #|#### '
    [4]='#   #|#   #|#####|    #|    #'
    [5]='#####|#    |#### |    #|#### '
    [6]=' ### |#    |#### |#   #| ### '
    [7]='#####|    #|   # |  #  | #   '
    [8]=' ### |#   #| ### |#   #| ### '
    [9]=' ### |#   #| ####|    #| ### '
    [':']='   | # |   | # |   '
    ['%']='##  #|## # |  #  | # ##|#  ##'
    ['-']='     |     |#####|     |     '
    [A]=' # |# #|###|# #|# #'
    [D]='## |# #|# #|# #|## '
    [E]='###|#  |## |#  |###'
    [H]='# #|# #|###|# #|# #'
    [I]='###| # | # | # |###'
    [L]='#  |#  |#  |#  |###'
    [P]='## |# #|## |#  |#  '
    [R]='## |# #|## |# #|# #'
    [T]='###| # | # | # | # '
    [U]='# #|# #|# #|# #|###'
    [V]='# #|# #|# #|# #| # '
  )
  last_display=$key
  ui_dirty=false
  if ! "$ui_active"; then
    printf 'VirtualHerePad | Server running | %s | Battery: %s%% (%s) | Local IP: %s | %s | Clients: %s\n' \
      "$clock_time" "$battery_percent" "$battery_status" "$local_ip" "$client_status" "$client_ips"
    return 0
  fi
  if [[ $battery_percent != -- ]]; then
    if ((battery_percent <= 15)); then
      battery_color=31
    elif ((battery_percent <= 30)); then
      battery_color=33
    else battery_color=32; fi
  fi
  if ((client_count > 0)); then connection_color=32; fi
  printf '\033[0;37;40m\033[2J'
  if ((rows < 24 || cols < 72)); then
    printf '\033[1;36m'
    text_at 1 1 'VirtualHerePad - Server running'
    printf '\033[0;37;40m'
    text_at 2 1 "Local time: $clock_time"
    printf '\033[%sm' "$battery_color"
    text_at 3 1 "Battery: $battery_percent% ($battery_status)"
    printf '\033[%sm' "$connection_color"
    text_at 4 1 "$client_status"
    printf '\033[0;37;40m'
    text_at 5 1 "Local IP: $local_ip"
    text_at 6 1 "Clients: $client_ips"
    text_at 8 1 'Hold any corner for 2s to stop.'
    text_at 9 1 'Local keyboard: Ctrl+C'
    paint_settings
    return 0
  fi
  if ((cols >= 120 && rows >= 34)); then
    numerator=2
  elif ((cols >= 90 && rows >= 30)); then
    numerator=3
    denominator=2
  fi
  height=$(((5 * numerator + denominator - 1) / denominator))
  title_width=$(((55 * numerator + denominator - 1) / denominator))
  clock_width=$(((27 * numerator + denominator - 1) / denominator))
  width=$((clock_width + 8 + (23 * numerator + denominator - 1) / denominator))
  if ((width < title_width)); then width=$title_width; fi
  left=$(((cols - width) / 2 + 1))
  top=$(((rows - (2 * height + 13)) / 2 + 1))
  battery_left=$((left + clock_width + 8))
  printf '\033[36m'
  text_at 1 1 '+ HOLD 2s'
  text_at 1 "$((cols - 8))" 'HOLD 2s +'
  text_at "$rows" 1 '+ HOLD 2s'
  text_at "$rows" "$((cols - 8))" 'HOLD 2s +'
  printf '\033[1m'
  draw_block "$title" "$((top + 1))" "$((left + (width - title_width) / 2))" "$numerator" "$denominator"
  printf '\033[0;37;40m'
  text_at "$((top + height + 2))" "$left" 'LOCAL TIME'
  text_at "$((top + height + 2))" "$battery_left" 'BATTERY'
  printf '\033[1;35m'
  draw_block "$clock_time" "$((top + height + 4))" "$left" "$numerator" "$denominator"
  printf '\033[1;%sm' "$battery_color"
  draw_block "$battery_percent%" "$((top + height + 4))" "$battery_left" "$numerator" "$denominator"
  printf '\033[0;37;40m'
  text_at "$((top + 2 * height + 4))" "$battery_left" "$battery_status"
  printf '\033[1;32m'
  text_at "$((top + 2 * height + 6))" "$left" 'SERVER RUNNING'
  printf '\033[0;%s;40m' "$connection_color"
  text_at "$((top + 2 * height + 6))" "$((left + 18))" "$client_status"
  printf '\033[0;36;40m'
  text_at "$((top + 2 * height + 8))" "$left" "Local IP: $local_ip"
  text_at "$((top + 2 * height + 9))" "$left" "Clients: $client_ips"
  printf '\033[0;37;40m'
  text_at "$((top + 2 * height + 11))" "$left" 'Hold one finger in any corner for 2 seconds to exit.'
  text_at "$((top + 2 * height + 12))" "$left" 'Local keyboard: Ctrl+C | TCP link only; device use not checked'
  paint_settings
}

paint_settings() {
  local row=1
  if ((rows < 24 || cols < 72)); then row=10; fi
  if [[ ${settings_available:-false} == true ]]; then
    text_at "$row" "$(((cols - 12) / 2 + 1))" '[ SETTINGS ]'
  else
    text_at "$((rows - 1))" 1 'Settings unavailable - rerun setup with --gui'
  fi
}

show_shutdown() {
  if [[ ${shutdown_shown:-false} == true && $ui_dirty == false ]]; then return 0; fi
  shutdown_shown=true
  ui_dirty=false
  if ! "$ui_active"; then
    printf 'SHUTTING DOWN - waiting for VirtualHere to stop...\n'
    return 0
  fi
  local message='SHUTTING DOWN' scale=1 width=51 height=5 top left
  local -A glyph=(
    [S]='###|#  |###|  #|###' [H]='# #|# #|###|# #|# #'
    [U]='# #|# #|# #|# #|###' [T]='###| # | # | # | # '
    [I]='###| # | # | # |###' [N]='# #|###|###|###|# #'
    [G]='###|#  |# #|# #|###' [D]='## |# #|# #|# #|## '
    [O]='###|# #|# #|# #|###' [W]='# #|# #|###|###|# #'
    [' ']='   |   |   |   |   '
  )
  printf '\033[0;37;40m\033[2J\033[1;36m'
  if ((cols < 55 || rows < 10)); then
    text_at 1 1 "$message"
    printf '\033[0;37;40m'
    text_at 3 1 'Waiting for VirtualHere to stop...'
    return 0
  fi
  if ((cols >= 106 && rows >= 18)); then scale=2; fi
  width=$((width * scale))
  height=$((height * scale))
  top=$(((rows - height - 3) / 2 + 1))
  left=$(((cols - width) / 2 + 1))
  draw_block "$message" "$top" "$left" "$scale" 1
  printf '\033[0;37;40m'
  text_at "$((top + height + 2))" "$((cols / 2 - 16))" 'Waiting for VirtualHere to stop...'
}

resize_dashboard() {
  local size
  if "$ui_active" && size=$(stty size 2>/dev/null) && [[ $size =~ ^[0-9]+\ [0-9]+$ ]]; then
    read -r rows cols <<<"$size"
  fi
  ui_dirty=true
}
# END DASHBOARD_FUNCTIONS

# The popup is unprivileged and only edits next-launch preferences. Keep the
# heartbeat in this process running while it is open; never start another service.
open_settings() {
  "$settings_available" || return 0
  if [[ -n $settings_pid ]] && jobs -pr | grep -qx "$settings_pid"; then return 0; fi
  /usr/bin/python3 -I "$base/vhp_qt.py" --settings </dev/null 9>&- &
  settings_pid=$!
}

terminal_input() {
  local key='' sequence='' x y target_row=1
  IFS= read -rsn1 -t 1 key || return 0
  case "$key" in
    s | S) open_settings ;;
    $'\033')
      # Bounded SGR mouse press: Konsole turns a touchscreen tap into a click.
      IFS= read -rsn32 -d M -t 0.05 sequence || true
      if [[ $sequence =~ ^\[\<0\;([0-9]{1,4})\;([0-9]{1,4})$ ]]; then
        x=$((10#${BASH_REMATCH[1]}))
        y=$((10#${BASH_REMATCH[2]}))
        if ((rows < 24 || cols < 72)); then target_row=10; fi
        if ((y == target_row && x >= (cols - 12) / 2 + 1 && x <= (cols - 12) / 2 + 12)); then open_settings; fi
      fi
      ;;
  esac
}

# Invoked by the EXIT trap, including after INT/TERM.
# shellcheck disable=SC2329
cleanup() {
  local status=$?
  trap - EXIT INT TERM WINCH
  show_shutdown
  if [[ -n $settings_pid ]] && jobs -pr | grep -qx "$settings_pid"; then
    kill -TERM "$settings_pid" 2>/dev/null || true
  fi
  sudo -n "$HELPER" stop || true
  if "$ui_active"; then printf '\033[?1000l\033[?1006l\033[0m\033[?25h\033[?1049l'; fi
  if [[ -n $terminal_state ]]; then stty "$terminal_state" || true; fi
  if ((status != 0)); then
    echo "VHP exited with code $status. Inspect logs: journalctl -u vhp.service" >&2
  fi
  echo 'VHP stopped.'
}
# Pulse Steam's idle bookkeeping before privileged brightness changes. The
# helper returns a validated target token, or nothing outside Gaming Mode.
base=$(dirname -- "$(readlink -f -- "$0")")
if [[ -n ${WAYLAND_DISPLAY:-}${DISPLAY:-} && -f $base/pylib/PySide6/__init__.py ]] &&
  timeout 5 /usr/bin/python3 -I "$base/vhp_qt.py" --check-runtime >/dev/null 2>&1; then
  settings_available=true
fi
IDLE_HELPER="$base/vhp_idle.py"
idle_target=$(/usr/bin/python3 -I "$IDLE_HELPER" start)
next_idle_pulse=$((SECONDS + 10))
# Do not claim/stop somebody else's service if start is refused.
sudo -n "$HELPER" start
trap cleanup EXIT
trap 'exit 0' INT TERM
if [[ -t 1 && ${TERM:-dumb} != dumb ]]; then
  ui_active=true
  printf '\033[?1049h\033[?25l'
  if [[ -t 0 ]]; then
    terminal_state=$(stty -g)
    stty -echo -icanon min 0 time 0
    if "$settings_available"; then printf '\033[?1000h\033[?1006h'; fi
  fi
fi
resize_dashboard
trap 'ui_dirty=true' WINCH
next_battery_check=0
next_network_check=0
while /usr/bin/systemctl is-active --quiet vhp.service; do
  if [[ -n $settings_pid ]] && ! jobs -pr | grep -qx "$settings_pid"; then
    wait "$settings_pid" || true
    settings_pid=''
    ui_dirty=true
  fi
  if sudo -n "$HELPER" keepalive; then
    :
  else
    keepalive_status=$?
    if ((keepalive_status == 2)); then
      show_shutdown
    elif ! /usr/bin/systemctl is-active --quiet vhp.service; then
      break
    else
      echo 'ERROR: could not refresh the VHP heartbeat.' >&2
      exit 1
    fi
  fi
  # Reuse this heartbeat loop; no additional persistent watcher. Stop the
  # service through the EXIT trap if its Gamescope session/protection is lost.
  if [[ -n $idle_target && $shutdown_shown == false ]] && ((SECONDS >= next_idle_pulse)); then
    /usr/bin/python3 -I "$IDLE_HELPER" pulse "$idle_target"
    next_idle_pulse=$((SECONDS + 10))
  fi
  # Reuse the existing heartbeat loop; no extra polling processes or animations.
  if "$ui_dirty"; then resize_dashboard; fi
  if "$shutdown_shown"; then
    show_shutdown
  else
    if ((SECONDS >= next_battery_check)); then
      sample_battery
      next_battery_check=$((SECONDS + 30))
    fi
    if ((SECONDS >= next_network_check)); then
      sample_network
      next_network_check=$((SECONDS + 5))
    fi
    # Bash's builtin clock formatting adds no subprocess and displays no seconds.
    printf -v clock_time '%(%H:%M)T' -1
    paint_dashboard
  fi
  # Keep a foreground input loop for the Konsole/Steam input context.
  if [[ -t 0 ]]; then
    terminal_input
  else
    sleep 1
  fi
done
if /usr/bin/systemctl is-failed --quiet vhp.service; then
  echo 'VHP failed. Inspect logs with: journalctl -u vhp.service' >&2
  exit 1
fi
