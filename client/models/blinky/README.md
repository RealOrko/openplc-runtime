# blinky

Minimal ST smoke-test. Toggles `%QX0.0` every 500 ms.

## Deploy

From the repo root, with the docker-compose stack running:

```bash
cd client
pip install -r requirements.txt
python -m openplc_client setup               # one-time binary download
python -m openplc_client deploy ./models/blinky
```

## Observe

If `conf/modbus_slave.json` is present, the runtime auto-enables the
Modbus Slave plugin on upload. Point any Modbus TCP client at
`tcp://localhost:5020` and watch coil `0` flip at 1 Hz:

```bash
pip install pymodbus
python -c "
from pymodbus.client import ModbusTcpClient
c = ModbusTcpClient('localhost', port=5020)
c.connect()
for _ in range(20):
    print(c.read_coils(0, count=1).bits[0])
    import time; time.sleep(0.25)
"
```
