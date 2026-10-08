# PCGauge

A lightweight Linux terminal dashboard with analog-style gauges for CPU, memory, storage, and network bandwidth. Add live history graphs with `--trend`. Uses only Python 3's standard library.

<img width="3322" height="996" alt="image" src="https://github.com/user-attachments/assets/607e4f17-55de-4829-bae0-9ccb3c4bf3f1" />

## Run

```bash
python3 pcgauge.py
```

Press **q** to quit. To show the gauges and trend graphs:

```bash
python3 pcgauge.py --trend
```

## Options

- `--mount /path` — monitor another filesystem (default: `/`).
- `--interface eth0` — choose a network interface (default: interface used by the default route).
- `--link-mbps 1000` — set a fixed bandwidth scale in Mbps.
- `--interval 1` — refresh every second (default: 0.5).

The gauges need at least 72 terminal columns. Trend graphs work best with 24 or more rows.

## License

[GNU GPL v3](LICENSE).
