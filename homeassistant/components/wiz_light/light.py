"""WiZ Light integration."""
from datetime import timedelta
import logging

from pywizlight import SCENES, PilotBuilder, wizlight
from pywizlight.exceptions import (
    WizLightConnectionError,
    WizLightNotKnownBulb,
    WizLightTimeOutError,
)
import voluptuous as vol

# Import the device class from the component
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    PLATFORM_SCHEMA,
    SUPPORT_BRIGHTNESS,
    SUPPORT_COLOR,
    SUPPORT_COLOR_TEMP,
    SUPPORT_EFFECT,
    LightEntity,
)
from homeassistant.const import CONF_HOST, CONF_NAME
import homeassistant.helpers.config_validation as cv
from homeassistant.util import slugify
import homeassistant.util.color as color_utils

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SUPPORT_FEATURES_RGB = (
    SUPPORT_BRIGHTNESS | SUPPORT_COLOR | SUPPORT_COLOR_TEMP | SUPPORT_EFFECT
)
SUPPORT_FEATURES_DIM = SUPPORT_BRIGHTNESS
SUPPORT_FEATURES_WHITE = SUPPORT_BRIGHTNESS | SUPPORT_COLOR_TEMP

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {vol.Required(CONF_HOST): cv.string, vol.Required(CONF_NAME): cv.string}
)

# set poll interval to 15 sec because of changes from external to the bulb
SCAN_INTERVAL = timedelta(seconds=15)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the WiZ Light platform from legacy config."""
    # Assign configuration variables.
    # The configuration check takes care they are present.
    ip_address = config[CONF_HOST]
    try:
        bulb = wizlight(ip_address)
        # Add devices
        async_add_entities([WizBulb(bulb, config[CONF_NAME])], update_before_add=True)
    except WizLightConnectionError:
        _LOGGER.error("Can't add bulb with ip %s.", ip_address)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the WiZ Light platform from config_flow."""
    # Assign configuration variables.
    bulb = hass.data[DOMAIN][entry.unique_id]
    wizbulb = WizBulb(bulb, entry.data.get(CONF_NAME))
    # Add devices with defined name
    async_add_entities([wizbulb], update_before_add=True)

    # Register services
    async def async_update(call=None):
        """Trigger update."""
        _LOGGER.debug("[wizlight %s] update requested", entry.data.get(CONF_HOST))
        await wizbulb.async_update()
        await wizbulb.async_update_ha_state()

    service_name = slugify(f"{entry.data.get(CONF_NAME)} updateService")
    hass.services.async_register(DOMAIN, service_name, async_update)


class WizBulb(LightEntity):
    """Representation of WiZ Light bulb."""

    def __init__(self, light: wizlight, name):
        """Initialize an WiZLight."""
        self._light = light
        self._state = None
        self._brightness = None
        self._name = name
        self._rgb_color = None
        self._temperature = None
        self._hscolor = None
        self._available = None
        self._effect = None
        self._scenes = []
        self._bulbtype = None
        self._mac = None

    @property
    def brightness(self):
        """Return the brightness of the light."""
        return self._brightness

    @property
    def rgb_color(self):
        """Return the color property."""
        return self._rgb_color

    @property
    def hs_color(self):
        """Return the hs color value."""
        return self._hscolor

    @property
    def name(self):
        """Return the ip as name of the device if any."""
        return self._name

    @property
    def unique_id(self):
        """Return light unique_id."""
        return self._mac

    @property
    def is_on(self):
        """Return true if light is on."""
        return self._state

    async def async_turn_on(self, **kwargs):
        """Instruct the light to turn on."""
        rgb = None
        if ATTR_RGB_COLOR in kwargs:
            rgb = kwargs.get(ATTR_RGB_COLOR)
        if ATTR_HS_COLOR in kwargs:
            rgb = color_utils.color_hs_to_RGB(
                kwargs[ATTR_HS_COLOR][0], kwargs[ATTR_HS_COLOR][1]
            )

        brightness = None
        if ATTR_BRIGHTNESS in kwargs:
            brightness = kwargs.get(ATTR_BRIGHTNESS)

        colortemp = None
        if ATTR_COLOR_TEMP in kwargs:
            kelvin = color_utils.color_temperature_mired_to_kelvin(
                kwargs[ATTR_COLOR_TEMP]
            )
            colortemp = kelvin
            _LOGGER.debug(
                "[wizlight %s] kelvin changed and send to bulb: %s",
                self._light.ip,
                colortemp,
            )

        sceneid = None
        if ATTR_EFFECT in kwargs:
            sceneid = self._light.get_id_from_scene_name(kwargs[ATTR_EFFECT])

        if sceneid == 1000:  # rhythm
            pilot = PilotBuilder()
        else:
            pilot = PilotBuilder(
                rgb=rgb, brightness=brightness, colortemp=colortemp, scene=sceneid
            )
        await self._light.turn_on(pilot)

    async def async_turn_off(self, **kwargs):
        """Instruct the light to turn off."""
        await self._light.turn_off()

    @property
    def color_temp(self):
        """Return the CT color value in mireds."""
        return self._temperature

    @property
    def min_mireds(self):
        """Return the coldest color_temp that this light supports."""
        if self._bulbtype:
            return self.kelvin_max_map()
        # fallback
        return color_utils.color_temperature_kelvin_to_mired(6500)

    @property
    def max_mireds(self):
        """Return the warmest color_temp that this light supports."""
        if self._bulbtype:
            return self.kelvin_min_map()
        # fallback
        return color_utils.color_temperature_kelvin_to_mired(2500)

    @property
    def supported_features(self) -> int:
        """Flag supported features."""
        if self._bulbtype:
            return self.featuremap()
        # fallback
        return SUPPORT_BRIGHTNESS | SUPPORT_COLOR | SUPPORT_COLOR_TEMP | SUPPORT_EFFECT

    @property
    def effect(self):
        """Return the current effect."""
        return self._effect

    @property
    def effect_list(self):
        """Return the list of supported effects."""
        if self._bulbtype:
            # Special filament bulb type
            if self._bulbtype.name == "ESP56_SHTW3_01":
                return [self._scenes[key] for key in [8, 9, 14, 15, 17, 28, 29, 31]]
            # Filament bulb without white color led
            if self._bulbtype.name == "ESP06_SHDW9_01":
                return [self._scenes[key] for key in [8, 9, 13, 28, 30, 29, 31]]
            # Filament bulb ST64
            if self._bulbtype.name == "ESP06_SHDW1_01":
                return [self._scenes[key] for key in [8, 9, 13, 28, 29, 31]]
            if self._bulbtype.name == "ESP15_SHTW1_01I":
                return [
                    self._scenes[key]
                    for key in [5, 8, 9, 10, 11, 12, 13, 14, 15, 17, 28, 30, 29, 31]
                ]
            return self._scenes
        return []

    @property
    def available(self):
        """Return if light is available."""
        return self._available

    async def async_update(self):
        """Fetch new state data for this light."""
        await self.update_state()
        await self.get_bulb_type()
        await self.get_mac()

        if self._state is not None and self._state is not False:
            self.update_brightness()
            self.update_temperature()
            self.update_color()
            self.update_effect()
            self.update_scene_list()

    @property
    def device_info(self):
        """Get device specific attributes."""
        _LOGGER.debug(
            "[wizlight %s] Call device info: MAC: %s - Name: %s - Type: %s",
            self._light.ip,
            self._mac,
            self._name,
            self._bulbtype.name,
        )
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": self._name,
            "manufacturer": "WiZ Light Platform",
            "model": self._bulbtype.name,
        }

    async def update_state_available(self):
        """Update the state if bulb is available."""
        self._state = self._light.status
        self._available = True

    async def update_state_unavailable(self):
        """Update the state if bulb is unavailable."""
        self._state = False
        self._available = False

    async def update_state(self):
        """Update the state."""
        try:
            await self._light.updateState()
            if self._light.state is None:
                _LOGGER.debug(
                    "[wizlight %s] state unavailable: %s", self._light.ip, self._state
                )
                await self.update_state_unavailable()
            else:
                await self.update_state_available()
        except WizLightTimeOutError as ex:
            _LOGGER.debug(ex)
            await self.update_state_unavailable()
        _LOGGER.debug("[wizlight %s] updated state: %s", self._light.ip, self._state)

    def update_brightness(self):
        """Update the brightness."""
        if self._light.state.get_brightness() is None:
            return
        try:
            brightness = self._light.state.get_brightness()
            if 0 <= int(brightness) <= 255:
                self._brightness = int(brightness)
            else:
                _LOGGER.error(
                    "Received invalid brightness : %s. Expected: 0-255", brightness
                )
                self._brightness = None
        # pylint: disable=broad-except
        except Exception as ex:
            _LOGGER.error(ex)
            self._state = None

    def update_temperature(self):
        """Update the temperature."""
        colortemp = self._light.state.get_colortemp()
        if colortemp is None or colortemp == 0:
            return
        try:
            _LOGGER.debug(
                "[wizlight %s] kelvin from the bulb: %s", self._light.ip, colortemp
            )
            temperature = color_utils.color_temperature_kelvin_to_mired(colortemp)
            self._temperature = temperature

        # pylint: disable=broad-except
        except Exception:
            _LOGGER.error("Cannot evaluate temperature", exc_info=True)
            self._temperature = None

    def update_color(self):
        """Update the hs color."""
        if self._light.state.get_rgb() is None:
            return
        try:
            red, green, blue = self._light.state.get_rgb()
            if red is None:
                # this is the case if the temperature was changed - no information was return form the lamp.
                # do nothing until the RGB color was changed
                return
            color = color_utils.color_RGB_to_hs(red, green, blue)
            if color is not None:
                self._hscolor = color
            else:
                _LOGGER.error("Received invalid HS color : %s", color)
                self._hscolor = None
        # pylint: disable=broad-except
        except Exception:
            _LOGGER.error("Cannot evaluate color", exc_info=True)
            self._hscolor = None

    def update_effect(self):
        """Update the bulb scene."""
        self._effect = self._light.state.get_scene()

    async def get_bulb_type(self):
        """Get the bulb type."""
        if self._bulbtype is None:
            try:
                self._bulbtype = await self._light.get_bulbtype()
                _LOGGER.info(
                    "[wizlight %s] Initiate the WiZ bulb as %s",
                    self._light.ip,
                    self._bulbtype.name,
                )
            except WizLightTimeOutError:
                _LOGGER.debug(
                    "[wizlight %s] Bulbtype update failed - Timeout", self._light.ip
                )

    def update_scene_list(self):
        """Update the scene list."""
        self._scenes = []
        for number in SCENES:
            self._scenes.append(SCENES[number])

    async def get_mac(self):
        """Get the mac from the bulb."""
        try:
            self._mac = await self._light.getMac()
        except WizLightTimeOutError:
            _LOGGER.debug("[wizlight %s] Mac update failed - Timeout", self._light.ip)

    def featuremap(self):
        """Map the features from WizLight Class."""
        features = 0
        try:
            # Map features for better reading
            if self._bulbtype.features.brightness:
                features = features | SUPPORT_BRIGHTNESS
            if self._bulbtype.features.color:
                features = features | SUPPORT_COLOR
            if self._bulbtype.features.effect:
                features = features | SUPPORT_EFFECT
            if self._bulbtype.features.color_tmp:
                features = features | SUPPORT_COLOR_TEMP
            return features
        except WizLightNotKnownBulb:
            _LOGGER.info(
                "Bulb is not present in the library. Fallback to full feature."
            )
            return (
                SUPPORT_BRIGHTNESS | SUPPORT_COLOR | SUPPORT_COLOR_TEMP | SUPPORT_EFFECT
            )

    def kelvin_max_map(self):
        """Map the maximum kelvin from YAML."""
        # Map features for better reading
        try:
            kelvin = color_utils.color_temperature_kelvin_to_mired(
                self._bulbtype.kelvin_range.max
            )
            return kelvin
        except WizLightNotKnownBulb:
            _LOGGER.info("Kelvin is not present in the library. Fallback to 6500")
            return 6500

    def kelvin_min_map(self):
        """Map the minimum kelvin from YAML."""
        # Map features for better reading
        try:
            return color_utils.color_temperature_kelvin_to_mired(
                self._bulbtype.kelvin_range.min
            )
        except WizLightNotKnownBulb:
            _LOGGER.info("Kelvin is not present in the library. Fallback to 2500")
            return 2500
