from homeassistant.helpers.entity import Entity

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    port = config.get('port')
    baudrate = config.get('baudrate', 9600)
    
    sensor = SerialPortSensor(port, baudrate)
    async_add_entities([sensor])

class SerialPortSensor(Entity):
    def __init__(self, port, baudrate):
        self._port = port
        self._baudrate = baudrate
        self._state = None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._serial = serial.Serial(self._port, self._baudrate)

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()
        self._serial.close()

    async def async_update(self):
        data = self._serial.readline().strip().decode()
        self._state = data

    @property
    def name(self):
        return f"Serial Port Sensor ({self._port})"

    @property
    def state(self):
        return self._state
