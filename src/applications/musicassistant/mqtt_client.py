import time
import ujson as json

try:
    from umqtt.simple import MQTTClient
except ImportError:
    MQTTClient = None


class MusicAssistantMQTTClient:
    def __init__(self, broker, port, username, password, client_id, base_topic):
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.client_id = client_id
        self.base_topic = base_topic
        self.client = None

        self.on_state = None
        self.on_display = None
        self.on_leds = None
        self.on_brightness = None

        self.connected = False
        self.last_reconnect = 0

    def connect(self):
        if MQTTClient is None:
            print("umqtt.simple not available")
            return False

        try:
            self.client = MQTTClient(
                client_id=self.client_id,
                server=self.broker,
                port=self.port,
                user=self.username,
                password=self.password,
                keepalive=30
            )

            self.client.set_callback(self._handle_message)
            self.client.connect()

            self.client.subscribe(self.base_topic + "/state")
            self.client.subscribe(self.base_topic + "/ha/display/set")
            self.client.subscribe(self.base_topic + "/ha/leds/set")
            self.client.subscribe(self.base_topic + "/ha/brightness/set")

            self.connected = True

            print("MQTT connected")
            print("Subscribed to " + self.base_topic + "/state")
            print("Subscribed to " + self.base_topic + "/ha/display/set")
            print("Subscribed to " + self.base_topic + "/ha/leds/set")
            self.client.subscribe(self.base_topic + "/ha/brightness/set")

            self.publish_discovery()
            self.publish_display_state(True)
            self.publish_led_state(True)

            return True

        except Exception as e:
            self.connected = False
            print("MQTT connect failed:", e)
            return False

    def publish_discovery(self):
        if not self.client or not self.connected:
            return

        device = {
            "identifiers": [self.client_id],
            "name": "Presto Deck",
            "manufacturer": "Pimoroni",
            "model": "Presto",
        }

        display_config = {
            "name": "Display",
            "unique_id": self.client_id + "_display",
            "command_topic": self.base_topic + "/ha/display/set",
            "state_topic": self.base_topic + "/ha/display/state",
            "payload_on": "ON",
            "payload_off": "OFF",
            "device": device,
        }

        leds_config = {
            "name": "LEDs",
            "unique_id": self.client_id + "_leds",
            "command_topic": self.base_topic + "/ha/leds/set",
            "state_topic": self.base_topic + "/ha/leds/state",
            "payload_on": "ON",
            "payload_off": "OFF",
            "device": device,
        }

        brightness_config = {
            "name": "Brightness",
            "unique_id": self.client_id + "_brightness",
            "command_topic": self.base_topic + "/ha/brightness/set",
            "state_topic": self.base_topic + "/ha/brightness/state",
            "min": 0,
            "max": 100,
            "step": 5,
            "mode": "slider",
            "device": device,
        }

        try:
            self.client.publish(
                "homeassistant/switch/" + self.client_id + "/display/config",
                json.dumps(display_config),
                retain=True
            )

            self.client.publish(
                "homeassistant/switch/" + self.client_id + "/leds/config",
                json.dumps(leds_config),
                retain=True
            )

            self.client.publish(
                "homeassistant/number/" + self.client_id + "/brightness/config",
                json.dumps(brightness_config),
                retain=True
            )

            print("Published Home Assistant discovery")

        except Exception as e:
            print("MQTT discovery publish failed:", e)

    def publish_display_state(self, is_on):
        if self.client and self.connected:
            try:
                self.client.publish(
                    self.base_topic + "/ha/display/state",
                    "ON" if is_on else "OFF",
                    retain=True
                )
            except Exception as e:
                print("Display state publish failed:", e)

    def publish_led_state(self, is_on):
        if self.client and self.connected:
            try:
                self.client.publish(
                    self.base_topic + "/ha/leds/state",
                    "ON" if is_on else "OFF",
                    retain=True
                )
            except Exception as e:
                print("LED state publish failed:", e)

    def publish_brightness_state(self, value):
        if self.client and self.connected:
            try:
                self.client.publish(
                    self.base_topic + "/ha/brightness/state",
                    str(value),
                    retain=True
                )
            except Exception as e:
                print("Brightness state publish failed:", e)

    def reconnect(self):
        now = time.time()

        if now - self.last_reconnect < 5:
            return False

        self.last_reconnect = now
        print("MQTT reconnecting...")

        try:
            if self.client:
                try:
                    self.client.disconnect()
                except Exception:
                    pass

            self.client = None
            self.connected = False
            return self.connect()

        except Exception as e:
            print("MQTT reconnect failed:", e)
            self.connected = False
            return False

    def _handle_message(self, topic, msg):
        try:
            topic = topic.decode("utf-8")
            payload = msg.decode("utf-8")

            if topic == self.base_topic + "/state":
                data = json.loads(payload)
                if self.on_state:
                    self.on_state(data)

            elif topic == self.base_topic + "/ha/display/set":
                if self.on_display:
                    self.on_display(payload == "ON")

            elif topic == self.base_topic + "/ha/leds/set":
                if self.on_leds:
                    self.on_leds(payload == "ON")
                    
            elif topic == self.base_topic + "/ha/brightness/set":
                if self.on_brightness:
                    self.on_brightness(int(payload))

        except Exception as e:
            print("MQTT message error:", e)

    def check_msg(self):
        if not self.client:
            return

        try:
            self.client.check_msg()
        except Exception as e:
            self.connected = False
            print("MQTT check_msg failed:", e)
            self.reconnect()

    def publish_command(self, command, payload=""):
        if not self.client or not self.connected:
            print("MQTT not connected, reconnecting before publish")
            self.reconnect()

        if not self.client or not self.connected:
            print("MQTT publish skipped, still not connected")
            return

        topic = self.base_topic + "/cmd/" + command

        try:
            self.client.publish(topic, payload)
            print("Published " + topic)

        except Exception as e:
            self.connected = False
            print("MQTT publish failed:", e)
            self.reconnect()

    def disconnect(self):
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass

        self.connected = False

