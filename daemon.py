import asyncio
import json
import time
import psutil
import websockets
import settings

HOST: str = settings.HOST
PORT: int = settings.PORT
UPDATE_RATE_HZ: int = settings.UPDATE_RATE_HZ

CLIENTS: set = set()


class TelemetryCollector:
    def __init__(self) -> None:
        self.last_time = time.time()
        self.last_net = psutil.net_io_counters()
        self.last_disk = psutil.disk_io_counters()
        psutil.cpu_percent(percpu=True)

    def _get_temperature(self) -> dict:
        try:
            if not hasattr(psutil, "sensors_temperatures"):
                return {"package": None, "cores": []}

            temps = psutil.sensors_temperatures()
            if not temps:
                return {"package": None, "cores": []}

            sensor_entries = None
            possible_keys = ["coretemp", "k10temp", "cpu_thermal", "zenpower", "acpitz"]

            for key in possible_keys:
                if key in temps and temps[key]:
                    sensor_entries = temps[key]
                    break

            if not sensor_entries:
                for entries in temps.values():
                    if entries:
                        sensor_entries = entries
                        break

            if not sensor_entries:
                return {"package": None, "cores": []}

            core_temps = [
                round(entry.current, 1)
                for entry in sensor_entries
                if entry.current is not None
            ]

            package_temp = (
                round(sum(core_temps) / len(core_temps), 1) if core_temps else None
            )

            return {"package": package_temp, "cores": core_temps}
        except Exception:
            return {"package": None, "cores": []}

    def collect(self) -> dict:
        now = time.time()
        dt = max(now - self.last_time, 0.001)
        self.last_time = now

        net = psutil.net_io_counters()
        net_rx_rate = (net.bytes_recv - self.last_net.bytes_recv) / dt
        net_tx_rate = (net.bytes_sent - self.last_net.bytes_sent) / dt
        self.last_net = net

        disk = psutil.disk_io_counters()
        disk_read_rate = (
            (disk.read_bytes - self.last_disk.read_bytes) / dt if disk else 0
        )
        disk_write_rate = (
            (disk.write_bytes - self.last_disk.write_bytes) / dt if disk else 0
        )
        if disk:
            self.last_disk = disk

        cpu_cores = psutil.cpu_percent(percpu=True)
        cpu_total = sum(cpu_cores) / len(cpu_cores) if cpu_cores else 0
        ram = psutil.virtual_memory()

        return {
            "timestamp": now,
            "cpu": {
                "total": round(cpu_total, 1),
                "cores": [round(c, 1) for c in cpu_cores],
            },
            "ram": {
                "percent": round(ram.percent, 1),
                "used_mb": round(ram.used / (1024 * 1024), 1),
                "total_mb": round(ram.total / (1024 * 1024), 1),
            },
            "network": {
                "rx_kbps": round(net_rx_rate / 1024, 2),
                "tx_kbps": round(net_tx_rate / 1024, 2),
            },
            "disk": {
                "read_mbps": round(disk_read_rate / (1024 * 1024), 2),
                "write_mbps": round(disk_write_rate / (1024 * 1024), 2),
            },
            "temperature": self._get_temperature(),
        }


async def register_client(websocket) -> None:
    CLIENTS.add(websocket)
    remote = websocket.remote_address
    print(f"[+] Client connected: {remote[0]}:{remote[1]} | Active: {len(CLIENTS)}")
    try:
        await websocket.wait_closed()
    finally:
        CLIENTS.remove(websocket)
        print(
            f"[-] Client disconnected: {remote[0]}:{remote[1]} | Active: {len(CLIENTS)}"
        )


async def broadcast_loop(collector: TelemetryCollector) -> None:
    interval = 1.0 / UPDATE_RATE_HZ
    while True:
        if CLIENTS:
            payload = json.dumps(collector.collect())
            websockets.broadcast(CLIENTS, payload)
        await asyncio.sleep(interval)


async def main() -> None:
    collector = TelemetryCollector()
    print(f"Phantom Daemon initializing on ws://{HOST}:{PORT}")

    async with websockets.serve(register_client, HOST, PORT):
        print(f"Telemetry broadcaster running at {UPDATE_RATE_HZ}Hz")
        await broadcast_loop(collector)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nPhantom Daemon terminated")