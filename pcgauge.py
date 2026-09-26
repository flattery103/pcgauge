#!/usr/bin/env python3
"""PCGauge: four live Linux resource gauges. No third-party packages."""

import argparse
from collections import deque
import curses
import locale
import math
import os
import shutil
import time


def cpu_times():
    with open('/proc/stat', encoding='ascii') as source:
        fields = [int(value) for value in source.readline().split()[1:]]
    idle = fields[3] + fields[4]
    return sum(fields), idle


def cpu_percent(previous, current):
    elapsed = current[0] - previous[0]
    if elapsed <= 0:
        return 0.0
    idle = current[1] - previous[1]
    return max(0.0, min(100.0, 100.0 * (elapsed - idle) / elapsed))


def memory_usage(contents):
    values = {}
    for line in contents.splitlines():
        name, _, remainder = line.partition(':')
        if name in ('MemTotal', 'MemAvailable', 'MemFree'):
            values[name] = int(remainder.split()[0]) * 1024
    total = values['MemTotal']
    available = values.get('MemAvailable', values.get('MemFree', 0))
    used = max(0, total - available)
    return (100.0 * used / total if total else 0.0), used, total


def read_memory():
    with open('/proc/meminfo', encoding='ascii') as source:
        return memory_usage(source.read())


def format_size(size):
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'):
        if size < 1024 or unit == 'PiB':
            return f'{size:.1f} {unit}'
        size /= 1024


def fit(value, width):
    return value if len(value) <= width else value[:width - 1] + '…'


def trend_levels(samples, width, maximum, height=1):
    """Newest visible samples, expressed in eighths of a terminal cell."""
    return [max(0, min(8 * height, round(value * 8 * height / maximum)))
            if maximum > 0 else 0 for value in list(samples)[-width:]]


def network_counters(contents, interface):
    for row in contents.splitlines():
        name, separator, data = row.partition(':')
        if separator and name.strip() == interface:
            fields = data.split()
            return int(fields[0]), int(fields[8])
    raise ValueError(f'network interface {interface!r} not found')


def default_interface():
    with open('/proc/net/route', encoding='ascii') as source:
        for row in source.readlines()[1:]:
            fields = row.split()
            if len(fields) >= 4 and fields[1] == '00000000' and int(fields[3], 16) & 1:
                return fields[0]
    with open('/proc/net/dev', encoding='ascii') as source:
        names = [row.split(':', 1)[0].strip() for row in source if ':' in row]
    return next((name for name in names if name != 'lo'), 'lo')


def read_network(interface):
    with open('/proc/net/dev', encoding='ascii') as source:
        return network_counters(source.read(), interface)


def network_rates(previous, current, seconds):
    if seconds <= 0:
        return 0.0, 0.0
    return tuple(max(0, new - old) / seconds for old, new in zip(previous, current))


def network_scale_mbps(rate_mbps, previous_scale=100, capacity=None):
    if capacity is not None:
        return capacity
    scale = previous_scale
    while rate_mbps > scale:
        scale *= 2
    return scale


def dial_points(cx, cy, count=33, radius_x=10, radius_y=5):
    """Coordinates along an upper semicircle, from zero to full scale."""
    return [
        (round(cx - radius_x * math.cos(math.pi * index / (count - 1))),
         round(cy - radius_y * math.sin(math.pi * index / (count - 1))))
        for index in range(count)
    ]


def fill_points(cx, cy, radius_x, radius_y, percent):
    """Cells in the filled part of the dial, moving from left to right."""
    if percent <= 0:
        return []
    points = []
    for y in range(cy - radius_y + 1, cy + 1):
        for x in range(cx - radius_x + 1, cx + radius_x):
            horizontal = (x - cx) / radius_x
            vertical = (cy - y) / radius_y
            if horizontal * horizontal + vertical * vertical > 0.84:
                continue
            position = 1 - math.atan2(vertical, horizontal) / math.pi
            if position * 100 <= percent:
                points.append((x, y))
    return points


def put(screen, y, x, value, color=0):
    height, width = screen.getmaxyx()
    if 0 <= y < height and x < width and x + len(value) > 0:
        start = max(0, -x)
        try:
            screen.addstr(y, max(0, x), value[start:width - x], color)
        except curses.error:
            pass


def tone(percent):
    return 3 if percent >= 90 else 2 if percent >= 70 else 1


def line(screen, x0, y0, x1, y1, color):
    """Draw the needle with integer line steps."""
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    glyph = '━' if dx > 2 * -dy else '┃' if -dy > 2 * dx else ('╱' if (x1 - x0) * (y1 - y0) < 0 else '╲')
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    error = dx + dy
    while (x0, y0) != (x1, y1):
        put(screen, y0, x0, glyph, color)
        doubled = 2 * error
        if doubled >= dy:
            error += dy
            x0 += sx
        if doubled <= dx:
            error += dx
            y0 += sy


def draw_dial(screen, x, y, title, percent, detail, color_enabled,
              radius_x=15, radius_y=7, reading=None, maximum='100'):
    cx, cy = x + radius_x + 3, y + radius_y + 2
    width = radius_x * 2 + 6
    put(screen, y, x, title.center(width), curses.A_BOLD)
    points = dial_points(cx, cy, 49, radius_x, radius_y)
    active = round(max(0, min(100, percent)) * (len(points) - 1) / 100)
    fill_attribute = curses.color_pair(1) if color_enabled else curses.A_BOLD
    for fx, fy in fill_points(cx, cy, radius_x, radius_y, percent):
        put(screen, fy, fx, '█', fill_attribute)
    for index, (px, py) in enumerate(points):
        attribute = curses.color_pair(tone(percent)) if color_enabled and index <= active else 0
        put(screen, py, px, '◆' if index % 12 == 0 else '●', attribute)
    needle_x, needle_y = points[active]
    attribute = curses.color_pair(tone(percent)) if color_enabled else curses.A_BOLD
    line(screen, cx, cy, needle_x, needle_y, curses.A_BOLD)
    put(screen, cy, cx, '◉', curses.A_BOLD)
    put(screen, cy + 2, x, fit(reading or f'{percent:5.1f}%', width).center(width), curses.A_BOLD | attribute)
    put(screen, cy + 3, x, fit(detail, width).center(width))
    put(screen, cy + 1, cx - radius_x, '0')
    put(screen, cy + 1, cx + radius_x - len(maximum) + 1, maximum)


def dial_layout(width, height, trend=False):
    """Four dial origins and radii across one row, scaled to the terminal."""
    if width < 72 or height < 16:
        return []
    cell_width = width // 4
    radius_x = min(18, (cell_width - 6) // 2)
    radius_y = (min(7, max(4, (height - 20) // 2)) if trend
                else min(9, max(4, (height - 8) // 2)))
    dial_width = radius_x * 2 + 6
    return [((cell_width * index) + (cell_width - dial_width) // 2,
             1, radius_x, radius_y) for index in range(4)]


def draw_trends(screen, histories, scale, layout, color_enabled):
    height, width = screen.getmaxyx()
    start_y = layout[0][1] + layout[0][3] + 6
    chart_height = min(6, (height - start_y - 1) // 4)
    if chart_height < 3:
        put(screen, start_y, 2, 'Increase terminal height to 24 rows for trend graphs.')
        return
    graph_x, graph_width = 8, max(0, width - 10)
    charts = (
        ('CPU', histories[0], 100, 1, '%'),
        ('MEM', histories[1], 100, 2, '%'),
        ('DISK', histories[2], 100, 4, '%'),
        ('NET', histories[3], scale, 5, 'Mb/s'),
    )
    blocks = ' ▁▂▃▄▅▆▇█'
    for index, (name, samples, maximum, color, unit) in enumerate(charts):
        top = start_y + index * chart_height
        put(screen, top, 2, f'{name}  0–{maximum:g} {unit}', curses.A_BOLD)
        rows = chart_height - 1
        levels = trend_levels(samples, graph_width, maximum, rows)
        offset = graph_x + graph_width - len(levels)
        for column, level in enumerate(levels):
            for row in range(rows):
                portion = max(0, min(8, level - (rows - row - 1) * 8))
                if portion:
                    attribute = curses.color_pair(color) if color_enabled else curses.A_BOLD
                    put(screen, top + row + 1, offset + column, blocks[portion], attribute)


def draw(screen, metrics, mount, interface, interval, color_enabled,
         trend=False, histories=None):
    screen.erase()
    height, width = screen.getmaxyx()
    cpu, memory, disk, network = metrics
    items = (
        ('PROCESSOR', cpu, 'CPU utilization', None, '100'),
        ('MEMORY', memory[0], f'{format_size(memory[1])}/{format_size(memory[2])}', None, '100'),
        ('STORAGE', disk[0], f'{format_size(disk[1])}/{format_size(disk[2])}', None, '100'),
        ('BANDWIDTH', network[0], network[2], network[1], network[3]),
    )
    put(screen, 0, 2, 'PCGAUGE  •  TREND' if trend else 'PCGAUGE', curses.A_BOLD)
    layout = dial_layout(width, height, trend=trend)
    if layout:
        for (title, percent, detail, reading, maximum), (x, y, radius_x, radius_y) in zip(items, layout):
            draw_dial(screen, x, y, title, percent, detail, color_enabled,
                      radius_x=radius_x, radius_y=radius_y,
                      reading=reading, maximum=maximum)
        if trend and histories is not None:
            draw_trends(screen, histories, float(network[3]), layout, color_enabled)
    else:
        put(screen, 2, 2, 'Widen terminal to 72 columns and 16 rows for four dials.')
    put(screen, height - 1, 2, f'q quit  •  {interval:g}s refresh  •  disk {mount}  •  net {interface}')
    screen.refresh()


def app(screen, mount, interval, interface, capacity, trend):
    curses.curs_set(0)
    screen.timeout(int(interval * 1000))
    color_enabled = curses.has_colors()
    if color_enabled:
        curses.start_color()
        curses.use_default_colors()
        for number, foreground in enumerate((curses.COLOR_GREEN, curses.COLOR_YELLOW,
                                              curses.COLOR_RED, curses.COLOR_CYAN,
                                              curses.COLOR_MAGENTA), 1):
            curses.init_pair(number, foreground, -1)
    previous = cpu_times()
    previous_network = read_network(interface)
    previous_time = time.monotonic()
    scale = 100.0
    histories = tuple(deque(maxlen=600) for _ in range(4))
    while True:
        current = cpu_times()
        current_network = read_network(interface)
        current_time = time.monotonic()
        down, up = network_rates(previous_network, current_network, current_time - previous_time)
        down_mbps, up_mbps = down * 8 / 1_000_000, up * 8 / 1_000_000
        total_mbps = down_mbps + up_mbps
        scale = network_scale_mbps(total_mbps, scale, capacity)
        network = (min(100, total_mbps / scale * 100), f'{total_mbps:.1f} Mbps',
                   f'D {down_mbps:.1f} / U {up_mbps:.1f} Mb/s', f'{scale:g}', total_mbps)
        with_memory = read_memory()
        storage = shutil.disk_usage(mount)
        used = storage.used
        disk = (100.0 * used / storage.total if storage.total else 0.0, used, storage.total)
        for history, value in zip(histories, (cpu_percent(previous, current),
                                             with_memory[0], disk[0], total_mbps)):
            history.append(value)
        draw(screen, (cpu_percent(previous, current), with_memory, disk, network),
             mount, interface, interval, color_enabled, trend, histories)
        previous = current
        previous_network = current_network
        previous_time = current_time
        key = screen.getch()
        if key in (ord('q'), ord('Q'), 27):
            break


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mount', default='/', help='filesystem to measure (default: /)')
    parser.add_argument('--interval', type=float, default=0.5, help='refresh seconds (default: 0.5)')
    parser.add_argument('--interface', help='network interface (default: default route)')
    parser.add_argument('--link-mbps', type=float, help='link capacity for bandwidth gauge (default: auto scale)')
    parser.add_argument('--trend', action='store_true', help='show four live history graphs below the gauges')
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error('--interval must be positive')
    if not os.path.isdir(args.mount):
        parser.error('--mount must be an existing directory')
    if args.link_mbps is not None and args.link_mbps <= 0:
        parser.error('--link-mbps must be positive')
    interface = args.interface or default_interface()
    try:
        read_network(interface)
    except ValueError as error:
        parser.error(str(error))
    locale.setlocale(locale.LC_ALL, '')
    try:
        curses.wrapper(app, args.mount, args.interval, interface, args.link_mbps, args.trend)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
