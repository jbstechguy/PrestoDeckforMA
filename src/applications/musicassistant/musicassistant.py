import gc
import time
import jpegdec
import pngdec
import uasyncio as asyncio
import urequests as requests

from touch import Button
from base import BaseApp
import secrets

from applications.musicassistant.mqtt_client import MusicAssistantMQTTClient


class State:
    def __init__(self):
        self.toggle_leds = True
        self.is_playing = False
        self.repeat = False
        self.shuffle = False
        self.track = None
        self.show_controls = False
        self.exit = False
        self.display_on = True

        # Display modes:
        # 1 = artwork + title/artist bottom
        # 2 = controls + title/artist above controls
        # 3 = artwork only
        self.display_mode = 1

        self.last_active_time = time.time()
        self.auto_sleep_seconds = 300

        self.marquee_offset = 0
        self.last_marquee_time = time.time()

    def copy(self):
        state = State()
        state.toggle_leds = self.toggle_leds
        state.is_playing = self.is_playing
        state.repeat = self.repeat
        state.shuffle = self.shuffle
        state.show_controls = self.show_controls
        state.exit = self.exit
        state.display_on = self.display_on
        state.display_mode = self.display_mode
        state.track = {"id": self.track["id"]} if self.track else None
        return state

    def __eq__(self, other):
        if not isinstance(other, State) or other is None:
            return False

        return (
            self.toggle_leds == other.toggle_leds and
            self.is_playing == other.is_playing and
            self.repeat == other.repeat and
            self.shuffle == other.shuffle and
            self.show_controls == other.show_controls and
            self.exit == other.exit and
            self.display_on == other.display_on and
            self.display_mode == other.display_mode and
            (self.track or {}).get("id") == (other.track or {}).get("id")
        )


class ControlButton:
    def __init__(self, display, name, icons, bounds, on_press=None, update=None):
        self.name = name
        self.enabled = False
        self.icon = icons[0] if icons else None
        self.pngs = {}

        if icons:
            for icon in icons:
                png = pngdec.PNG(display)
                png.open_file("applications/musicassistant/icons/" + icon)
                self.pngs[icon] = png

        self.button = Button(*bounds)
        self.on_press = on_press
        self.update = update

    def is_pressed(self, state):
        return self.enabled and self.button.is_pressed()

    def draw(self, state):
        if self.enabled and self.icon:
            self.draw_icon()

    def draw_icon(self):
        png = self.pngs[self.icon]
        x, y, width, height = self.button.bounds
        png_width, png_height = png.get_width(), png.get_height()
        x_offset = (width - png_width) // 2
        y_offset = (height - png_height) // 2
        png.decode(x + x_offset, y + y_offset)


class MusicAssistant(BaseApp):
    def __init__(self):
        super().__init__(ambient_light=True, full_res=True, layers=2)

        self.display.set_layer(0)
        icon = pngdec.PNG(self.display)
        icon.open_file("applications/musicassistant/icon.png")
        icon.decode(
            self.center_x - icon.get_width() // 2,
            self.center_y - icon.get_height() // 2 - 20
        )
        self.presto.update()

        self.display.set_font("sans")
        self.display.set_layer(1)
        self.display_text("Connecting to WIFI", (90, self.height - 80), thickness=2)

        self.presto.connect()
        while not self.presto.wifi.isconnected():
            self.clear(1)
            self.display_text("Failed to connect to WIFI", (40, self.height - 80), thickness=2)
            time.sleep(2)

        self.j = jpegdec.JPEG(self.display)
        self.state = State()
        self.mqtt = None

        self.clear(1)
        self.display_text("Connecting to MA", (95, self.height - 80), thickness=2)

        self.setup_mqtt()
        self.setup_buttons()

        self.clear(1)
        self.presto.update()

    def display_text(self, text, position, color=65535, scale=1, thickness=None):
        if thickness:
            self.display.set_thickness(2)

        x, y = position
        self.display.set_pen(color)
        self.display.text(text, x, y, scale=scale)
        self.presto.update()

    def setup_mqtt(self):
        broker = getattr(secrets, "MQTT_BROKER", None)

        if not broker:
            print("MQTT_BROKER missing in secrets.py")
            return

        self.mqtt = MusicAssistantMQTTClient(
            broker=broker,
            port=getattr(secrets, "MQTT_PORT", 1883),
            username=getattr(secrets, "MQTT_USERNAME", None),
            password=getattr(secrets, "MQTT_PASSWORD", None),
            client_id=getattr(secrets, "MQTT_DEVICE_NAME", "prestodeck"),
            base_topic=getattr(secrets, "MQTT_BASE_TOPIC", "prestodeck")
        )

        self.mqtt.on_state = self.handle_musicassistant_state
        self.mqtt.on_display = self.handle_display_command
        self.mqtt.on_leds = self.handle_led_command

        try:
            self.mqtt.connect()
        except Exception as e:
            print("MQTT setup error:", e)
            self.mqtt = None

    def handle_musicassistant_state(self, payload):
        title = payload.get("title", "Unknown Track")
        artist = payload.get("artist", "Unknown Artist")
        album = payload.get("album", "")
        image = payload.get("image", "")
        track_id = payload.get("id", title + "-" + artist)

        self.state.track = {
            "id": track_id,
            "name": title,
            "artists": [{"name": artist}],
            "album": {
                "name": album,
                "images": [
                    {},
                    {"url": image}
                ]
            }
        }

        was_playing = self.state.is_playing
        self.state.is_playing = payload.get("playing", False)
        self.state.shuffle = payload.get("shuffle", False)
        self.state.repeat = payload.get("repeat", False)

        self.state.marquee_offset = 0
        self.state.last_marquee_time = time.time()

        if self.state.is_playing:
            self.state.last_active_time = time.time()

            if not self.state.display_on:
                print("Auto wake: music started")
                self.state.display_on = True
                self.presto.set_backlight(1.0)

                if self.mqtt:
                    self.mqtt.publish_display_state(self.state.display_on)

        if was_playing and not self.state.is_playing:
            self.state.last_active_time = time.time()

        print("Now playing:", title, "-", artist)

    def handle_display_command(self, state):
        self.state.display_on = state

        if state:
            print("HA Display ON")
            self.presto.set_backlight(1.0)
            self.state.last_active_time = time.time()
        else:
            print("HA Display OFF")
            self.presto.set_backlight(0)

        if self.mqtt:
            self.mqtt.publish_display_state(self.state.display_on)

    def handle_led_command(self, state):
        print("HA LEDs", "ON" if state else "OFF")

        self.toggle_leds(state)
        self.state.toggle_leds = state
        self.state.last_active_time = time.time()

        if self.mqtt:
            self.mqtt.publish_led_state(self.state.toggle_leds)

    def setup_buttons(self):
        def update_controls_only(state, button):
            button.enabled = state.display_mode == 2

        def update_always_enabled(state, button):
            button.enabled = True

        def update_play_pause(state, button):
            button.enabled = state.display_mode == 2
            button.icon = "pause.png" if state.is_playing else "play.png"

        def update_shuffle(state, button):
            button.enabled = state.display_mode == 2
            button.icon = "shuffle_on.png" if state.shuffle else "shuffle_off.png"

        def update_repeat(state, button):
            button.enabled = state.display_mode == 2
            button.icon = "repeat_on.png" if state.repeat else "repeat_off.png"

        def update_light(state, button):
            button.enabled = state.display_mode == 2
            button.icon = "light_on.png" if state.toggle_leds else "light_off.png"

        def exit_app(self):
            self.state.display_on = not self.state.display_on

            if self.state.display_on:
                print("Display ON")
                self.presto.set_backlight(1.0)
                self.state.last_active_time = time.time()
            else:
                print("Display OFF")
                self.presto.set_backlight(0)

            if self.mqtt:
                self.mqtt.publish_display_state(self.state.display_on)

        def toggle_display_mode(self):
            if self.state.display_mode == 1:
                self.state.display_mode = 2
            elif self.state.display_mode == 2:
                self.state.display_mode = 1
            else:
                self.state.display_mode = 1

            self.state.show_controls = self.state.display_mode == 2
            self.state.last_active_time = time.time()
            print("Display mode:", self.state.display_mode)

        def play_pause(self):
            self.publish_command("playpause")
            self.state.is_playing = not self.state.is_playing
            self.state.last_active_time = time.time()

        def next_track(self):
            self.publish_command("next")
            self.state.last_active_time = time.time()

        def previous_track(self):
            self.publish_command("previous")
            self.state.last_active_time = time.time()

        def toggle_shuffle(self):
            self.state.shuffle = not self.state.shuffle
            self.publish_command("shuffle", "on" if self.state.shuffle else "off")
            self.state.last_active_time = time.time()

        def toggle_repeat(self):
            self.state.repeat = not self.state.repeat
            self.publish_command("repeat", "on" if self.state.repeat else "off")
            self.state.last_active_time = time.time()

        def toggle_lights(self):
            self.toggle_leds(not self.state.toggle_leds)
            self.state.toggle_leds = not self.state.toggle_leds
            self.state.last_active_time = time.time()

            if self.mqtt:
                self.mqtt.publish_led_state(self.state.toggle_leds)

        buttons_config = [
            ("Exit", ["exit.png"], (0, 0, 80, 80), exit_app, update_controls_only),
            ("Next", ["next.png"], (self.center_x + 60, self.height - 100, 80, 100), next_track, update_controls_only),
            ("Previous", ["previous.png"], (self.center_x - 140, self.height - 100, 80, 100), previous_track, update_controls_only),
            ("Play", ["play.png", "pause.png"], (self.center_x - 50, self.height - 100, 80, 100), play_pause, update_play_pause),
            ("Toggle Shuffle", ["shuffle_on.png", "shuffle_off.png"], (self.center_x - 230, self.height - 100, 80, 100), toggle_shuffle, update_shuffle),
            ("Toggle Repeat", ["repeat_on.png", "repeat_off.png"], (self.center_x + 150, self.height - 100, 80, 100), toggle_repeat, update_repeat),
            ("Toggle Light", ["light_on.png", "light_off.png"], (self.width - 100, 0, 100, 80), toggle_lights, update_light),
            ("Toggle Controls", None, (0, 0, self.width, self.height), toggle_display_mode, update_always_enabled),
        ]

        self.buttons = [
            ControlButton(self.display, name, icons, bounds, on_press, update)
            for name, icons, bounds, on_press, update in buttons_config
        ]

    def publish_command(self, command, payload=""):
        if self.mqtt:
            self.mqtt.publish_command(command, payload)
        else:
            print("MQTT not connected")

    def run(self):
        loop = asyncio.get_event_loop()
        loop.create_task(self.mqtt_loop())
        loop.create_task(self.touch_handler_loop())
        loop.create_task(self.display_loop())
        loop.run_forever()

    async def mqtt_loop(self):
        while not self.state.exit:
            if self.mqtt:
                self.mqtt.check_msg()
            await asyncio.sleep_ms(500)

    async def touch_handler_loop(self):
        while not self.state.exit:
            self.touch.poll()

            if not self.state.display_on and self.touch.state:
                self.state.display_on = True
                self.state.last_active_time = time.time()
                self.presto.set_backlight(1.0)

                if self.mqtt:
                    self.mqtt.publish_display_state(self.state.display_on)

                while self.touch.state:
                    self.touch.poll()

                await asyncio.sleep_ms(250)
                continue

            if self.touch.state:
                # First, if controls are visible, check actual control buttons.
                if self.state.display_mode == 2:
                    command_pressed = False

                    for button in self.buttons:
                        if button.name == "Toggle Controls":
                            continue

                        button.update(self.state, button)

                        if button.is_pressed(self.state):
                            print(button.name, "pressed")
                            command_pressed = True
                            try:
                                button.on_press(self)
                            except Exception as e:
                                print("Button error:", e)
                            break

                    while self.touch.state:
                        self.touch.poll()

                    if command_pressed:
                        await asyncio.sleep_ms(250)
                        continue

                # If no command button was pressed, treat it as screen mode control.
                touch_start = time.time()
                long_press_triggered = False

                while self.touch.state:
                    self.touch.poll()

                    if not long_press_triggered and time.time() - touch_start > 0.8:
                        print("Long press: cover only mode")
                        self.state.display_mode = 3
                        self.state.show_controls = False
                        self.state.last_active_time = time.time()
                        long_press_triggered = True

                if not long_press_triggered:
                    if self.state.display_mode == 1:
                        self.state.display_mode = 2
                    elif self.state.display_mode == 2:
                        self.state.display_mode = 1
                    else:
                        self.state.display_mode = 1

                    self.state.show_controls = self.state.display_mode == 2
                    self.state.last_active_time = time.time()
                    print("Display mode:", self.state.display_mode)

                await asyncio.sleep_ms(250)

            await asyncio.sleep_ms(50)


    def show_image(self, img):
        if not img:
            return

        try:
            self.j.open_RAM(memoryview(img))

            img_width = self.j.get_width()
            img_height = self.j.get_height()
            img_x = (self.width - img_width) // 2
            img_y = (self.height - img_height) // 2

            self.clear(0)
            self.j.decode(img_x, img_y, jpegdec.JPEG_SCALE_FULL, dither=True)

        except Exception as e:
            print("Failed to load image:", e)

    def write_track(self):
        if self.state.track and self.state.display_mode in (1, 2):
            self.display.set_thickness(3)

            track_name = clean_text(self.state.track.get("name", ""))
            artists = ", ".join([artist.get("name") for artist in self.state.track.get("artists", [])])
            artists = clean_text(artists)

            max_title_chars = 20
            max_artist_chars = 35

            now = time.time()
            if len(track_name) > max_title_chars and now - self.state.last_marquee_time > 0.10:
                self.state.marquee_offset += 1
                if self.state.marquee_offset > len(track_name) + 4:
                    self.state.marquee_offset = 0
                self.state.last_marquee_time = now

            if len(track_name) > max_title_chars:
                padded_title = track_name + "    "
                start = self.state.marquee_offset
                display_title = (padded_title + padded_title)[start:start + max_title_chars]
            else:
                display_title = track_name

            if len(artists) > max_artist_chars:
                artists = artists[:max_artist_chars] + " ..."

            if self.state.display_mode == 1:
                title_y = self.height - 70
                artist_y = self.height - 42
            else:
                title_y = self.height - 137
                artist_y = self.height - 108

            self.display.set_pen(self.colors._BLACK)
            self.display.text(display_title, 20, title_y + 3, scale=1.1)

            self.display.set_pen(self.colors.WHITE)
            self.display.text(display_title, 18, title_y, scale=1.1)

            self.display.set_thickness(2)

            self.display.set_pen(self.colors._BLACK)
            self.display.text(artists, 20, artist_y + 3, scale=0.7)

            self.display.set_pen(self.colors.WHITE)
            self.display.text(artists, 18, artist_y, scale=0.7)

    async def display_loop(self):
        prev_state = None
        prev_track_id = None

        while not self.state.exit:
            if (
                self.state.display_on and
                not self.state.is_playing and
                time.time() - self.state.last_active_time > self.state.auto_sleep_seconds
            ):
                print("Auto sleep: idle")
                self.state.display_on = False
                self.presto.set_backlight(0)

                if self.mqtt:
                    self.mqtt.publish_display_state(self.state.display_on)

            if self.state.track:
                current_track_id = self.state.track.get("id")

                if current_track_id != prev_track_id:
                    img = get_album_cover(self.state.track)
                    self.show_image(img)
                    prev_track_id = current_track_id

                should_redraw = prev_state != self.state

                if self.state.display_mode in (1, 2):
                    track_name = clean_text(self.state.track.get("name", ""))
                    if len(track_name) > 20:
                        should_redraw = True

                if should_redraw:
                    self.clear(1)

                    for button in self.buttons:
                        button.update(self.state, button)
                        button.draw(self.state)

                    self.write_track()
                    self.presto.update()
                    prev_state = self.state.copy()

            gc.collect()
            await asyncio.sleep_ms(200)


def clean_text(text):
    replacements = {
        "á": "a", "à": "a", "ä": "a", "â": "a",
        "Á": "A", "À": "A", "Ä": "A", "Â": "A",
        "é": "e", "è": "e", "ë": "e", "ê": "e",
        "É": "E", "È": "E", "Ë": "E", "Ê": "E",
        "í": "i", "ì": "i", "ï": "i", "î": "i",
        "Í": "I", "Ì": "I", "Ï": "I", "Î": "I",
        "ó": "o", "ò": "o", "ö": "o", "ô": "o",
        "Ó": "O", "Ò": "O", "Ö": "O", "Ô": "O",
        "ú": "u", "ù": "u", "ü": "u", "û": "u",
        "Ú": "U", "Ù": "U", "Ü": "U", "Û": "U",
        "ñ": "n", "Ñ": "N",
        "ç": "c", "Ç": "C",
        "“": "\"", "”": "\"",
        "‘": "'", "’": "'",
        "×": "x",
        "–": "-", "—": "-",
        "…": "...",
    }

    cleaned = ""
    for char in text:
        cleaned += replacements.get(char, char if ord(char) < 128 else "")

    return cleaned


def get_album_cover(track):
    img_url = track["album"]["images"][1]["url"]

    if not img_url:
        return None

    try:
        response = requests.get(img_url)

        if response.status_code == 200:
            img = response.content
            response.close()
            return img

        print("Failed to fetch image:", response.status_code)
        response.close()

    except Exception as e:
        print("Image fetch error:", e)

    return None


def launch():
    app = MusicAssistant()
    app.run()

    if app.mqtt:
        app.mqtt.disconnect()

    app.clear()
    del app
    gc.collect()

