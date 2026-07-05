#/usr/bin/python

import kivy
from kivy import platform
from kivy.clock import Clock, mainthread
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.properties import (ObjectProperty, BooleanProperty, NumericProperty,
							StringProperty, OptionProperty, ListProperty,
							DictProperty, ReferenceListProperty, AliasProperty)
from kivy.uix.behaviors.button import ButtonBehavior
from kivy.logger import Logger
from kivy.metrics import sp
from kivy.animation import Animation
from CommonHidsBase import CommonHidsBase

#install_twisted_rector must be called before importing the reactor
from kivy.support import install_twisted_reactor
install_twisted_reactor()
Logger.info("Control: Twisted reactor installed")

from IconButton import IconButton, IconButtonText
from BackgroundedBehaviour import BackgroundedBehaviour
from OverlayBehaviour import OverlayBehaviour
from OverlayButtonsLayout import OverlayButtonsLayout
# from LonLatPositionPopup import CoordinatesInputPopup
from RoverStatusOverlayLayout import RoverStatusOverlayLayout
from TurretStatusOverlayLayout import TurretStatusOverlayLayout
from ScopeGrid import Scope, AngleSystems, DistanceSystems
from TargetBox import TargetsBox
from GuideLine import GuideLine
from FollowMe import FollowMe
# from FollowMeBox import FollowMeBox
from FixedAspectRatioBehaviour import FixedAspectRatioLayout
from RoverSchema import RoverSchema
from Compass import Compass
from AnimatedHintLabel import AnimatedHintLabel
from Slider2DAutoZero import Slider2DAutoZero
from SliderAutoZero import SliderAutoZero
from VideoMontage import VideoMontage, check_cam_in_group
from KeyHandlerBehaviour import KeyHandlerBehaviour
from InputPopup import InputPopup
from ShortcutSelectorPopup import ShortcutSelectorPopup
from GpsLogger import GpsLogger
from BallisticCalc import BallisticCalc, BalisticTables_CLS
from TurretSchema import TurretSchema as RwsSchema
from MotionDetector import MotionDetector

try:
	from MapPanel import MapPanel
except:
	MapPanel = None

try:
	from MapPanel import MapPanel
except:
	MapPanel = None

from multilingual import _

import tools.control as control
import tools.rssi_rocket as rssi_rocket
from tools.onvif import Camera
from tools.utils import run_in_thread, ignore_exceptions
from tools.external_rangefinder_comm import ExternalRangefinderCommunicator
from threading import Thread
import sys, os.path, math

if sys.version_info[0] == 2:
	str = basestring
# from BallisticCalc import BallisticCalc

if platform == 'android':
	from osandroid.orientation import HardwareRotation
else:
	HardwareRotation = None
try:
	from plyer import vibrator
	has_vibrator = vibrator.exists()
except:
	has_vibrator = False

if platform == 'linux':
	from oslinux.hw_brightness import HardwareBrightness
	if HardwareBrightness.GetDevices():
		HardwareBrightness().brightness = 0.7
	else:
		HardwareBrightness = None
else:
	HardwareBrightness = None

if platform == 'linux':
	from oslinux.hw_battery import HardwareBattery
	if not HardwareBattery.GetDevices():
		HardwareBattery = None
elif platform == 'win':
	from oswindows.hw_battery import HardwareBattery
else:
	HardwareBattery = None

if platform == 'linux':
	from oslinux.hw_volume import HardwareVolume
else:
	HardwareVolume = None

if platform == 'linux':
	from VideoRecorder import VideoRecorder, VIDEO_RECORDER_INDICATOR, RECORDING_EVENTS
else:
	VideoRecorder = None

def samesign(x, y):
	return (x >= 0 and y >= 0) or (x <= 0 and y <= 0)

def limit(x, top=1.0, unidirect=False):
	return min(top, max(0.0 if unidirect else -top, x))

def norm_angle(a):
	a = a % (math.pi * 2)
	return a if a <= math.pi else a - math.pi * 2

def norm_angle_uni(a):
	a = a % (math.pi * 2)
	return a if a <= math.pi * 2 else a + math.pi * 2

class ControlLayout(KeyHandlerBehaviour, BackgroundedBehaviour, FloatLayout):
	VIDEO_QUALITIES = ['hd', 'sd', 'ld']
	DRIVE_CAM_GROUPS = ['front', 'rear', 'left', 'right']
	SIGHT_CAM_GROUPS = ['wide', 'tele', 'thermo']
	BUTTON_ICONS = {
		'motion_enable': 'enable',
		'axisunit_enable': 'enable',
		'motion_slow': 'slow',
		'axisunit_slow': 'slow',
		'drive_menu': 'menu_settings',
		'target_menu': 'menu_settings',
		'steer_mode': 'steermode',
		'sight_zoom_in': 'zoomin',
		'sight_zoom_out': 'zoomout',
		'cams_quality': 'video',
		'drive_cams_quality': 'video',
		'sight_cams_quality': 'video',
		'cams_pip': 'video_pip',
		'drive_cams_pip': 'video_pip',
		'sight_cams_pip': 'video_pip',
		'cams_night': 'night',
		'drive_cams_night': 'night',
		'sight_cams_night_req': 'night',
		'drive_brightness_more': 'brightness_more',
		'sight_brightness_more': 'brightness_more',
		'drive_brightness_less': 'brightness_less',
		'sight_brightness_less': 'brightness_less',
		'drive_contrast_more': 'contrast_more',
		'sight_contrast_more': 'contrast_more',
		'drive_contrast_less': 'contrast_less',
		'sight_contrast_less': 'contrast_less',
		'drive_cams_reset': 'video_reset',
		'sight_cams_reset': 'video_reset',
		'cams_reset': 'video_reset',
		'cams_loop': 'video_loop',
		'drive_cams_loop': 'video_loop',
		'sight_cams_loop': 'video_loop',
		'sight_cams_thermo': 'video_loop',
		'master_volume_level': 'volume',
		} | MotionDetector.BUTTON_ICONS | ( VideoRecorder.BUTTON_ICONS if VideoRecorder else {} )
	control_comm = ObjectProperty(allownone=True)
	outpost_comm = ObjectProperty(allownone=True)
	local_waypoints = ListProperty()
	preset = OptionProperty('dual', options=['drive', 'target', 'dual'])
	displays = NumericProperty(1)
	portrait = BooleanProperty(True)
	allow_slider2d = BooleanProperty(True)
	gsensor = ObjectProperty(None, allownone=True)
	joystickhids = ObjectProperty(None, allownone=True)
	serialhids = ObjectProperty(None, allownone=True)
	throttle_mode = OptionProperty('vel_bi', options=['vel_bi', 'vel_uni', 'acc'])
	steering_mode = OptionProperty('dir', options=['dir', 'acc'])
	direction_lock = BooleanProperty(False)
	active = BooleanProperty(False)
	throttle = NumericProperty(0.0)
	brake = NumericProperty(0.0)
	throttle_out = NumericProperty(0.0)
	throttle_allowed_direction = NumericProperty(0.0)
	steering = NumericProperty(0.0)
	steering_out = NumericProperty(0.0)
	rotation = NumericProperty(0.0)
	elevation = NumericProperty(0.0)
	rotation_slow = NumericProperty(0.0)
	elevation_slow = NumericProperty(0.0)
	motion_slow = BooleanProperty(True)
	axisunit_slow = BooleanProperty(False)
	axisunit_center = BooleanProperty(False)
	axisunit_force_home = BooleanProperty(False)
	reverse = BooleanProperty(False)
	engine_on = BooleanProperty(False)
	engine_start = BooleanProperty(False)
	motion_enable = BooleanProperty(False)
	motion_enable_delayed = BooleanProperty(False)
	axisunit_enable = BooleanProperty(False)
	armed = BooleanProperty(False)
	armed_req = BooleanProperty(False)
	fire = BooleanProperty(False)
	fire_req = BooleanProperty(False)
	fire_burst = NumericProperty(1)
	burst_duration = NumericProperty(0)
	fire_seq = NumericProperty(0)
	lights_on = BooleanProperty(True)
	headlight = BooleanProperty(False)
	marker_light = BooleanProperty(False)
	charging_enable = BooleanProperty(False)
	steer_mode = OptionProperty('normal', options=['normal', 'pull', 'diff'])
	video_recorder_req = OptionProperty('off', options=['off', 'on'])
	video_recorder_indicator = OptionProperty(
		VIDEO_RECORDER_INDICATOR.DONOTRECORD if VideoRecorder else '',
		options=(VIDEO_RECORDER_INDICATOR.items() if VideoRecorder else ()))
	followme_on = BooleanProperty(False)
	followme = OptionProperty('off', options=['off', 'on', 'ready', 'run'])
	horn_on = BooleanProperty(False)
	steerassist_on = BooleanProperty(False)
	winch_out = BooleanProperty(False)
	winch_in = BooleanProperty(False)
	background = ObjectProperty()
	touched = BooleanProperty(False)
	preferred_ssid_list = ListProperty()
	preferred_ssid_passwords = DictProperty()
	target_mode = OptionProperty('move', options=('move', 'rangefinder', 'adjust'))
	rangefinder_on = BooleanProperty(False)
	rangefinder_seq = NumericProperty(0)
	rangefinder_distance = NumericProperty(None, allownone=True)
	rangefinder_retries = NumericProperty(0)
	compass_heading = NumericProperty(None, allownone=True)
	rover_speed = NumericProperty(None, allownone=True)
	rover_steer = NumericProperty(None, allownone=True) # angle in radians
	axisunit_azimuth = NumericProperty(None, allownone=True)
	axisunit_elevation = NumericProperty(None, allownone=True)
	ballistic_distance = NumericProperty(100.0)
	ballistic_elevation = NumericProperty(0.0)
	ballistic_windage = NumericProperty(0.0)
	adjust_elevation = NumericProperty(0.0)
	adjust_windage = NumericProperty(0.0)
	target_azimuth = NumericProperty(None, allownone = True)
	target_elevation = NumericProperty(None, allownone = True)
#	target_directions = ReferenceListProperty(target_azimuth, target_elevation)
	auto_move_to_target = BooleanProperty(False)
	cameras_config = DictProperty({})
	cams_quality = OptionProperty(VIDEO_QUALITIES[0], options=VIDEO_QUALITIES)
	cams_pip = BooleanProperty(True)
	cams_night = BooleanProperty(False)
	drive_cams = ListProperty()
	drive_cam_selected = StringProperty('front')
	drive_front_cam_selected = StringProperty('front')
	drive_rear_cam_selected = StringProperty('rear')
	drive_cams_quality = OptionProperty(VIDEO_QUALITIES[0], options=VIDEO_QUALITIES)
	drive_cams_pip = BooleanProperty(True)
	drive_cams_night = BooleanProperty(False)
	sight_cam_selected = StringProperty('wide')
	sight_coarse_cam_selected = StringProperty('wide')
	sight_fine_cam_selected = StringProperty('tele')
	sight_thermo_cam_selected = StringProperty('thermo')
	sight_cams_quality = OptionProperty(VIDEO_QUALITIES[0], options=VIDEO_QUALITIES)
	sight_cams_pip = BooleanProperty(True)
	sight_cams_night_req = BooleanProperty(False)
	sight_cams_night = BooleanProperty(False)
	sight_zoom = NumericProperty(1.0)
	angle_units = OptionProperty('mil', options=AngleSystems.keys())
	distance_units = OptionProperty('m', options=DistanceSystems.keys())
	numpad_layout = OptionProperty('top789', options=['top789', 'top123'])
	hud_color_schema = OptionProperty('day', options=['day','night'])
	loaded_ammo = NumericProperty(100)
	axisunit_lat = NumericProperty(0)
	axisunit_lon = NumericProperty(0)
	axisunit_coords = AliasProperty(
		getter=lambda self: (self.axisunit_lat, self.axisunit_lon,),
		setter=lambda self: (self.axisunit_lat, self.axisunit_lon,),
		bind=['axisunit_lat', 'axisunit_lon']
	)
	axisunit_alt = NumericProperty(allownone=True)
	shots_offset = NumericProperty(None, allownone=True)
	axisunit_base_azimuth = NumericProperty(None, allownone=True)
	axisunit_abs_azimuth = AliasProperty(
		getter=lambda self: (
				None if self.axisunit_azimuth is None else
				norm_angle(self.axisunit_azimuth + (self.rover_abs_heading or 0))
				if self.axisunit_base_azimuth is None else
				norm_angle_uni(self.axisunit_azimuth + (self.axisunit_base_azimuth or 0) + (self.rover_abs_heading or 0))),
		setter=lambda self, abs_azimuth: self.setter('axisunit_base_azimuth')(self,
				None if abs_azimuth is None else
				None if self.axisunit_azimuth is None else
				norm_angle(abs_azimuth - self.axisunit_azimuth - (self.rover_abs_heading or 0))), 
		bind=['axisunit_azimuth', 'axisunit_base_azimuth', 'rover_abs_heading'])
	rover_abs_heading = AliasProperty(
		getter=lambda self: (
				None if self.compass_heading is None else
				norm_angle(self.compass_heading + (self.rover_steer or 0))),
		bind=['compass_heading', 'rover_steer'])
	config = ObjectProperty(allownone=True)

	cams_motion_sensor = BooleanProperty(False)
	drive_cams_motion_sensor = BooleanProperty(False)
	drive_cams_motion_detected = NumericProperty(0)
	sight_cams_motion_sensor = BooleanProperty(False)
	sight_cams_motion_detected = NumericProperty(0)

	master_volume_level = OptionProperty(
		HardwareVolume.DEFAULT_SOUND_VOLUME_LEVEL if HardwareVolume else None, options=HardwareVolume.SOUND_VOLUME_LEVELS if HardwareVolume else [None])

	def on_master_volume_level(self, instance, value):
		if HardwareVolume:
			HardwareVolume.set_volume_level(self.master_volume_level)

	def on_master_volume_level_less(self, *args):
		if HardwareVolume:
			self.master_volume_level = HardwareVolume.set_volume_level(self.master_volume_level, delta=-1)

	def on_master_volume_level_more(self, *args):
		if HardwareVolume:
			self.master_volume_level = HardwareVolume.set_volume_level(self.master_volume_level, delta=+1)

	__events__ = [
		'on_back', 'on_map_zoom_in', 'on_map_zoom_out', 'on_map_zoom_loop',
		'on_sight_zoom_in', 'on_sight_zoom_out', 'on_sight_zoom_loop', 'on_sight_zoom_reset',
		'on_sight_mark_loop', 'on_sight_grid_loop', 'on_sight_rangefinder_loop', 'on_sight_legend_loop', 
		'on_rangefinder_request', 'on_distance_request', 'on_target_menu_request',
		'on_add_target_request', 'on_move_target_request', 'on_coordinates_request',
		'on_del_target_request', 'on_clear_targets_request',
		'on_next_target_request', 'on_prev_target_request',  
		'on_target_azimuth_request', 'on_target_elevation_request', 
		'on_adjust_windage_request', 'on_adjust_elevation_request', 'on_adjust_zero_request',
		'on_ammo_reload_request', 'on_azimuth_request', 'on_lat_request', 'on_lon_request', 'on_alt_request',
		'on_sector_set_left_request', 'on_sector_set_right_request',
		'on_sector_store_request', 'on_sector_reset_request',
		'on_sector_set_up_request', 'on_sector_set_down_request',
		'on_brightness_more', 'on_brightness_less',
		'on_drive_brightness_more', 'on_drive_brightness_less',
		'on_sight_brightness_more', 'on_sight_brightness_less',
		'on_contrast_more', 'on_contrast_less',
		'on_drive_contrast_more', 'on_drive_contrast_less',
		'on_sight_contrast_more', 'on_sight_contrast_less',
		'on_drive_cams_reset', 'on_sight_cams_reset', 'on_cams_reset',
		'on_menu_ok', 'on_menu_cancel', 'on_menu_next', 'on_menu_prev',
		'on_master_volume_level_less', 'on_master_volume_level_more',
	] + (VideoRecorder.__events__ if VideoRecorder else [])

	def __init__(self, **kwargs):
		self.hint_label = None
		self.slider_thr_str = None
		self.slider_str = None
		self.slider_thr = None
		self.slider_rot_ele = None
		self.slider_ele = None
		self.slider_rot = None
		self._trigger_reconfig_inputs = Clock.create_trigger(self._reconfig_inputs)
		self._trigger_throttle_updater = None
		self._trigger_steering_updater = None
		self._trigger_adjustment_updater = None
		self._trigger_update_control_data = Clock.create_trigger(self._update_control_data)
		self._trigger_update_target_mode_icon = Clock.create_trigger(self._update_target_mode_icon)
		self._trigger_update_guideline_layout = Clock.create_trigger(self._update_guideline_layout)
		self._trigger_rangefinder_measurement = Clock.create_trigger(self._rangefinder_measurement, timeout=0.5, interval=True)
		self._fire_burst_end_event = None
		self._status_battery_event = None
		super(ControlLayout, self).__init__(**kwargs)
		self.register_key_handlers(
			    XF86_VOL_RAISE = self.on_brightness_more,
			    XF86_VOL_LOWER = self.on_brightness_less,
			    FN_VOL_RAISE = self.on_brightness_more,
			    FN_VOL_LOWER = self.on_brightness_less,
				ENTER = self.key_handler_enter,
				NUM_ENTER = self.key_handler_enter,
				UP = self.key_handler_up,
				DOWN = self.key_handler_down,
				RIGHT = self.key_handler_right,
				LEFT = self.key_handler_left,
				MENU = self.key_handler_center,
				NUM_6 = self.key_handler_right,
				NUM_4 = self.key_handler_left,
				NUM_5 = self.key_handler_center,
				_6 = self.key_handler_right,
				_4 = self.key_handler_left,
				_5 = self.key_handler_center,
		)
		if self.numpad_layout == 'top789':
			self.register_key_handlers(
					_9 = self.key_handler_prev,
					NUM_9 = self.key_handler_prev,
					_3 = self.key_handler_next,
					NUM_3 = self.key_handler_next,
					_8 = self.key_handler_up,
					NUM_8 = self.key_handler_up,
					_2 = self.key_handler_down,
					NUM_2 = self.key_handler_down,
			)
		else:
			self.register_key_handlers(
					_3 = self.key_handler_prev,
					NUM_3 = self.key_handler_prev,
					_9 = self.key_handler_next,
					NUM_9 = self.key_handler_next,
					_2 = self.key_handler_up,
					NUM_2 = self.key_handler_up,
					_8 = self.key_handler_down,
					NUM_8 = self.key_handler_down,
			)

		self.bind(parent=self._on_parent)
		self.bind_key_handler()

		if self.config:
			self.throttle_mode = self.config.get('input', 'throttlemode')
			self.steering_mode = self.config.get('input', 'steeringmode')
			self.direction_lock = self.config.getboolean('input', 'directionlock')
			self.angle_units = self.config.get('hud', 'angle_units')
			self.coords_system = self.config.get('map', 'coord_system')

		if self.config and not self.config.getboolean('hud', 'hw_brightness'):
			HardwareBrightness = None
		self._cfg_sounds = self.config.getboolean('hud', 'sounds') if self.config else False
		self.slow_gain = self.config.getfloat('input', 'slowgain') if self.config else 0.25
		self.brakes_gain = self.config.getfloat('input', 'brakesgain') if self.config else 1.0
		self.acceleration_gain = self.config.getfloat('input', 'accgain') if self.config else 1.0
		self.fire_burst_trigtime = self.config.getfloat('rover', 'firetrigtime') if self.config else 0.1
		self.fire_burst_firerate = self.config.getfloat('rover', 'firerate') if self.config else 0.2
		self._rover_speed_max = self.config.getfloat('rover', 'speed_max') if self.config else 20
		self._rover_steer_max = self.config.getfloat('rover', 'steer_max') if self.config else math.radians(30)
		self.throttle_gain = self.slow_gain if self.motion_slow else 1.0
		self.steering_gain = self.config.getfloat('input', 'steeringgain') if self.config else 1.0
		self.steering_acceleration = self.config.getfloat('input', 'steeringacc') if self.config else 0.5
		self.steering_recover = self.config.getfloat('input', 'steeringrecover') if self.config else 0.1
		self._rangefinder_present = self.config.get('rover', 'rangefinder') if self.config else 'off'
		self._external_rangefinder_ip = self.config.get(
			'rover', 'external_rangefinder_ip') if self.config else '192.168.88.97'
		self._rangefinder_elevation = self.config.getfloat('rover', 'rangefinder_pitch') if self.config else 0 
		self._rangefinder_windage = self.config.getfloat('rover', 'rangefinder_yaw') if self.config else 0 
		self._rotation_from = self.config.getfloat('rover', 'rotation_from') if self.config else -1.5
		self._rotation_to = self.config.getfloat('rover', 'rotation_to') if self.config else 1.5
		self._elevation_from = self.config.getfloat('rover', 'elevation_from') if self.config else -0.4
		self._elevation_to = self.config.getfloat('rover', 'elevation_to') if self.config else 0.4
		self._reload_ammo = self.config.getfloat('rover', 'reloaded_ammo') if self.config else 0
		self.BURST_NAMES = {
			1: _("short bursts"),
			5: _("middle bursts"),
			0: _("full auto"),
			}
		self._angle_system = AngleSystems[self.angle_units]
		self._adjust_click_value = self._angle_system.to_radians(0.1)
		self.sight_zoom_max_wide = 10.0
		self.sight_zoom_max_tele = 10.0
		self._serial_bindings = []
		self._cached_ballistic_distance = 0
		self._joystick_bindings = []
		self._buttons_bindings = []
		self._external_rangefinder_comm = None
		box_layout = BoxLayout()
		display_layouts = []
		self.add_widget(box_layout)
		for n in range(self.displays):
			box = FloatLayout()
			box_layout.add_widget(box)
			display_layouts.append(box)

		self.video_montage_drive = None
		self.video_montage_target = None
		self.video_monitor_coarse = None
		self.video_monitor_fine = None
		self.video_monitor_thermo = None
		self.buttons_layout_drive = None
		self.buttons_layout_target = None
		self.rssi = None
		self.status_ugv = None
		self.status_axisunit = None
		self.guidelines = None
		self.followme_box = None
		self.fine_scope = None
		self.coarse_scope = None
		self.thermo_scope = None
		self.targets_box = None
		self.compass = None
		self.rover_schema = None
		self.rws_schema = None
		
		drive_display_layout = 0 
		target_display_layout = 1 if self.preset != 'drive' and self.displays > 1 else 0 
		main_display_layout = 1 if self.preset == 'target' and self.displays > 1 else 0 
		extra_display_layout = 1 if self.preset == 'drive' and self.displays > 1 else 0 

		self.video_recorder = None
		if self.config.getboolean('video', 'recording_active', default=False) and VideoRecorder:

			self.video_recorder = VideoRecorder(
				control=self,
				tmp_dir=self.config.get('video', 'recording_tmp_dir'),
				tmp_file_template=self.config.get(
					'video', 'recording_tmp_file_template',
					default="segment_$recording{camera_id}_$recording{camera_quality}_$recording{file_started}_$recording{file_number}.mkv"),
				media_mount_dirs=self.config.get('video', 'recording_media_mount_dirs'),
				media_mount_device_regexp=self.config.get('video', 'recording_media_mount_device_regexp'),
				save_file_command=self.config.get('video', 'recording_save_file_command'),
				save_sub_dir=self.config.get('video', 'recording_save_subdir', default=None),
				save_filename_template=self.config.get(
					'video', 'recording_save_filename_template',
					default="shablya-$recording{file_date_started}-$recording{file_time_started}-$recording{file_time_finished}-$recording{camera_id}$recording{event_list}.mkv"),
				media_desired_empty_space_GB=self.config.getfloat('video', 'recording_media_desired_empty_space_GB', default=1),
				media_warn_empty_space_GB=self.config.getfloat(
					'video', 'recording_media_warn_empty_space_GB', default=self.config.getfloat(
						'video', 'recording_media_desired_empty_space_GB', default=1)*2),
				auto_record_motion=self.config.getboolean('video', 'recording_auto_motion', default=False),
				auto_record_armed=self.config.getboolean('video', 'recording_auto_armed', default=False),
				min_file_duration=self.config.getfloat('video', 'recording_min_file_duration', default=1),
				tmp_files_orphaned_ttl=self.config.getfloat('video', 'reconnect', default=60)+2.,
				has_motion_detector=self.config.getboolean('video', 'motion_detection_active', default=True),
			)
			try:
				self.video_recorder.patch_cameras_config(self.cameras_config)
			except Exception as e:
				Logger.exception(e)
		if not self.video_recorder and VideoRecorder:
			VideoRecorder.cleanup_cameras_config(self.cameras_config)

		self._init_video_montages(display_layouts)
		self._init_overlay_status_radio(display_layouts[main_display_layout])
		self._init_overlay_status_ugv(display_layouts[drive_display_layout])
		self._init_overlay_status_rws(display_layouts[target_display_layout])
		self._init_overlay_scopes(display_layouts[target_display_layout])
		self._init_overlay_hint(display_layouts[main_display_layout])
		self._init_overlay_map(display_layouts[extra_display_layout])
		self._init_overlay_compass(display_layouts[drive_display_layout])
		self._init_overlay_schema_ugv(display_layouts[drive_display_layout])
		self._init_overlay_schema_rws(display_layouts[target_display_layout])
		self._init_overlay_buttons(display_layouts)
		self._camera_states = self._init_camera_states()

		self._trigger_reconfig_inputs()
		if HardwareVolume:
			HardwareVolume.set_volume_level(self.master_volume_level)

		self._update_color_schema(init=True)
		self.bind(hud_color_schema=self._update_color_schema)
		self.bind(target_mode=self._trigger_update_target_mode_icon)
		self.bind(sight_cams_motion_sensor=self._trigger_update_target_mode_icon)
		self.bind(sight_cams_motion_detected=self._trigger_update_target_mode_icon)

		self._start_communication()

		self.motion_detector = None
		if self.config.getboolean('video', 'motion_detection_active', default=True):
			try:
				self.motion_detector = MotionDetector(self)
			except Exception as e:
				Logger.exception(e)

		if HardwareRotation: HardwareRotation.RequestRotation('sensor')

		Clock.schedule_once(self._start_components)

		Logger.info("Control: preset {}, displays {}".format(self.preset, self.displays))

	def _start_components(self, *a, **kwa):
		if self.motion_detector:
			self.motion_detector.start()
		if self.video_recorder:
			self.video_recorder.start()
		# from tools.dev_tools import testing_mode
		# testing_mode(self)


	def _stop_components(self, *a, **kwa):
		if self.motion_detector:
			self.motion_detector.stop()
		if self.video_recorder:
			self.video_recorder.stop()

	def _init_video_montages(self, display_layouts):
		def create_video_montage(display, name, monitors, quality):
			video_montage = VideoMontage(
					monitors = monitors,
					prime = monitors[0],
					quality = quality,
					cameras_config = self.cameras_config,
					size_hint = (1.0, 1.0),
					pos_hint = {'center_x': 0.5, 'center_y':0.5},
					)
			video_montage.bind(pos=lambda i,v: Logger.info(
					"VideoLayout: name: {}, pos: {}, hint: {}, parent: {}".format(
					name, v, video_montage.pos_hint, video_montage.parent.pos)))
			video_montage.bind(size=lambda i,v: Logger.info(
					"VideoLayout: name: {}, size: {}, hint: {}, parent: {}".format(
					name, v, video_montage.size_hint, video_montage.parent.size)))
			display_layouts[display].add_widget(video_montage)
			Logger.info('Control: video montage {} at display {} for {}'.format(name, display, monitors))
			return video_montage

		if self.preset == 'drive':
			self.video_montage_drive = create_video_montage(0, 'drive',
					(self.drive_front_cam_selected, self.drive_rear_cam_selected),
					self.drive_cams_quality)
			self.video_montage_drive.bind(
				video_monitors=self._update_target_video_monitors,
				prime_config=self._update_target_fov,
				video_scale=self._update_target_fov,
				)
			self._update_drive_video_monitors()
		elif self.preset == 'target':
			displ = 0 if self.displays == 1 else 1
			self.video_montage_target = create_video_montage(displ, 'target',
					(self.sight_coarse_cam_selected, self.sight_fine_cam_selected, self.sight_thermo_cam_selected),
					self.sight_cams_quality)
			self.video_montage_target.show_limit = 2 if self.sight_cams_pip else 1
			self.video_montage_target.bind(
				video_monitors=self._update_target_video_monitors,
				prime_config=self._update_target_fov,
				video_scale=self._update_target_fov,
				)
			self._update_target_video_monitors()
			self._update_sight_zoom_max()
		elif self.preset == 'dual':
			if self.displays == 1:
				self.video_montage_drive = self.video_montage_target = create_video_montage(0, 'all',
						(self.drive_front_cam_selected, self.drive_rear_cam_selected,
						self.sight_coarse_cam_selected, self.sight_fine_cam_selected),
						self.cams_quality)
			else:
				self.video_montage_drive = create_video_montage(0, 'drive',
						(self.drive_front_cam_selected, self.drive_rear_cam_selected),
						self.drive_cams_quality)
				self.video_montage_target = create_video_montage(1, 'target',
						(self.sight_coarse_cam_selected, self.sight_fine_cam_selected, self.sight_thermo_cam_selected),
						self.sight_cams_quality)
			self.video_montage_target.show_limit = 2 if self.sight_cams_pip else 1
			self.video_montage_drive.bind(video_monitors=self._update_drive_video_monitors)
			self.video_montage_target.bind(video_monitors=self._update_target_video_monitors)
			self._update_drive_video_monitors()
			self._update_target_video_monitors()
			self._update_sight_zoom_max()
		self.cams_pip = self.config.getboolean('video', 'pip') if self.config else False 
		self.cams_quality = self.config.get('video', 'quality')
		# TODO OZ (BOB): do we need this one (self.video_reconnect_period)?
		self.video_reconnect_period = self.config.getfloat('video', 'reconnect_period') if self.config else 0

	def _init_external_rangefinder(self):
		if self._rangefinder_present != 'external':
			return
		client_host = self._external_rangefinder_ip
		client_port = 20424
		self._external_rangefinder_comm = ExternalRangefinderCommunicator(
			client_host, client_port, timout=6, logger=Logger)

	def _init_overlay_status_radio(self, display_layout):
		radio_host = self.config.get('net', 'radio_host') if self.config else None
		radio_keyfile = self.config.get('net', 'radio_keyfile') if self.config else None
		if radio_host and radio_keyfile and os.path.isfile(radio_keyfile):
			self.rssi = rssi = rssi_rocket.RocketRssiCommunicator(
					host         = radio_host,
					port         = self.config.getint('net', 'radio_port'),
					hostSignature= self.config.get('net', 'radio_signature'),
					username     = self.config.get('net', 'radio_login'),
					keyFile      = self.config.get('net', 'radio_keyfile'),
					password     = self.config.get('net', 'radio_passphrase'),
					period       = self.config.getfloat('net', 'radio_rssi_period'),
				)
			rssi.OnRssiConnected = self.OnRssiConnected
			rssi.OnRssiChanged = self.OnRssiChanged
			rssi.OnRssiDisconnected = self.OnRssiDisconnected
		else:
			if radio_host:
				Logger.error("Control: RSSI monitoring configuration error, invalid keyfile {}".format(radio_keyfile))

	def _init_camera_states(self):
		# init all camera states. in future we have to ask each camera for it state (brightness, contrast, night vision)
		# but for now we set all values to None (unknown) and night vision to false
		video_montages = (self.video_montage_drive, self.video_montage_target,)
		ret = {}
		for video_montage in video_montages:
			if video_montage:
				for video_id, cam_config in video_montage.cameras_config.items():
					ret[video_id] = {'brightness': None, 'contrast': None, 'night': False,
									 'onvif_address': cam_config.onvif_address if cam_config.onvif_address else None,
									 'ignore_ircut_filter': cam_config.ignore_ircut_filter,
									 }
		return ret

	def _init_overlay_buttons(self, display_layouts):
		switches = [
				'motion_enable', 'motion_slow', 'reverse', 'engine',
				'charge', 'allow_lights', 'marker_light', 'headlight',
				'steerassist',
#				'axisunit_enable', 'axisunit_slow', 'safety',
				'drive_cams_pip', 'sight_cams_pip', 'cams_pip',
		]
		class ovr_btn_cls(OverlayBehaviour, OverlayButtonsLayout): pass
		self.buttons_layout_drive = OverlayButtonsLayout(
							buttons_list = [],
							switch_values = {k:(False,True) for k in switches},
							icons = ControlLayout.BUTTON_ICONS,
							pos_hint = {'center_x': 0.5, 'center_y': 0.5},
							size_hint = (0.9, 0.9),
							buttons_size = self.config.getfloat('hud', 'buttons_size') if self.config else OverlayButtonsLayout.buttons_size.defaultvalue,
							buttons_opacity = self.config.getfloat('hud', 'buttons_opacity') if self.config else OverlayButtonsLayout.buttons_opacity.defaultvalue,
							groups_timeout =  self.config.getfloat('hud', 'buttons_timeout') if self.config else 10.0,
							)
		if self.preset == 'target' and self.displays > 1:
			display_layouts[1].add_widget(self.buttons_layout_drive)
		else:
			display_layouts[0].add_widget(self.buttons_layout_drive)
		if self.preset == 'dual' and self.displays > 1:
			self.buttons_layout_target = OverlayButtonsLayout(
						switch_values = {k:(False,True) for k in switches},
						icons = ControlLayout.BUTTON_ICONS,
						pos_hint = {'center_x': 0.5, 'center_y': 0.5},
						size_hint = (0.9, 0.9),
						buttons_size = self.config.getfloat('hud', 'buttons_size') if self.config else OverlayButtonsLayout.buttons_size.defaultvalue,
						buttons_opacity = self.config.getfloat('hud', 'buttons_opacity') if self.config else OverlayButtonsLayout.buttons_opacity.defaultvalue,
						)
			display_layouts[1].add_widget(self.buttons_layout_target)

	def _init_overlay_status_ugv(self, display_layout):
		if self.preset in ('drive', 'dual'):
			self.status_ugv = status_ugv = RoverStatusOverlayLayout(
								full_battery_distance = self.config.getfloat('rover', 'full_battery_distance') if self.config else 0,
								full_battery_voltage = self.config.getfloat('rover', 'full_battery_voltage') if self.config else 0,
								empty_battery_voltage = self.config.getfloat('rover', 'empty_battery_voltage') if self.config else 0,
								max_cell_voltage = self.config.getfloat('rover', 'max_cell_voltage') if self.config else 0,
								min_cell_voltage = self.config.getfloat('rover', 'min_cell_voltage') if self.config else 0,
								battery_cells_count = self.config.getfloat('rover', 'cells_count') if self.config else 0,
								battery_cell_type = self.config.get('rover', 'cell_type') if self.config else 'LiIon',
								battery_height = self.config.getfloat('hud', 'buttons_size') if self.config else RoverStatusOverlayLayout.battery_height.defaultvalue,
								speedometer_height = self.config.getfloat('hud', 'buttons_size') if self.config else RoverStatusOverlayLayout.speedometer_height.defaultvalue,
								speed = self.rover_speed,
								speed_max = self.config.getfloat('rover', 'speed_max') if self.config else 0,
								steer_max = self.config.getfloat('rover', 'steer_max') if self.config else math.radians(30),
								pos_hint = {'center_x': 0.5, 'center_y':0.5},
								size_hint = (1.0, 1.0),
							    font_size = sp(self.config.getfloat('hud', 'font_size') if self.config else 15),
	 							preferred_ssid_list = self.preferred_ssid_list,
	 							preferred_ssid_passwords = self.preferred_ssid_passwords,
	 							)
			if not self._status_battery_event:
				self._update_status_battery()
				self._status_battery_event = Clock.schedule_interval(
						self._update_status_battery, 10)
			display_layout.add_widget(status_ugv)
			self.guidelines = guidelines = GuideLine(
						line_width = 3,
						pos_hint = {},
						size_hint = (None, None),
						)
			self._update_guideline()
			self.bind(steering_out = self._update_guideline)
			if self.config:
				guidelines.path_length = self.config.getfloat('hud', 'guideline_distance')
				guidelines.car_half_length = self.config.getfloat('rover', 'wheelbase') / 2
				guidelines.wheel_track_width = self.config.getfloat('rover', 'track_width')
				guidelines.wheel_width = self.config.getfloat('rover', 'wheel_width')
			self._update_guideline_bindings()
			display_layout.add_widget(guidelines)
			self.followme_box = followme_box = FollowMe()
			display_layout.add_widget(followme_box)
			# self.buttons_layout_drive.fbind('button_followme', self.on_followme)
			# if self.video_monitor_front or self.video_monitor_rear:
				# self.followme_box = followme_box = FollowMe()
			# 	# self.followme_box = followme_box = FollowMeBox()
				# display_layouts[0].add_widget(followme_box)

	def _init_overlay_scopes(self, display_layouts):
		if self.preset in ('target', 'dual'):
			BallisticTableCls = list(BalisticTables_CLS.values())[0]
			if self.config:
				BallisticTableCls = BalisticTables_CLS.get(
					self.config.get('rover', 'ammunition_type'),
					BallisticTableCls
					)
			self._ballistic = BallisticCalc(
				table = BallisticTableCls,
			)
			self.bind(ballistic_distance=self._update_ballistic)
			self.bind(ballistic_windage=self._update_ballistic_windage)
			self.bind(ballistic_elevation=self._update_ballistic_elevation)
			self._update_ballistic()
			wide_cam_config = self.cameras_config.get(self.sight_coarse_cam_selected, None)
			if wide_cam_config and self.video_monitor_coarse:
				self.coarse_scope = coarse_scope = Scope(
#						redraw_delay = 0.1,
						show_legend = (self.sight_cam_selected == self.sight_coarse_cam_selected),
						distance = self.ballistic_distance,
						ballistic_elevation = self.ballistic_elevation,
						ballistic_windage = self.ballistic_windage,
						angle = self.axisunit_elevation,
						azimuth = self.axisunit_abs_azimuth,
						fov = wide_cam_config.fov,
						elevation = wide_cam_config.pitch + self.adjust_elevation,
						windage = wide_cam_config.yaw + self.adjust_windage,
						offset_x = wide_cam_config.x,
						offset_y = wide_cam_config.y,
						angle_units = self.angle_units,
						distance_units = self.distance_units,
						font_size = sp(self.config.getfloat('hud', 'font_size') if self.config else 15),
						pos_hint = {},
						size_hint = (None, None),
						)
				self.bind(
						ballistic_distance=coarse_scope.setter('distance'),
						ballistic_elevation=coarse_scope.setter('ballistic_elevation'),
						ballistic_windage=coarse_scope.setter('ballistic_windage'),
						axisunit_elevation=coarse_scope.setter('angle'),
						axisunit_abs_azimuth=coarse_scope.setter('azimuth'),
						armed=coarse_scope.setter('armed'),
						adjust_elevation=self._update_coarse_scope_elevation,
						adjust_windage=self._update_coarse_scope_windage,
						)
				coarse_scope.bind(
						cross_pos=self._update_coarse_video_origin,
						)
				wide_cam_config.bind(
						yaw=self._update_coarse_scope_windage,  
						pitch=self._update_coarse_scope_elevation,
						)  
				self._update_coarse_scope_bindings()
				self.add_widget(coarse_scope)
			tele_cam_config = self.cameras_config.get(self.sight_fine_cam_selected, None)
			if tele_cam_config and self.video_monitor_fine:
				self.fine_scope = fine_scope = Scope(
#						redraw_delay = 0.1,
						show_legend = (self.sight_cam_selected == self.sight_fine_cam_selected),
						distance = self.ballistic_distance,
						ballistic_elevation = self.ballistic_elevation,
						ballistic_windage = self.ballistic_windage,
						angle = self.axisunit_elevation,
						azimuth = self.axisunit_abs_azimuth,
						fov = tele_cam_config.fov,
						elevation = tele_cam_config.pitch + self.adjust_elevation,
						windage = tele_cam_config.yaw + self.adjust_windage,
						offset_x = tele_cam_config.x,
						offset_y = tele_cam_config.y,
						angle_units = self.angle_units,
						distance_units = self.distance_units,
						font_size = sp(self.config.getfloat('hud', 'font_size') if self.config else 15),
						pos_hint = {},
						size_hint = (None, None),
						)
				self.bind(
						ballistic_distance=fine_scope.setter('distance'),
						ballistic_elevation=fine_scope.setter('ballistic_elevation'),
						ballistic_windage=fine_scope.setter('ballistic_windage'),
						axisunit_elevation=fine_scope.setter('angle'),
						axisunit_abs_azimuth=fine_scope.setter('azimuth'),
						armed=fine_scope.setter('armed'),
						adjust_elevation=self._update_fine_scope_elevation,  
						adjust_windage=self._update_fine_scope_windage,  
						)
				fine_scope.bind(
						cross_pos=self._update_fine_video_origin,
						)
				tele_cam_config.bind(
						yaw=self._update_fine_scope_windage,  
						pitch=self._update_fine_scope_elevation,
						)  
				self._update_fine_scope_bindings()
				self.add_widget(fine_scope)
			thermo_cam_config = self.cameras_config.get(self.sight_thermo_cam_selected, None)
			if thermo_cam_config and self.video_monitor_thermo:
				self.thermo_scope = thermo_scope = Scope(
# 						redraw_delay = 0.1,
						show_legend=(self.sight_cam_selected == self.sight_thermo_cam_selected),
						distance=self.ballistic_distance,
						ballistic_elevation=self.ballistic_elevation,
						ballistic_windage=self.ballistic_windage,
						angle=self.axisunit_elevation,
						azimuth=self.axisunit_abs_azimuth,
						fov=thermo_cam_config.fov,
						elevation=thermo_cam_config.pitch + self.adjust_elevation,
						windage=thermo_cam_config.yaw + self.adjust_windage,
						offset_x=thermo_cam_config.x,
						offset_y=thermo_cam_config.y,
						angle_units=self.angle_units,
						distance_units=self.distance_units,
						font_size=sp(self.config.getfloat('hud', 'font_size') if self.config else 15),
						pos_hint={},
						size_hint=(None, None),
						)
				self.bind(
						ballistic_distance=thermo_scope.setter('distance'),
						ballistic_elevation=thermo_scope.setter('ballistic_elevation'),
						ballistic_windage=thermo_scope.setter('ballistic_windage'),
						axisunit_elevation=thermo_scope.setter('angle'),
						axisunit_abs_azimuth=thermo_scope.setter('azimuth'),
						armed=thermo_scope.setter('armed'),
						adjust_elevation=self._update_thermo_scope_elevation,
						adjust_windage=self._update_thermo_scope_windage,
						)
				thermo_scope.bind(
						cross_pos=self._update_thermo_video_origin,
						)
				thermo_cam_config.bind(
						yaw=self._update_thermo_scope_windage,
						pitch=self._update_thermo_scope_elevation,
						)
				self._update_thermo_scope_bindings()
				self.add_widget(thermo_scope)

	def _init_overlay_status_rws(self, display_layout):
		if self.preset in ('target', 'dual'):
			self.status_axisunit = status_axisunit = TurretStatusOverlayLayout(
								full_battery_time = self.config.getfloat('rover', 'full_battery_time') if self.config else 0,
								full_battery_voltage = self.config.getfloat('rover', 'full_battery_voltage') if self.config else 0,
								empty_battery_voltage = self.config.getfloat('rover', 'empty_battery_voltage') if self.config else 0,
								max_cell_voltage = self.config.getfloat('rover', 'max_cell_voltage') if self.config else 0,
								min_cell_voltage = self.config.getfloat('rover', 'min_cell_voltage') if self.config else 0,
								battery_cells_count = self.config.getfloat('rover', 'cells_count') if self.config else 0,
								battery_cell_type = self.config.get('rover', 'cell_type') if self.config else 'LiIon',
								battery_height = self.config.getfloat('hud', 'buttons_size') if self.config else TurretStatusOverlayLayout.battery_height.defaultvalue,
								elevation_from = self._elevation_from,
								elevation_to = self._elevation_to,
								rotation_from = self._rotation_from,
								rotation_to = self._rotation_to,
								elevation_minP = self.config.getfloat('rover', 'elevation_min') if self.config else -0.8,
								elevation_maxP = self.config.getfloat('rover', 'elevation_max') if self.config else 0.8,
								rotation_minP = self.config.getfloat('rover', 'rotation_min') if self.config else -3,
								rotation_maxP = self.config.getfloat('rover', 'rotation_max') if self.config else 3,
								armed=self.armed,
								ammo_height = self.config.getfloat('hud', 'buttons_size') if self.config else TurretStatusOverlayLayout.ammo_height.defaultvalue,
								ammo_name = self.config.get('rover', 'ammunition_type'),
								burst_mode = self.fire_burst,
								burst_names = self.BURST_NAMES,
								pos_hint = {'center_x': 0.5, 'center_y':0.5},
								size_hint = (1.0, 1.0),
								)
			self.bind(
					armed=status_axisunit.setter('armed'),
					fire_burst=status_axisunit.setter('burst_mode'),
					)
			self._update_target_fov()
			if not self._status_battery_event:
				self._update_status_battery()
				self._status_battery_event = Clock.schedule_interval(
						self._update_status_battery, 10)
			display_layout.add_widget(status_axisunit)
			self.targets_box = targets_box = TargetsBox(
					base_azimuth = self.axisunit_azimuth,
					base_elevation = self.axisunit_elevation,
					default_distance = self.ballistic_distance,
					disabled=not (self.config.getboolean('input', 'touch_target') if self.config else True),
			)
			self.bind(
#					target_azimuth=self._update_target_azimuth_by_axisunit,
#					target_elevation=self._update_target_elevation_by_axisunit,
					axisunit_azimuth=targets_box.setter('base_azimuth'),
					axisunit_elevation=targets_box.setter('base_elevation'),
					ballistic_distance=targets_box.setter('default_distance'),
			)
			targets_box.bind(
					on_target_selected=self._update_axisunit_by_selected_target,
					on_target_deselected=self._update_axisunit_by_selected_target,
			)
			self.video_montage_target.video_overlays['prime'] = targets_box
			self.popup = InputPopup(
				pos_hint = {'center_x': 1 - 0.5 / self.displays, 'center_y':0.5},
				size_hint=(0.6 / self.displays, 0.8),
				attach_to = display_layout,
				input_filter = 'float',
			)
			self.popup.bind(on_open=self.unbind_key_handler, on_dismiss=self.bind_key_handler)

			## TARGET MENU POPUP
			self._init_target_menu_popup(display_layout)

			## LOCATION MENU POPUP
			location_menu_title = f'Coordinates ({self.coords_system})'
			self.location_menu = None  # todo uncomment this before next release
			#self.location_menu = CoordinatesInputPopup(
			#	title=location_menu_title,
			#	size_hint=(.8, .8),
			#	pos_hint={"right": .8, "center": .8},
			#	overlay_color=(0, 0, 0, 0),
			#	coords_system=self.coords_system,
			#	new_latitude=0,
			#	new_longitude=0,
			#	latitude=self.axisunit_lat,
			#	longitude=self.axisunit_lon
#
			#)
			if self.location_menu:
				self.location_menu.bind(
					latitude=self.lat_input_handler,
					longitude=self.lon_input_handler
				)

			## TARGET MODE
			icon_size = self.config.getfloat('hud', 'buttons_size') if self.config else OverlayButtonsLayout.buttons_size.defaultvalue
			self._target_mode_icon = IconButtonText(
					size_hint = (None, None),
					size = (icon_size, icon_size),
					pos_hint = {'center_x':0.9, 'center_y':0.4},
					on_release = self.on_target_mode_icon_release,
					opacities = {'':0},
					disabled = True,
			)
			self._animation_hide = Animation(opacities={'':0.0}, duration=2)
			self._animation_dimout = Animation(opacities={'':0.3}, duration=2)
			self._animation_blink = Animation(size=(icon_size/2, icon_size/2), duration=0.5)
			self._animation_blink += Animation(size=(icon_size, icon_size), duration=0.5)
			self._animation_blink.repeat = True
			self.add_widget(self._target_mode_icon)
		else:
			self.coarse_scope = self.fine_scope = self.thermo_scope = None
			self.status_axisunit = None
			self.popup = None
			self.target_menu = None

	def lat_input_handler(self, i, v):
		self.axisunit_lat = v

	def lon_input_handler(self, i, v):
		self.axisunit_lon = v

	def _init_target_menu_popup(self, display_layout):
		# TARGET MENU POPUP
		from kivy.app import App
		version_string = App.get_running_app().get_version_string()
		targeting_settings_title = f"{_('Targeting settings')} | {version_string}"
		self.target_menu = ShortcutSelectorPopup(
			title = targeting_settings_title,
			pos_hint = {'center_x': 1 - 0.5 / self.displays, 'center_y':0.5},
			size_hint=(0.6 / self.displays, 0.8),
			attach_to = display_layout,
			numpad_layout = self.numpad_layout,
			joystickhids=self.joystickhids,
			serialhids=self.serialhids
		)
		self.target_menu.bind(on_pre_open=self._update_target_menu_mode)

		# adjust
		self._update_target_menu_mode()

		# location
		self.target_menu.add_space(index=3)
		self.target_menu.add_group(_("My location"), 'location_menu', path='location', back=True)
		self.target_menu.add_space(path='location')
		self.target_menu.add_space(path='location')
		self.target_menu.add_shortcut(_("Azimuth"), 'home_azimuth', lambda: (
			self.dispatch('on_azimuth_request')), path='location')
		#  todo uncomment this on the next release
		# self.target_menu.add_shortcut(_("Coordinates"), 'home_position', lambda: (
		# 	self.dispatch('on_coordinates_request')), path='location')
		# self.target_menu.add_shortcut(_("Altitude"), 'home_altitude', lambda: (
		# 	self.dispatch('on_alt_request')), path='location')
		self.target_menu.add_space(index=5)

		# limits
		self.target_menu.add_space(index=6)
		self.target_menu.add_group(_("Limits"), 'sector_menu', path='limits', back=True)
		self.target_menu.add_shortcut(_("Limit bottom"), 'sector_down', lambda: (
			self.dispatch('on_sector_set_down_request')), path='limits')
		self.target_menu.add_shortcut(_("Limits reset"), 'sector_clear', lambda: (
			self.dispatch('on_sector_reset_request')), path='limits')
		self.target_menu.add_shortcut(_("Limit left"), 'sector_left', lambda: (
			self.dispatch('on_sector_set_left_request')), path='limits')
		self.target_menu.add_shortcut(_("Limits store"), 'sector_store', lambda: (
			self.dispatch('on_sector_store_request')), path='limits')
		self.target_menu.add_shortcut(_("Limit right"), 'sector_right', lambda: (
			self.dispatch('on_sector_set_right_request')), path='limits')
		self.target_menu.add_space('limits')
		self.target_menu.add_shortcut(_("Limit top"), 'sector_up', lambda: (
			self.dispatch('on_sector_set_up_request')), path='limits')

		# bindings
		self.target_menu.bind(
			on_open=self.unbind_key_handler,
			on_dismiss=self.bind_key_handler)
		self.target_menu.bind(
			on_dismiss=self.target_menu_on_close
		)

	def _update_target_menu_mode(self, *args):
		tm = self.target_menu
		if self.target_mode in {'move', 'rangefinder'}:
			tm.remove_item(2)
			tm.remove_item(1)
			tm.remove_item(0)
			tm.add_space(index=0)
			tm.add_shortcut(_("Adjusting"), 'adjustment_knobs',
							lambda: (self.setter('target_mode')(self, 'adjust')),
							index=1)
			tm.add_space(index=2)
		elif self.target_mode == 'adjust':
			tm.remove_item(2)
			tm.remove_item(1)
			tm.remove_item(0)
			tm.add_shortcut(_("Reset"), 'adjustment_clear', lambda: (
				self.setter('target_mode')(self, 'move'),
				self.dispatch('on_adjust_zero_request')), index=0)
			tm.add_shortcut(_("Targeting"), 'targeting', lambda: (
				self.setter('target_mode')(self, 'move')), index=1)
			tm.add_shortcut(_("Store"),   'adjustment_store', lambda: (
				self.setter('target_mode')(self, 'move'),
				self.dispatch('on_adjust_zero_request', store=True)), index=2)

	def on_coordinates_request(self, *args):
		if not self.location_menu:
			return
		self.location_menu.activate()


	def _init_overlay_hint(self, display_layout):
		self.hint_label = label = AnimatedHintLabel(
						font_size=sp(self.config.getfloat('hud', 'font_size') if self.config else 15),
						halign='center',
						background_color=(0,0,0,0.3),
						pos_hint = {'center_x': 0.5, 'center_y':0.15},
						show_duration = 0.1,
						show_period = 10,
						hide_duration = 2.0,
						muted=not self._cfg_sounds
						)
		display_layout.add_widget(label)
		label.show()
		self.bind(size=self.on_portrait)
		
	def _init_overlay_map(self, display_layout):
		map_file = self.config.get('map', 'file') if self.config else None
		full_screen_map = self.preset in ('drive', 'target') and self.displays > 1
		pip_map = self.preset in ('dual') or self.displays == 1
		if ((full_screen_map or pip_map) 
			and MapPanel and map_file and os.path.isfile(map_file)
			and os.path.splitext(map_file)[1] == '.mbtiles'
		):
			self.map = MapPanel(
				map_file_name = map_file,
				zoom = self.config.getint('map', 'zoom'),
				markers_size = self.config.getint('map', 'markers_size'),
				size_hint = (1,1) if full_screen_map else (0.3, 0.3),
				pos_hint = {'center_x': 0.5, 'center_y':0.5} if full_screen_map else {'right': 1, 'bottom': 0},
				waypoints = self.local_waypoints,
				coords_system = self.coords_system,
				)
			if self.preset in ('drive', 'dual'):
				self.map.rover_heading = self.rover_abs_heading
				self.bind(rover_abs_heading = self.map.setter('rover_heading'))
				self._update_map_view_by_drive()
				self.bind(rover_abs_heading = self._update_map_view_by_drive)
				self.bind(drive_cam_selected = self._update_map_view_by_drive)
			elif self.preset in ('target',):
				self.map.rover_heading = self.axisunit_base_azimuth
				self.bind(axisunit_base_azimuth = self.map.setter('rover_heading'))
				self._update_map_view_by_sight()
				self.bind(axisunit_abs_azimuth = self._update_map_view_by_sight)
				self.bind(sight_cam_selected = self._update_map_view_by_sight)
			if self.preset in ('target', 'dual'):
				self.map.axisunit_azimuth = self.axisunit_abs_azimuth
				self.map.axisunit_distance = self.ballistic_distance
				self.bind(axisunit_abs_azimuth = self.map.setter('axisunit_azimuth'))
				self.bind(ballistic_distance = self.map.setter('axisunit_distance'))
			if self.outpost_comm:
				self.map.outpost_data = self.outpost_comm.GetOutpostData()
			display_layout.add_widget(self.map)
		else:
			self.map = None
			if map_file:
				Logger.error("Control: Map configuration error, invalid map file {}".format(map_file))
				
		self.gps_logger = GpsLogger() if self.config and self.config.getboolean('map', 'gpslog') else None

	def _init_overlay_compass(self, display_layout):
		if not self.config or self.config.getboolean('hud', 'show_compass'):
			self.compass_size = 1
			ar_layout = FixedAspectRatioLayout(
				aspect_ratio = 5 / 8,
				pos_hint = {'right':0.95, 'y':0.6},
				child_pos_hint = {'right':1, 'y':0},
 				)
			class CompassButton(ButtonBehavior, Compass):
				pass
			self.compass = compass = CompassButton(
				opacity = 0.5,
				on_press = self.switch_compass_size,
				)
			if self.preset in ('drive', 'dual'):
				compass.angle = self.rover_abs_heading
				self.bind(rover_abs_heading = compass.setter('angle'))
			else:
				compass.angle = self.axisunit_abs_azimuth
				self.bind(axisunit_abs_azimuth = compass.setter('angle'))
			ar_layout.add_widget(self.compass)
			display_layout.add_widget(ar_layout)
			self._update_compass_size()

	def _init_overlay_schema_ugv(self, display_layout):
		if self.preset in ('drive', 'dual'):
			if not self.config or self.config.getboolean('hud', 'show_telemetry'):
				self.rover_schema_size = 1
				ar_layout = FixedAspectRatioLayout(
					aspect_ratio = 5 / 8,
					pos_hint = {'right':0.95, 'top':0.6},
					child_pos_hint = {'right':1, 'top':1},
	 				)
				class RoverSchemaButton(ButtonBehavior, RoverSchema):
					pass
				self.rover_schema = RoverSchemaButton(
					opacity = 0.5,
					on_press = self.switch_rover_schema_size,
					full_battery_voltage = self.config.getfloat('rover', 'full_battery_voltage') if self.config else 0,
					empty_battery_voltage = self.config.getfloat('rover', 'empty_battery_voltage') if self.config else 0,
					)
				ar_layout.add_widget(self.rover_schema)
				display_layout.add_widget(ar_layout)
				self._update_rover_schema_size()

	def _init_overlay_schema_rws(self, display_layout):
		if self.preset in ('target', 'dual'):
			if not self.config or self.config.getboolean('hud', 'show_telemetry'):
				self.rws_schema_size = 1
				ar_layout = FixedAspectRatioLayout(
					aspect_ratio = 5 / 5,
					pos_hint = {'right':0.95, 'top':0.6},
					child_pos_hint = {'right':1, 'top':1},
	 				)
				class RwsSchemaButton(ButtonBehavior, RwsSchema):
					pass
				self.rws_schema = RwsSchemaButton(
					opacity = 0.5,
					on_press = self.switch_rws_schema_size,
					)
				ar_layout.add_widget(self.rws_schema)
				display_layout.add_widget(ar_layout)
				self._update_rws_schema_size()
			
	def _on_parent(self, instance, value):
		Logger.debug("Control: _on_parent {}".format(value))
		if not value or value is None:
			Logger.info("Control: stoping all")
			self._unassign_buttons()
			if self.video_montage_drive:
				self.video_montage_drive.video_monitors = []
				self.video_montage_drive.unload()
			if self.video_montage_target:
				self.video_montage_target.video_monitors = []
				self.video_montage_target.unload()
			if self.gsensor is None:
				Logger.debug("Control: no gsensor to stop")
			else:
				self.gsensor.stop()
				self.gsensor.unbind()
				self.gsensor = None
			if self.joystickhids is None:
				Logger.debug("Control: no joystickhids to stop")
			else:
				Logger.debug("Control: stoping joystickhids")
				self._unassign_joystick()
				self.joystickhids.stop()
				self.joystickhids.unbind()
				self.joystickhids = None
			if self.serialhids is None:
				Logger.debug("Control: no serialhids to stop")
			else:
				Logger.debug("Control: stoping serialhids")
				self._unassign_serial()
				self.serialhids.stop()
				self.serialhids.unbind()
				self.serialhids = None
			self._stop_throttle_updater()
			self._stop_steering_updater()
			self._stop_adjustment_updater()
			self._stop_components()
			self._stop_communication()
			if self._status_battery_event:
				self._status_battery_event.cancel()
			self.clear_widgets()
			self.unbind_key_handler()
			Logger.info("Control: all stoped")

	def _update_drive_video_monitors(self, *args):
		Logger.info("Control: video montage monitors update")
		if self.guidelines:
			self._update_guideline_bindings()

	def _update_guideline_bindings(self, *args):
		video_id = self.drive_cam_selected
		cam_config = self.cameras_config.get(video_id)
		video_monitor = self.video_montage_drive.get_prime_video_monitor()
		Logger.info("Control: guideline bindings to %s, %s, %s", video_id, str(cam_config), str(video_monitor))
		if not cam_config or not video_monitor: return
		if not self.reverse:
			self.guidelines.cam_pos = cam_config.pos
			self.guidelines.cam_rot = cam_config.rot
		else:
			self.guidelines.cam_pos = (-cam_config.x, cam_config.y, -cam_config.z) 
			self.guidelines.cam_rot = (cam_config.yaw-math.pi, cam_config.pitch, -cam_config.roll)
		self.guidelines.cam_fov = cam_config.fov
		video_monitor.bind(
			video_pos = self._trigger_update_guideline_layout,
			video_size = self._trigger_update_guideline_layout,
			video_scale = self._trigger_update_guideline_layout,
			)
		self._update_guideline_layout()

	def _update_guideline_layout(self, *args):
		video_monitor = self.video_montage_drive.get_prime_video_monitor()
		Logger.info("Control: guideline update layout to %s %s %s %s",
					str(video_monitor),
					str(video_monitor.video_pos) if video_monitor else "-",
					str(video_monitor.video_size) if video_monitor else "-",
					str(video_monitor.video_scale) if video_monitor else "-",
					)
		if not video_monitor: return
		self.guidelines.pos = video_monitor.video_pos
		self.guidelines.size = video_monitor.video_size
		self.guidelines.fov_scale = video_monitor.video_scale
		self._update_guideline()
		
	def _update_guideline(self, *args):
		self.guidelines.value = self.get_cmd_steering() * self._rover_steer_max * (
			-1 if self.reverse else 1)

	def _update_map_view_by_drive(self, *args):
		cam_config = self.cameras_config.get(self.drive_cam_selected)
		if cam_config and self.rover_abs_heading is not None:
			self.map.view_fov = cam_config.fov
			self.map.view_azimuth = self.rover_abs_heading + cam_config.rot[0] - (
					self.rover_steer if cam_config.pos[2] < 0 else 0)
		else:
			self.map.view_fov = 0
			self.map.view_azimuth = None

	def _update_map_view_by_sight(self, *args):
		cam_config = self.cameras_config.get(self.sight_cam_selected)
		if cam_config and cam_config.fov and self.axisunit_abs_azimuth is not None:
			self.map.view_fov = cam_config.fov
			self.map.view_azimuth = self.axisunit_abs_azimuth + cam_config.rot[0]
		else: 
			self.map.view_fov = 0
			self.map.view_azimuth = None 

	def _update_target_video_monitors(self, *args):
		if not self.video_montage_target or not self.video_montage_target.video_monitors:
			self.video_monitor_coarse = self.video_monitor_fine = self.video_monitor_thermo = None
		elif self.preset == 'target':
			self.video_monitor_coarse, self.video_monitor_fine, self.video_monitor_thermo = self.video_montage_target.video_monitors
		elif self.preset == 'dual':
			if self.displays == 1:
				self.video_monitor_coarse, self.video_monitor_fine = self.video_montage_target.video_monitors[2:]
				self.video_monitor_thermo = None
			else:
				self.video_monitor_coarse, self.video_monitor_fine, self.video_monitor_thermo = self.video_montage_target.video_monitors
		if self.coarse_scope:
			self._update_coarse_scope_bindings()
		if self.fine_scope:
			self._update_fine_scope_bindings()
		if self.thermo_scope:
			self._update_thermo_scope_bindings()

	def _update_coarse_scope_bindings(self, *args):
		self._do_update_scope_bindings(
			self.coarse_scope,
			self.cameras_config.get(self.sight_coarse_cam_selected),
			self.video_monitor_coarse,
			self._update_coarse_scope_layout,
			)

	def _update_coarse_scope_layout(self, *args):
		self._do_update_scope_layout(
			self.coarse_scope,
			self.video_monitor_coarse,
			)

	def _update_fine_scope_bindings(self, *args):
		self._do_update_scope_bindings(
			self.fine_scope,
			self.cameras_config.get(self.sight_fine_cam_selected),
			self.video_monitor_fine,
			self._update_fine_scope_layout,
			)

	def _update_fine_scope_layout(self, *args):
		self._do_update_scope_layout(
			self.fine_scope,
			self.video_monitor_fine,
			)

	def _update_thermo_scope_bindings(self, *args):
		self._do_update_scope_bindings(
			self.thermo_scope,
			self.cameras_config.get(self.sight_thermo_cam_selected),
			self.video_monitor_thermo,
			self._update_thermo_scope_layout,
			)

	def _update_thermo_scope_layout(self, *args):
		self._do_update_scope_layout(
			self.thermo_scope,
			self.video_monitor_thermo,
			)

	def _update_thermo_video_origin(self, *args):
		self._do_update_video_origin(
			self.thermo_scope,
			self.video_monitor_thermo,
			)

	def _update_fine_video_origin(self, *args):
		self._do_update_video_origin(
			self.fine_scope,
			self.video_monitor_fine,
			)

	def _update_coarse_video_origin(self, *args):
		self._do_update_video_origin(
			self.coarse_scope,
			self.video_monitor_coarse,
			)

	def _update_pip_order(self):
		# TODO fix with (self.sight_cam_selected == self.sight_coarse_cam_selected) etc
		if check_cam_in_group(self.sight_cam_selected, 'thermo'):
			order = [
				self.sight_coarse_cam_selected,
				self.sight_fine_cam_selected,
				]
			if self.axisunit_slow:
				order.reverse()
		else:
			order = []
		self.video_montage_target.order = order

	def _update_sight_zoom_max(self):
		coarse_cam_config = self.video_montage_target.cameras_config.get(self.sight_coarse_cam_selected)
		fine_cam_config = self.video_montage_target.cameras_config.get(self.sight_fine_cam_selected)
		if fine_cam_config and fine_cam_config.zooms:
			self.sight_zoom_max_tele = fine_cam_config.zooms[-1]
		else:
			self.sight_zoom_max_tele = 10
		if coarse_cam_config and coarse_cam_config.zooms:
			self.sight_zoom_max_wide = coarse_cam_config.zooms[-1]
		else:
			if fine_cam_config:
				z = 1
				zmax = round(coarse_cam_config.fov / fine_cam_config.fov / 1.2, 1)
				while z < zmax:
					z = round(z * 1.2, 1)
				self.sight_zoom_max_wide = z 
			else:
				self.sight_zoom_max_wide = 10.0
		Logger.info('Control: W/T cam edge %f', self.sight_zoom_max_wide)

	def _do_update_scope_bindings(self, scope, cam_config, video_monitor, update_fn):
		if not cam_config or not video_monitor: return
		scope.cam_pos = cam_config.pos
		scope.cam_rot = cam_config.rot
		scope.cam_fov = cam_config.fov
		video_monitor.bind(
			video_pos = update_fn,
			video_size = update_fn,
			video_scale = update_fn,
			hidden = update_fn,
			)
		update_fn()

	def _do_update_scope_layout(self, scope, video_monitor):
		if not scope or not video_monitor: return
		scope.opacity = 0 if video_monitor.hidden else 1
		scope.pos = video_monitor.video_pos
		scope.size = video_monitor.video_size
		scope.fov_scale = video_monitor.video_scale

	def _do_update_video_origin(self, scope, video_monitor):
		video_monitor.video_scale_origin_hint = (
			(scope.cross_x - scope.x) / scope.width,
			(scope.cross_y - scope.y) / scope.height
			)

	def _update_ballistic(self, *args):
		self.ballistic_windage = 0.0 #TODO
		self.ballistic_elevation = self._ballistic.CalcScopeElevation(self.ballistic_distance)

	def _update_ballistic_windage(self, *args):
		self._trigger_update_control_data()

	def _update_ballistic_elevation(self, *args):
		self._trigger_update_control_data()

	def _update_target_azimuth_by_axisunit(self, *args):
		target_index = self.targets_box.selected_target
		if target_index is not None and self.target_azimuth is not None:
			azimuth = self.target_azimuth - self.ballistic_windage
			self.targets_box.move_target(target_index, azimuth = azimuth)
			Logger.info("Control: target %d azimuth %.3f changed by axisunit",
					target_index, azimuth)

	def _update_target_elevation_by_axisunit(self, *args):
		target_index = self.targets_box.selected_target
		if target_index is not None and self.target_elevation is not None:
			elevation = self.target_elevation - self.ballistic_elevation
			self.targets_box.move_target(target_index, elevation = elevation)
			Logger.info("Control: target %d elevation %.3f changed by axisunit",
					target_index, elevation)
		
	def _update_axisunit_by_selected_target(self, *args):
		target_index = self.targets_box.selected_target
		if target_index is not None:
			target = self.targets_box.targets[target_index]
			self.ballistic_distance = target.distance
			self.target_azimuth = target.azimuth - self.adjust_windage + self.ballistic_windage
			self.target_elevation = target.elevation - self.adjust_elevation + self.ballistic_elevation
			self.auto_move_to_target = True
			Logger.info("Control: auto move activated to #%d @%.3f,%.3f",
					target_index, target.azimuth, target.elevation)
			self._trigger_update_control_data()

	def _update_coarse_scope_elevation(self, *args):
		cam_config = self.cameras_config.get(self.sight_coarse_cam_selected, None)
		self.coarse_scope.elevation = (
			(cam_config.pitch if cam_config else 0.0) +
			(self._rangefinder_elevation if (
					self.rangefinder_on and self._rangefinder_present != 'off') else self.adjust_elevation)
			)
	def _update_coarse_scope_windage(self, *args):
		cam_config = self.cameras_config.get(self.sight_coarse_cam_selected, None)
		self.coarse_scope.windage = (
			(cam_config.yaw if cam_config else 0.0) +
			(self._rangefinder_windage if (
					self.rangefinder_on and self._rangefinder_present != 'off') else self.adjust_windage)
			)
	def _update_fine_scope_elevation(self, *args):
		cam_config = self.cameras_config.get(self.sight_fine_cam_selected, None)
		self.fine_scope.elevation = (
			(cam_config.pitch if cam_config else 0.0) +
			(self._rangefinder_elevation if (
					self.rangefinder_on and self._rangefinder_present != 'off') else self.adjust_elevation)
			)
	def _update_fine_scope_windage(self, *args):
		cam_config = self.cameras_config.get(self.sight_fine_cam_selected, None)
		self.fine_scope.windage = (
			(cam_config.yaw if cam_config else 0.0) +
			(self._rangefinder_windage if (
					self.rangefinder_on and self._rangefinder_present != 'off') else self.adjust_windage)
			)

	def _update_thermo_scope_elevation(self, *args):
		if not self.thermo_scope:
			return
		cam_config = self.cameras_config.get(self.sight_thermo_cam_selected, None)
		self.thermo_scope.elevation = (
			(cam_config.pitch if cam_config else 0.0) +
			(self._rangefinder_elevation if (
					self.rangefinder_on and self._rangefinder_present != 'off') else self.adjust_elevation)
			)

	def _update_thermo_scope_windage(self, *args):
		if not self.thermo_scope:
			return
		cam_config = self.cameras_config.get(self.sight_thermo_cam_selected, None)
		self.thermo_scope.windage = (
			(cam_config.yaw if cam_config else 0.0) +
			(self._rangefinder_windage if (
					self.rangefinder_on and self._rangefinder_present != 'off') else self.adjust_windage)
			)

	def _update_target_fov(self, *args):
		if self.status_axisunit:
			cfg = self.video_montage_target.prime_config
			fov_h = (cfg.fov if cfg else 0) / self.video_montage_target.video_scale
			self.status_axisunit.fov_h = fov_h
			self.status_axisunit.fov_v = fov_h / 16 * 9
#			self.status_axisunit.fov_h = fov_d * math.cos(math.atan(9/16))
#			self.status_axisunit.fov_v = fov_d * math.sin(math.atan(9/16))

	def _update_status_battery(self, *args):
		if HardwareBattery:
			hb = HardwareBattery()
			if self.status_ugv:
				self.status_ugv.battery2_charging = hb.charging
				self.status_ugv.battery2_percentage = hb.capacity * 100
			if self.status_axisunit:
				self.status_axisunit.battery2_charging = hb.charging
				self.status_axisunit.battery2_percentage = hb.capacity * 100
		
	def key_handler_enter(self, window, modifiers):
		if self.preset in ('target', 'dual'):
			self.dispatch('on_distance_request')
			return True

	def key_handler_left(self, window, modifiers):
		Logger.info("key LEFT")
		if self.preset in ('target', 'dual'):
			if 'alt' in modifiers or self.target_mode == 'adjust':
				if 'ctrl' in modifiers:
					self.dispatch('on_adjust_windage_request', -1) # popup
				elif 'shift' in modifiers:
					self.dispatch('on_adjust_windage_request', shift = -0.02)
				else:
					self.dispatch('on_adjust_windage_request',
								angle = -self._adjust_click_value*1.00001)
			elif 'ctrl' in modifiers:
				self.dispatch('on_target_azimuth_request', -1) # popup
			elif 'shift' in modifiers:
				self.dispatch('on_target_azimuth_request', shift = -0.2)
			else:
				self.dispatch('on_target_azimuth_request', angle = -0.00025)
			return True
		
	def key_handler_right(self, window, modifiers):
		Logger.info("key RIGHT")
		if self.preset in ('target', 'dual'):
			if 'alt' in modifiers or self.target_mode == 'adjust':
				if 'ctrl' in modifiers:
					self.dispatch('on_adjust_windage_request', +1) # popup
				elif 'shift' in modifiers:
					self.dispatch('on_adjust_windage_request', shift = +0.02)
				else:
					self.dispatch('on_adjust_windage_request',
								angle = self._adjust_click_value*1.00001)
			elif 'ctrl' in modifiers:
				self.dispatch('on_target_azimuth_request', +1) # popup
			elif 'shift' in modifiers:
				self.dispatch('on_target_azimuth_request', shift = +0.2)
			else:
				self.dispatch('on_target_azimuth_request', angle = +0.00025)
			return True

	def key_handler_down(self, window, modifiers):
		Logger.info("key DOWN")
		if self.preset in ('target', 'dual'):
			if 'alt' in modifiers or self.target_mode == 'adjust':
				if 'ctrl' in modifiers:
					self.dispatch('on_adjust_elevation_request', -1) # popup
				elif 'shift' in modifiers:
					self.dispatch('on_adjust_elevation_request', shift = -0.02)
				else:
					self.dispatch('on_adjust_elevation_request',
								angle = -self._adjust_click_value*1.00001)
			elif 'ctrl' in modifiers:
				self.dispatch('on_target_elevation_request', -1) # popup
			elif 'shift' in modifiers:
				self.dispatch('on_target_elevation_request', shift = -0.2)
			else:
				self.dispatch('on_target_elevation_request', angle = -0.00025)
			return True

	def key_handler_prev(self, window, modifiers):
		if self.preset in ('target', 'dual'):
			self.dispatch('on_prev_target_request')
			return True

	def key_handler_next(self, window, modifiers):
		if self.preset in ('target', 'dual'):
			self.dispatch('on_next_target_request')
			return True

	def key_handler_up(self, window, modifiers):
		Logger.info("key UP")
		if self.preset in ('target', 'dual'):
			if 'alt' in modifiers or self.target_mode == 'adjust':
				if 'ctrl' in modifiers:
					self.dispatch('on_adjust_elevation_request', +1) # popup
				elif 'shift' in modifiers:
					self.dispatch('on_adjust_elevation_request', shift = +0.02)
				else:
					self.dispatch('on_adjust_elevation_request',
								angle = +self._adjust_click_value*1.00001)
			elif 'ctrl' in modifiers:
				self.dispatch('on_target_elevation_request', +1) # popup
			elif 'shift' in modifiers:
				self.dispatch('on_target_elevation_request', shift=0.2)
			else:
				self.dispatch('on_target_elevation_request', angle = +self._adjust_click_value)
			return True

	def key_handler_center(self, window, modifiers):
		Logger.info("key CENTER")
		if self.preset in ('target', 'dual'):
			if 'alt' in modifiers:
				if 'ctrl' in modifiers:
					self.dispatch('on_adjust_zero_request', store = True)
				else:
					self.dispatch('on_adjust_zero_request')
#			else:
#				self.dispatch('on_target_menu_request')
			return True

	def on_reload_ammo(self):
		Logger.info("Ballistic: Ammo reloaded {}".format(self.loaded_ammo))
		self.shots_offset = None
		if self.status_axisunit:
			self.status_axisunit.ammo_count = self.loaded_ammo
			self.status_axisunit.ammo_capacity = self.loaded_ammo
		
	def _popup_loaded_ammo(self, *args):
		if self.popup:
			def loaded_ammo_setter_fn(i,v):
				ammo_count = int(v)
				self.loaded_ammo = ammo_count
				self.on_reload_ammo()
			self.popup.activate(
					title = _('Reload ammo count'),
					text = str(int(self.loaded_ammo)),
					input_filter = 'int',
					signed = False,
					step = 1,
					on_validate = loaded_ammo_setter_fn,
			)

	def _popup_azimuth(self, *args):
		if self.popup:
			def my_azimuth_setter_fn(i,v):
				azimuth = self._angle_system.to_radians(float(v))
				Logger.info("Ballistic: my azimuth {}".format(azimuth))
				self.axisunit_abs_azimuth = azimuth
			self.popup.activate(
					title = 'My azimuth ({})'.format(self._angle_system.name),
					text = str(self._angle_system.from_radians(
						self.axisunit_abs_azimuth or 0)),
					input_filter = 'float',
					signed = True,
					step = 1,
					on_validate = my_azimuth_setter_fn,
			)

	def _popup_alt(self, *args):
		if self.popup:
			distance_system = DistanceSystems[self.distance_units]

			def altitude_setter_fn(i, v):
				alt = distance_system[2](float(v))
				Logger.info(f"Location: my altitude {alt}")
				self.axisunit_alt = alt

			self.popup.activate(
					title='Altitude ({})'.format(distance_system[0]),
					text=str(int(distance_system[1](self.axisunit_alt))),
					input_filter='float',
					signed=False,
					step=1,
					on_validate=altitude_setter_fn
			)

	def _popup_distance(self, *args):
		if self.popup:
			distance_system = DistanceSystems[self.distance_units]
			def distance_setter_fn(i,v):
				distance = distance_system[2](float(v))
				Logger.info("Ballistic: Distance changed by user from {} to {} ({}{})".format(
						self.ballistic_distance, distance, v, distance_system[0]))
				self.ballistic_distance = distance
			self.popup.activate(
					title = 'Distance ({})'.format(distance_system[0]),
					text = str(int(distance_system[1](self.ballistic_distance))),
					input_filter = 'float',
					signed = False,
					step = 1,
					on_validate = distance_setter_fn
			)
		
	def _popup_target_azimuth(self, sign, *args):
		if self.popup:
			if self.ballistic_distance:
				distance_system = DistanceSystems[self.distance_units]
				self.popup.activate(
						title = 'Target azimuth relative move at distance {} to {} ({})'.format(
								distance_system.from_meters(self.ballistic_distance),
								'left' if sign < 0 else 'right',
								distance_system.name),
						text = '0',
						input_filter = 'float',
						signed = True,
						step = 1,
						on_validate = lambda i,v: self.shift_target_azimuth(sign * distance_system.to_meters(float(v))),
				)
			else:
				self.popup.activate(
						title = 'Target azimuth relative move {} ({})'.format(
								'left' if sign < 0 else 'right',
								self._angle_system.name),
						text = '0',
						input_filter = 'float',
						signed = True,
						step = 1,
						on_validate = lambda i,v: self.change_target_azimuth(
								sign * self._angle_system.to_radians(float(v))),
				)

	def _popup_target_elevation(self, sign, *args):
		if self.popup:
			if self.ballistic_distance:
				distance_system = DistanceSystems[self.distance_units]
				self.popup.activate(
						title = 'Target elevation relative move at distance {} to {} ({})'.format(
								distance_system.from_meters(self.ballistic_distance),
								'left' if sign < 0 else 'right',
								distance_system.name),
						text = '0',
						input_filter = 'float',
						signed = True,
						step = 1,
						on_validate = lambda i,v: self.shift_target_elevation(
								sign * distance_system.to_meters(float(v))),
				)
			else:
				self.popup.activate(
						title = 'Target elevation relative move to {} ({})'.format(
								'left' if sign < 0 else 'right',
								self._angle_system.name),
						text = '0',
						input_filter = 'float',
						signed = True,
						step = 1,
						on_validate = lambda i,v: self.change_target_elevation(
								sign * self._angle_system.to_radians(float(v))),
				)
		
	def _popup_adjust_windage(self, sign, *args):
		if self.popup:
			if self.ballistic_distance:
				distance_system = DistanceSystems[self.distance_units]
				self.popup.activate(
						title = 'Windage {}{} correction at distance {} {} ({})'.format(
								self._angle_system.format(self.adjust_windage),
								self._angle_system.name,
								distance_system.format(self.ballistic_distance),
								'left' if sign < 0 else 'right',
								distance_system.name),
						text = '0',
						input_filter = 'float',
						signed = True,
						step = 1,
						on_validate = lambda i,v: self.shift_adjust_windage(
								sign * distance_system.to_meters(float(v))),
				)
			else:
				self.popup.activate(
						title = 'Windage correction relative from {} {} ({})'.format(
								self._angle_system.format(self.adjust_windage),
								'left' if sign < 0 else 'right',
								self._angle_system.name),
						text = '0',
						input_filter = 'float',
						signed = True,
						step = 1,
						on_validate = lambda i,v: self.change_adjust_windage(
								sign * self._angle_system.to_radians(float(v))),
				)
		
	def _popup_adjust_elevation(self, sign, *args):
		if self.popup:
			if self.ballistic_distance:
				distance_system = DistanceSystems[self.distance_units]
				self.popup.activate(
						title = 'Elevation {} correction at distance {} {} ({})'.format(
								self._angle_system.format(self.adjust_windage),
								self._angle_system.name,
								distance_system.format(self.ballistic_distance),
								'down' if sign < 0 else 'up',
								distance_system.name),
						text = '0',
						input_filter = 'float',
						signed = True,
						step = 1,
						on_validate = lambda i,v: self.shift_adjust_elevation(
								sign * distance_system.to_meters(float(v))),
				)
			else:
				self.popup.activate(
						title = 'Elevation correction relative from {} {} ({})'.format(
								self._angle_system.format(self.adjust_elevation),
								'down' if sign < 0 else 'up',
								self._angle_system.name),
						text = '0',
						input_filter = 'float',
						signed = True,
						step = 1,
						on_validate = lambda i,v: self.change_adjust_elevation(
								sign * self._angle_system.to_radians(float(v))),
				)
		
	def shift_target_azimuth(self, shift):
		"""change target azimuth by specified "on target" shift (m) at current ballistic distance (m)"""
		from_azimuth = self.target_azimuth if self.target_azimuth is not None else self.axisunit_azimuth
		if from_azimuth is None: return
		new_azimuth = from_azimuth + math.atan2(shift, self.ballistic_distance)
		Logger.info("Target: Azimuth shifted from {} to {} by {}m at {}m".format(
				self.axisunit_azimuth, new_azimuth, shift, self.ballistic_distance))
		self.target_azimuth = new_azimuth

	def change_target_azimuth(self, change):
		"""change target azimuth by specified angle relative to current target azimuth"""
		from_azimuth = self.target_azimuth if self.target_azimuth is not None else self.axisunit_azimuth
		if from_azimuth is None: return
		new_azimuth = from_azimuth + change
		Logger.info("Target: Azimuth changed from {} to {} by {}rad)".format(
				from_azimuth, new_azimuth, change))
		self.target_azimuth = new_azimuth
		
	def shift_target_elevation(self, shift):
		"""change target elevation by specified "on target" shift (m) at current ballistic distance (m)"""
		from_elevation = self.target_elevation if self.target_elevation is not None else self.axisunit_elevation
		if from_elevation is None: return
		new_elevation = from_elevation + math.atan2(shift, self.ballistic_distance)
		Logger.info("Target: Elevation shifted from {} to {} by {}m at {}m".format(
				self.axisunit_elevation, new_elevation, shift, self.ballistic_distance))
		self.target_elevation = new_elevation

	def change_target_elevation(self, change):
		"""change target elevation by specified angle relative to current target elevation"""
		from_elevation = self.target_elevation if self.target_elevation is not None else self.axisunit_elevation
		if from_elevation is None: return
		new_elevation = from_elevation + change
		Logger.info("Target: Elevation changed from {} to {} by {}rad)".format(
				from_elevation, new_elevation, change))
		self.target_elevation = new_elevation
		
	def shift_adjust_windage(self, shift):
		"""change adjust windage by specified "on target" shift (m) at current ballistic distance (m)"""
		self._update_scope_adjustment(windage=math.atan2(shift, self.ballistic_distance))

	def change_adjust_windage(self, change):
		"""change adjust windage by specified angle relative"""
		self._update_scope_adjustment(windage=change)
		
	def set_adjust_windage(self, windage):
		"""set adjust windage to specified angle"""
		Logger.info("Adjust: Windage set to {})".format(windage))
		self.adjust_windage = windage
		self._show_adjustment_hint()

	def shift_adjust_elevation(self, shift):
		"""change adjust elevation by specified "on target" shift (m) at current ballistic distance (m)"""
		self._update_scope_adjustment(elevation=math.atan2(shift, self.ballistic_distance))

	def change_adjust_elevation(self, change):
		"""change adjust elevation by specified angle relative to current adjust elevation"""
		self._update_scope_adjustment(elevation=change)
		
	def set_adjust_elevation(self, elevation):
		"""set adjust elevation to specified angle"""
		Logger.info("Adjust: Elevation changed to {})".format(elevation))
		self.adjust_elevation = elevation
		self._show_adjustment_hint()

	def _rollback_rangefinder_adjustment(self):
		self._rangefinder_elevation = self.config.getfloat('rover', 'rangefinder_pitch') if self.config else 0
		self._rangefinder_windage = self.config.getfloat('rover', 'rangefinder_yaw') if self.config else 0

	def reset_adjust(self, *args):
		self.adjust_windage = self.adjust_elevation = 0.0
		self._rollback_rangefinder_adjustment()
		self._show_adjustment_hint(click_sound='click-clear')

	def store_adjust(self, *args):
		if self._rangefinder_adjustment_allowed():
			self._rangefinder_windage -= self.adjust_windage
			self._rangefinder_elevation -= self.adjust_elevation
		lock_cams = self.config.getboolean('video', 'lock_cams') if self.config else True
		if lock_cams:
			cam_configs = [cam_config for cam_config in self.cameras_config.values()
						if any([check_cam_in_group(cam_config.id, grp) for grp in ('tele', 'wide', 'thermo')])]
		else:
			cam_id = self.video_montage_target.prime
			cam_config = self.cameras_config.get(cam_id, None)
			if cam_config:
				cam_configs = [cam_config]
			else:
				cam_configs = []
				Logger.warning("Control: Camera config %s not found", cam_id)
		for cam_config in cam_configs:
			cam_config.pitch = cam_config.pitch + self.adjust_elevation
			cam_config.yaw = cam_config.yaw + self.adjust_windage
			Logger.warning("Control: Camera config %s changed, yaw: %f, pitch: %f", cam_config.id, cam_config.yaw, cam_config.pitch)
		self._show_adjustment_stored_hint()
		self.adjust_windage = self.adjust_elevation = 0.0
		self.video_montage_target.update_overlays()
		if self.config: 
			self.config.config.set('rover', 'rangefinder_pitch', self._rangefinder_elevation)
			self.config.config.set('rover', 'rangefinder_yaw', self._rangefinder_windage) 
			self.config.config.write() 

	def _show_adjustment_hint(self, click_sound=None, with_distance = False):
		fmt = self._angle_system.format
		self._show_hint_label(
			message=u'\n'.join([
				_("Adjusting:"),
				_("Windage {}").format(
					fmt(self.adjust_windage, precision=2, plus=True, name=True)),
				_("Elevation {}").format(
					fmt(self.adjust_elevation, precision=2, plus=True, name=True)),
				]),
			sound=click_sound)
		
	def _show_adjustment_stored_hint(self):
		fmt = self._angle_system.format
		self._show_hint_label(
			message=u'\n'.join([
				_("Adjustment stored for:"),
				_("Windage {}").format(
					fmt(self.adjust_windage, precision=2, plus=True, name=True)),
				_("Elevation {}").format(
					fmt(self.adjust_elevation, precision=2, plus=True, name=True)),
				]),
			sound='click-complete')

	def _show_sector_horizontal_hint(self):
		fmt = self._angle_system.format
		self._show_hint_label(_("Limits changed for azimuth {}-{}").format(
				fmt(self._rotation_from, precision=0, plus=True, name=False),
				fmt(self._rotation_to, precision=0, plus=True, name=True),
		))
		
	def _show_sector_vertical_hint(self):
		fmt = self._angle_system.format
		self._show_hint_label(_("Limits changed for angle {}-{}").format(
				fmt(self._elevation_from, precision=0, plus=True, name=False),
				fmt(self._elevation_to, precision=0, plus=True, name=True),
		))
		
	def _show_sector_reset_hint(self):
		self._show_hint_label(_("Limits reset. Do it again to force hardware homing"))

	def _show_forced_homing_hint(self):
		self._show_hint_label(_("Homing forced"))

	def _show_sector_stored_hint(self):
		fmt = self._angle_system.format
		self._show_hint_label(_("Limits stored for azimuth {}-{} and angle {}-{}").format(
				fmt(self._rotation_from, precision=0, plus=True, name=False),
				fmt(self._rotation_to, precision=0, plus=True, name=True),
				fmt(self._elevation_from, precision=0, plus=True, name=False),
				fmt(self._elevation_to, precision=0, plus=True, name=True),
		))

	def on_target_menu_request(self, *args):
		Logger.info("Control: target menu requested")
		btn_list = ['on_press_menu_next', 'on_press_menu_prev', 'on_press_menu_cancel', 'on_press_menu_ok']
		interface_key_map_pair_list = [(self.serialhids, self._serial_bindings,),
									   (self.joystickhids, self._joystick_bindings,)]
		CommonHidsBase.unbind_items(interface_key_map_pair_list, btn_list)
		if self.buttons_layout_target:
			self.buttons_layout_target.reset_selection()
		elif self.buttons_layout_drive:
			self.buttons_layout_drive.reset_selection()
		self.target_menu.activate()

	def on_rangefinder_request(self, *args):
		self.rangefinder_distance = None
		self.rangefinder_retries = 0
		self._init_external_rangefinder()
		if self._external_rangefinder_comm:
			self._external_rangefinder_comm.launch_in_thread(callback=self.OnDistanceChanged)
		self._trigger_rangefinder_measurement()
		self._rangefinder_measurement()

	def _rangefinder_measurement(self, *args):
		self.rangefinder_retries += 1
		if self.rangefinder_retries <= 5:
			if self.rangefinder_seq < 254:
				self.rangefinder_seq = self.rangefinder_seq + 1
			else:
				self.rangefinder_seq = 1
			self._trigger_update_control_data()
			Logger.info("Control: rangefinder requested %d.%d",
					self.rangefinder_seq, self.rangefinder_retries)
		else:
			self._trigger_rangefinder_measurement.cancel()
			if not self.ballistic_distance:
				self.ballistic_distance = self._cached_ballistic_distance
			self._popup_distance()

	def on_distance_request(self, *args):
		Logger.info("Control: distance requested")
		self.rangefinder_on = not self.rangefinder_on

	def on_add_target_request(self, *args):
		if self.targets_box and self.axisunit_azimuth is not None and self.axisunit_elevation is not None:
			azimuth = self.axisunit_azimuth + self.adjust_windage - self.ballistic_windage
			elevation = self.axisunit_elevation + self.adjust_elevation - self.ballistic_elevation
			distance = self.ballistic_distance
			closest_idx,d = self.targets_box.get_closest(azimuth, elevation, distance)
			if closest_idx is None or d > 0.001:
				self.targets_box.add_target(azimuth, elevation, distance)
				Logger.info("Control: Target added @ %.4f, %.4f (%+.4f), d:%.0f"%(
						azimuth, elevation, self.ballistic_elevation, distance))
				self.targets_box.select_target(len(self.targets_box.targets) - 1)
			else:
				self.targets_box.move_target(closest_idx, azimuth, elevation, distance)
				Logger.info("Control: Target %d changed @ %.4f, %.4f (%+.4f), d:%.0f"%(
						closest_idx, azimuth, elevation,
						self.ballistic_elevation, distance))
				self.targets_box.select_target(len(self.targets_box.targets) - 1)

	def on_move_target_request(self, *args):
		n = len(self.targets_box.targets)
		if n > 0:
			target_index = self.targets_box.selected_target
			if target_index is not None:
				scope = self.fine_scope if self.axisunit_slow else self.coarse_scope
				targets_box = self.targets_box
				self.targets_box.move_target(
					target_index,
					self.axisunit_azimuth - self.ballistic_windage,
					self.axisunit_elevation - self.ballistic_elevation,
					self.ballistic_distance)

	def on_del_target_request(self, *args):
		n = len(self.targets_box.targets)
		if n > 0:
			target_index = self.targets_box.selected_target
			if target_index is not None:
				self.targets_box.remove_target(target_index)

	def on_clear_targets_request(self, *args):
		while len(self.targets_box.targets):
			self.targets_box.remove_target(0)
		self.auto_move_to_target = False

	def on_prev_target_request(self, *args):
		n = len(self.targets_box.targets)
		if n > 0:
			target_index = self.targets_box.selected_target
			if target_index is None:
				target_index = n - 1
			elif self.auto_move_to_target:
				target_index = (target_index + n - 1)%n
			else:
				target_index = target_index%n
			self.targets_box.select_target(target_index)
		
	def on_next_target_request(self, *args):
		n = len(self.targets_box.targets)
		if n > 0:
			target_index = self.targets_box.selected_target
			if target_index is None:
				target_index = 0
			elif self.auto_move_to_target:
				target_index = (target_index + 1)%n
			else:
				target_index = target_index%n
			self.targets_box.select_target(target_index)
		
	def on_target_azimuth_request(self, sign = 1, angle=None, shift=None, *args):
		if angle is not None:
			self.change_target_azimuth(angle)
		elif shift is not None and self.ballistic_distance:
			self.shift_target_azimuth(shift)
		else:
			self._popup_target_azimuth(sign)

	def on_target_elevation_request(self, sign = 1, angle=None, shift=None, *args):
		m = 0.2 if self.axisunit_slow else 1.0
		if angle is not None:
			self.change_target_elevation(angle * m)
		elif shift is not None and self.ballistic_distance:
			self.shift_target_elevation(shift * m)
		else:
			self._popup_target_elevation(sign)

	def on_adjust_windage_request(self, sign = 1, angle=None, shift=None, *args):
		m = 0.2 if self.axisunit_slow else 1.0
		if angle is not None:
			self.change_adjust_windage(angle * m)
		elif shift is not None and self.ballistic_distance:
			self.shift_adjust_windage(shift * m)
		else:
			self._popup_adjust_windage(sign)

	def on_adjust_elevation_request(self, sign = 1, angle=None, shift=None, *args):
		if angle is not None:
			self.change_adjust_elevation(angle)
		elif shift is not None and self.ballistic_distance:
			self.shift_adjust_elevation(shift)
		else:
			self._popup_adjust_elevation(sign)

	def on_adjust_zero_request(self, store = False, *args):
		if store:
			self.store_adjust()
		else:
			self.reset_adjust()

	def on_gsensor(self, instance, value):
		Logger.debug("Control: GSensor changed: %s"%str(value))
		if self.gsensor and not value:
			self.gsensor.stop()

	def on_allow_slider2d(self, instance, value):
		self._trigger_reconfig_inputs()

	def on_preset(self, instance, value):
		self._trigger_reconfig_inputs()

	def on_portrait(self, instance, value):
		self._update_sliders_layout()

	def on_throttle_mode(self, instance, value):
		self._restart_throttle_updater()

	def on_steering_mode(self, instance, value):
		self._restart_steering_updater()

	def on_active(self, instance, value):
		if value:
			Logger.info("Control: ACTIVE!")
			if platform == 'android': Logger.info("Control: Orientation locked to %s"%HardwareRotation.GetRotation())
			if platform == 'android': HardwareRotation.LockRotation()
			self._start_throttle_updater()
			self._start_steering_updater()
			self._hide_hint_label(duration=0.1)
		else:
			Logger.info("Control: Passive")
			self._stop_throttle_updater()
			self._stop_steering_updater()
			if platform == 'android': HardwareRotation.UnlockRotation()

	def on_throttle(self, instance, value):
# 		Logger.info("Control: Throttle = %.2f"%value)
		if self._trigger_throttle_updater: self._trigger_throttle_updater()

	def on_brake(self, instance, value):
		Logger.info("Control: Brake %.2f"%value)
		if self._trigger_throttle_updater: self._trigger_throttle_updater()

	def on_throttle_out(self, instance, value):
		self._trigger_update_control_data()

	def on_steering(self, instance, value):
		if self._trigger_steering_updater: self._trigger_steering_updater()

	def on_steering_out(self, instance, value):
		self._trigger_update_control_data()

	def on_elevation(self, instance, value):
		if self.auto_move_to_target and abs(value) > 0.02:
			# discard moving to target on joystick override
			self.auto_move_to_target = False
			Logger.info("Control: auto move discarded by Elevation %.3f", value)
		self._trigger_update_control_data()

	def on_rotation(self, instance, value):
		if self.auto_move_to_target and abs(value) > 0.02:
			# discard moving to target on joystick override
			self.auto_move_to_target = False
			Logger.info("Control: auto move discarded by Rotation %.3f", value)
		self._trigger_update_control_data()

	def on_elevation_slow(self, instance, value):
		if self.auto_move_to_target and abs(value) > 0.02:
			# discard moving to target on joystick override
			self.auto_move_to_target = False
			Logger.info("Control: auto move discarded by elevation %.3f", value)
		self._trigger_update_control_data()

	def on_rotation_slow(self, instance, value):
		if self.auto_move_to_target and abs(value) > 0.02:
			# discard moving to target on joystick override
			self.auto_move_to_target = False
			Logger.info("Control: auto move discarded by rotation %.3f", value)
		self._trigger_update_control_data()

	def on_target_azimuth(self, instance, value):
		self._trigger_update_control_data()

	def on_target_elevation(self, instance, value):
		self._trigger_update_control_data()

	def on_engine_on(self, instance, value):
		if value:
			self._show_hint_label(_('Ignition ON, now run the starter'))
		else:
			self._show_hint_label(_('Engine off'))
		self._trigger_update_control_data()

	def on_engine_start(self, instance, value):
		if value:
			self._show_hint_label(_('Starter...'))
		else:
			self._hide_hint_label(_('Starter...'), 0.1)
		self._trigger_update_control_data()

	def on_motion_enable(self, instance, value):
		if value:
			Clock.unschedule(self._delayed_disable)
			self.motion_enable_delayed = True
		else:
			Clock.schedule_once(self._delayed_disable, 2)
		Logger.info("Control: ENABLE!" if value else "disable")
		self._trigger_update_control_data()

	def on_motion_enable_delayed(self, instance, value):
		Logger.info("Control: ENABLE! (delayed)" if value else "disable (delayed)")
		self._trigger_update_control_data()

	def on_reverse(self, *args):
		if self.reverse:
			if check_cam_in_group(self.drive_rear_cam_selected, 'rear'):
				self.drive_cam_selected = self.drive_rear_cam_selected
			else:
				self.drive_cam_selected = 'rear'
		else:
			if check_cam_in_group(self.drive_front_cam_selected, 'front'):
				self.drive_cam_selected = self.drive_front_cam_selected
			else:
				self.drive_cam_selected = 'front'
		if self.video_montage_drive:
			self.video_montage_drive.prime = self.drive_cam_selected
		if self.guidelines:
			self._update_guideline_bindings()

	def on_axisunit_enable(self, instance, value):
		# discard moving to target on enable/disable
		self.auto_move_to_target = False
		self._trigger_update_control_data()

	def on_target_mode_icon_release(self, instance):
		if self.target_mode == 'move':
			self.sight_cams_motion_sensor = False
			self.drive_cams_motion_sensor = False
		elif self.target_mode == 'adjust':
			self.target_mode = 'move'
#			self.dispatch('on_adjust_windage_request') 
		elif self.target_mode == 'rangefinder':
			self.rangefinder_on = False 
		
	def _update_target_mode_icon(self, *args):
		icon = self._target_mode_icon
		Animation.cancel_all(icon)
		icon_size = self.config.getfloat('hud', 'buttons_size') if self.config else OverlayButtonsLayout.buttons_size.defaultvalue
		icon.size = (icon_size, icon_size) 
		icon.opacities = {'':1}
		if self.target_mode == 'adjust':
			icon.background_name = 'adjustment_knobs'
			icon.disabled = False
			icon.state = 'normal'
			icon.text = ''
			self._animation_blink.start(icon)
		elif self.target_mode == 'rangefinder':
			icon.background_name = 'distance'
			icon.disabled = False
			icon.state = 'normal'
			icon.text = ''
			self._animation_blink.start(icon)
		elif self.sight_cams_motion_sensor:
			if self.motion_detector:
				self.motion_detector.update_icon_by_motion_alert(icon)
		else:
			icon.background_name = 'targeting'
			icon.state = 'normal'
			icon.disabled = True
			icon.text = ''
			self._animation_hide.start(icon)

	def on_target_mode(self, instance, value):
		if value == 'move':
			if self._stop_adjustment_updater():
				self._show_adjustment_hint()
			if self.target_menu:
				self.target_menu.dismiss()
			self.rangefinder_on = False
		elif value == 'adjust':
			if self.target_menu:
				self.target_menu.dismiss()
			self._start_adjustment_updater()
		elif value == 'rangefinder':
			if self._stop_adjustment_updater():
				self._show_adjustment_hint()
		self._trigger_update_control_data()

	def on_rangefinder_on(self, instance, value):
		self._update_scopes_types()
		if value:
			self.target_mode = 'rangefinder'
			if self._rangefinder_present in {'internal', 'external'}:
				self._cached_ballistic_distance = self.ballistic_distance
				self.ballistic_distance = 0  # zero sight cross offset
		else:
			self.target_mode = 'move'
			if self._rangefinder_present in {'internal', 'external'}:
				self.dispatch('on_rangefinder_request')
			else:
				self._popup_distance()
			
	def on_armed_req(self, instance, value):
		self.armed = (value and not self.fire_req and not self.fire)

	def on_armed(self, instance, value):
		Logger.info("Control: Armed req %s"%value)
		if value:
			self._show_hint_label(_("! ARMED !"), expose_period=0.5,
								sound='armed')
		else:
			self._show_hint_label(_("SAFE"), expose_period=2.0,
								sound='safe')
		if self.video_recorder:
			self.video_recorder.set_event(RECORDING_EVENTS.ARMED, 'on' if value else 'off')
		self._trigger_update_control_data()
		if self.motion_detector:
			self.motion_detector.invalidate_motion_detection('armed', valid_until=True if value else None)

	def on_video_recorder_req(self, instance, value):
		if self.video_recorder:
			self.video_recorder.set_event(RECORDING_EVENTS.RECORD, value)
			if value == 'off' and self.video_montage_target:
				for monitor in self.video_montage_target.video_monitors:
					if monitor and monitor.video_widget and monitor.video_widget:
						monitor.video_widget.split_files_by_player()

	def on_video_recorder_settings(self):
		if self.video_recorder:
			self.video_recorder.open_settings_dialog()

	def on_fire_req(self, instance, value):
		self.fire = (value and self.armed)

	def on_fire(self, instance, value):
		Logger.info("Control: Fire %s"%value)
		self._trigger_update_control_data()
		if value:
			self.burst_duration = (self.fire_burst_trigtime + self.fire_burst / self.fire_burst_firerate) if self.fire_burst else 0
			self._hide_hint_label(duration=0.1)
			self.fire_seq += 1
		else:
			self.burst_duration = 0

		if self.motion_detector:
			self.motion_detector.invalidate_motion_detection('fire',  valid_until=True if value else None)
		
	def on_fire_burst(self, instance, value):
		Logger.info("Control: Fire burst! %d"%value)
		self._show_hint_label(self.BURST_NAMES.get(self.fire_burst, _("Burst: %d")%self.fire_burst))

	def on_motion_slow(self, instance, value):
		self.throttle_gain = self.slow_gain if self.motion_slow else 1.0
		self._trigger_update_control_data()

	def on_axisunit_slow(self, instance, value):
		self._trigger_update_control_data()
		if value and check_cam_in_group(self.sight_cam_selected, 'wide'):
			Logger.info('Control: Sight fine cam %s selected by slow axisunit', self.sight_fine_cam_selected)
			self.sight_cam_selected = self.sight_fine_cam_selected
		elif not value and check_cam_in_group(self.sight_cam_selected, 'tele'):
			Logger.info('Control: Sight coarse cam %s selected by fast axisunit', self.sight_coarse_cam_selected)
			self.sight_cam_selected = self.sight_coarse_cam_selected
		elif check_cam_in_group(self.sight_cam_selected, 'thermo'):
			Logger.info('Control: Sight pip cam selected by slow %s axisunit with thermo', value)
			self._update_pip_order()

	def on_axisunit_center(self, instance, value):
		# discard moving to target on center axisunit
		self.auto_move_to_target = False
		self._trigger_update_control_data()

	def on_lights_on(self, instance, value):
		if value:
			self._show_hint_label(_('LIGHTS ON'))
		else:
			self._show_hint_label(_('Lights off'))
		self._trigger_update_control_data()

	def on_headlight(self, instance, value):
		if value:
			self._show_hint_label(_('HEADLIGHT ON'))
		else:
			self._show_hint_label(_('Headlight off'))
		self._trigger_update_control_data()

	def on_marker_light(self, instance, value):
		self._trigger_update_control_data()
	
	def on_charging_enable(self, instance, value):
		if value:
			self._show_hint_label(_('CHARGING ENABLED'))
		else:
			self._show_hint_label(_('Charging disabled'))
		self._trigger_update_control_data()

	def on_steer_mode(self, instance, value):
		if value == 'pull':
			self._show_hint_label(_('PULL mode'))
		elif value == 'diff':
			self._show_hint_label(_('DIFFERENTIAL steering'))
		else:
			self._show_hint_label(_('Normal steering'))
		self._trigger_update_control_data()

	def on_followme(self, instance, value):
		print(f'on_followme:  value', value, '; button_followme state: ', self.buttons_layout_drive.get('button_followme'))
		if self.followme_on:
			self.followme_on = False
			self.buttons_layout_drive.set(button_followme='off')
		else:
			self.followme_on =True
		self._trigger_update_control_data()

	def on_horn_on(self, instance, value):
		self._trigger_update_control_data()
		
	def on_steerassist_on(self, instance, value):
		self._trigger_update_control_data()
		
	def on_cams_quality(self, instance, value):
		Logger.info("Control: ALL cams %s quality", value)
		self.drive_cams_quality = self.sight_cams_quality = self.cams_quality

	def _get_onvif_addresses(self, video_montages, prime_only=False):
		onvif_addresses = {}
		for video_montage in video_montages:
			if video_montage:
				for video_id, cam_config in video_montage.cameras_config.items():
					if cam_config and (prime_only is not True or video_id == video_montage.prime):
						onvif_address = cam_config.onvif_address
						if onvif_address:
							onvif_addresses[video_id] = onvif_address
		return onvif_addresses

	def _get_current_scope(self):
		if self.sight_cam_selected == self.sight_fine_cam_selected:
			scope = self.fine_scope
		elif self.sight_cam_selected == self.sight_thermo_cam_selected:
			scope = self.thermo_scope
		elif self.axisunit_slow:
			scope = self.fine_scope
		else:
			scope = self.coarse_scope
		return scope

	def _get_button_disabled_setters(self, buttons_layouts, name):
		res = []
		names = name if list in type(name).__mro__ else [name]
		for buttons_layout in buttons_layouts:
			if buttons_layout:
				for n in names:
					res.append(buttons_layout.setter('disabled_' + n))
		return res

	def _cams_reset(self, video_montages, on_done=None):

		@run_in_thread(on_done=on_done, no_flood=True, mandatory_call_last_if_flood=False)
		def cams_reset(onvif_addresses):
			@ignore_exceptions
			def reset_cam(camera_id, onvif_address):
				if onvif_address:
					c = Camera(onvif_address)
					if not self._camera_states[camera_id]['ignore_ircut_filter']:
						c.off_on_ircut_filter()
					c.set_brightness(0.5)
					c.set_contrast(0.5)
					c.set_color_saturation(0.5)
					self._camera_states[camera_id]['night'] = False
					self.update_camera_buttons(self.video_montage_target)
			sub_threads = [Thread(
					name=f'reset camera request {onvif_address}',
					target=reset_cam,
					args=(camera_id, onvif_address,),
				) for camera_id, onvif_address in onvif_addresses.items()]
			[t.start() for t in sub_threads]
			[t.join() for t in sub_threads]

		cams_reset(self._get_onvif_addresses(video_montages, prime_only=True))
		
	def _set_cams_night(self, night, video_montages, on_done=None):
		@run_in_thread(on_done=on_done, no_flood=True)
		def set_cams_night(onvif_addresses):
			@ignore_exceptions
			def set_cam_night(camera_id, onvif_address, value):
				cam = Camera(onvif_address)
				if not self._camera_states[camera_id]['ignore_ircut_filter']:
					cam.set_ircut_filter('off' if value else 'on')
				cam.set_colorful(not value)
				self._camera_states[camera_id]['night'] = bool(value)
			sub_threads = [Thread(
				name=f'night mode request {onvif_address}',
				target=set_cam_night,
				args=(camera_id, onvif_address, night),
			) for camera_id, onvif_address in onvif_addresses.items()]
			[t.start() for t in sub_threads]
			[t.join() for t in sub_threads]

		set_cams_night(self._get_onvif_addresses(video_montages, prime_only=True))

	def _adjust_cams_contrast(self, value=None, delta=0.0, video_montages=None, btn_setters=None):
		self.change_cams_prop(value, delta, video_montages, key='contrast', btn_setters=btn_setters)

	def _adjust_cams_brightness(self, value=None, delta=0.0, video_montages=None, btn_setters=None):
		self.change_cams_prop(value, delta, video_montages, key='brightness', btn_setters=btn_setters)

	def change_cams_prop(self, value=None, delta=0.0, video_montages=None, key='', btn_setters=None):
		[s(self, True) for s in btn_setters]
		initial_worker_params = {'delta': 0, 'value': None, 'in_progress': False, 'new_request_arrived': False, 'last_requested_value': None}
		_workers = self.change_cams_prop.workers[key]

		def _change_cam_prop(camera_id, onvif_address):
			_workers[camera_id]['in_progress'] = True
			try:
				cam = Camera(onvif_address)
				while _workers[camera_id]['new_request_arrived'] is True:
					_workers[camera_id]['new_request_arrived'] = False
					if _workers[camera_id]['value'] is None:
						val = getattr(cam, f"get_{key}")()
						#  yes, we need two identical values check (['value'] can be changed during cam.get_brightness() request)
						if _workers[camera_id]['value'] is None:
							_workers[camera_id]['value'] = val
						else:
							val = _workers[camera_id]['value']
					else:
						val = _workers[camera_id]['value']

					val = max(0, min(1, val + _workers[camera_id]['delta']))

					if _workers[camera_id]['last_requested_value'] != val:
						getattr(cam, f"set_{key}")(val)
						_workers[camera_id]['last_requested_value'] = val
						self._camera_states[camera_id][key] = val
				_workers[camera_id] = initial_worker_params.copy()
			except Exception as e:
				Logger.error("Exception %s", e)
				_workers[camera_id]['in_progress'] = False

		for camera_id, o_addr in self._get_onvif_addresses(video_montages, prime_only=True).items():
			if camera_id not in _workers:
				_workers[camera_id] = initial_worker_params.copy()
			if value is None:
				if delta:
					_workers[camera_id]['delta'] += delta
				else:
					_workers[camera_id]['delta'] = 0
					_workers[camera_id]['value'] = 0.5
			else:
				_workers[camera_id]['delta'] = 0
				_workers[camera_id]['value'] = value

			_workers[camera_id]['new_request_arrived'] = True
			if not _workers[camera_id]['in_progress']:
				_workers[camera_id]['in_progress'] = (_change_cam_prop, [camera_id, o_addr], {})

		[Thread(target=cd['in_progress'][0], args=cd['in_progress'][1]).start() for o_addr, cd in _workers.items() if isinstance(cd['in_progress'], tuple)]
		[s(self, False) for s in btn_setters]

	change_cams_prop.workers = {'brightness': {}, 'contrast': {}}

	@mainthread
	def enable_setters(self, setters, enable=True):
		if setters:
			for s in setters:
				s(self, not bool(enable))

	def on_cams_night(self, instance, value):
		Logger.info("Control: ALL cams night %s", value)
		self.sight_cams_night = self.drive_cams_night = self.cams_night

	def on_drive_cams_night_req(self, instance, value):
		# TODO OZ: copy from on_sight_cams_night_req
		btn_setters = self._get_button_disabled_setters((self.buttons_layout_drive,), 'drive_cams_night')
		self.enable_setters(btn_setters, False)
		self._set_cams_night(value, video_montages=(self.video_montage_drive,), on_done=lambda res: self.enable_setters(btn_setters, True))

	def on_sight_cams_night_req(self, instance, value):
		if self.sight_cams_night != value:
			self.sight_cams_night = value
			btn_setters = self._get_button_disabled_setters(
					(self.buttons_layout_target or self.buttons_layout_drive,), ['sight_cams_night_req', 'sight_cams_reset'])
			self.enable_setters(btn_setters, False)
			self._set_cams_night(value, video_montages=(self.video_montage_target,), on_done=lambda res: self.enable_setters(btn_setters, True))

	def on_brightness_more(self, *args):
		(cam_value, cam_delta) = (None, 0.1)
		if HardwareBrightness:
			hb = HardwareBrightness()
			if hb.brightness < 0.02:
				cam_value = 0.5
			if hb.brightness < 0.98:
				cam_delta = 0.0
				hb.brightness = min(1.0, hb.brightness + 0.2)
				Logger.info("Control: brightness+ %d%%"%int(hb.brightness*100))
			else:
				hb.brightness = 1.0
		if cam_value or cam_delta:
			Logger.info("Control: camera brightness =%s +%s", cam_value, cam_delta)
			self._adjust_cams_brightness(
				value=cam_value,
				delta=cam_delta,
				video_montages=(self.video_montage_drive, self.video_montage_target,),
				btn_setters=self._get_button_disabled_setters(
					(self.buttons_layout_target or self.buttons_layout_drive,),
					'brightness_more')
				)

	def on_brightness_less(self, *args):
		(cam_value, cam_delta) = (None, -0.1)
		if HardwareBrightness:
			hb = HardwareBrightness()
			if hb.brightness > 0.98:
				cam_value = 0.5
			if hb.brightness > 0.02:
				cam_delta = 0.0
				hb.brightness = max(0.01, hb.brightness - 0.2)
				Logger.info("Control: brightness- %d%%"%int(hb.brightness*100))
			else:
				hb.brightness = 0.01
		if cam_value or cam_delta:
			Logger.info("Control: camera brightness =%s -%s", cam_value, cam_delta)
			self._adjust_cams_brightness(
				value=cam_value,
				delta=cam_delta,
				video_montages=(self.video_montage_drive, self.video_montage_target,),
				btn_setters=self._get_button_disabled_setters(
					(self.buttons_layout_target or self.buttons_layout_drive,),
					'brightness_less')
				)

	def on_drive_brightness_more(self, *args):
		(cam_value, cam_delta) = (None, 0.1)
		if HardwareBrightness:
			hb = HardwareBrightness()
			if hb.brightness < 0.02:
				cam_value = 0.5
			if hb.brightness < 0.98:
				cam_delta = 0.0
				hb.brightness = min(1.0, hb.brightness + 0.2)
				Logger.info("Control: brightness+ %d%%"%int(hb.brightness*100))
			else:
				hb.brightness = 1.0
		if cam_value or cam_delta:
			Logger.info("Control: camera brightness =%s +%s", cam_value, cam_delta)
			self._adjust_cams_brightness(
				value=cam_value,
				delta=cam_delta,
				video_montages=(self.video_montage_drive,),
				btn_setters=self._get_button_disabled_setters(
					(self.buttons_layout_drive,),
					'drive_brightness_more')
				)

	def on_drive_brightness_less(self, *args):
		(cam_value, cam_delta) = (None, -0.1)
		if HardwareBrightness:
			hb = HardwareBrightness()
			if hb.brightness > 0.98:
				cam_value = 0.5
			if hb.brightness > 0.02:
				cam_delta = 0.0
				hb.brightness = max(0.01, hb.brightness - 0.2)
				Logger.info("Control: brightness- %d%%"%int(hb.brightness*100))
			else:
				hb.brightness = 0.01
		if cam_value or cam_delta:
			Logger.info("Control: camera brightness =%s -%s", cam_value, cam_delta)
			self._adjust_cams_brightness(
				value=cam_value,
				delta=cam_delta,
				video_montages=(self.video_montage_drive,),
				btn_setters=self._get_button_disabled_setters(
					(self.buttons_layout_drive,),
					'drive_brightness_less')
				)

	def on_sight_brightness_more(self, *args):
		(cam_value, cam_delta) = (None, 0.1)
		if HardwareBrightness:
			hb = HardwareBrightness()
			if hb.brightness < 0.02:
				cam_value = 0.5
			if hb.brightness < 0.98:
				cam_delta = 0.0
				hb.brightness = min(1.0, hb.brightness + 0.2)
				Logger.info("Control: brightness+ %d%%"%int(hb.brightness*100))
			else:
				hb.brightness = 1.0
		if cam_value or cam_delta:
			Logger.info("Control: camera brightness =%s +%s", cam_value, cam_delta)
			self._adjust_cams_brightness(
				value=cam_value,
				delta=cam_delta,
				video_montages=(self.video_montage_target,),
				btn_setters=self._get_button_disabled_setters(
					(self.buttons_layout_target or self.buttons_layout_drive,),
					'sight_brightness_more')
				)

	def on_sight_brightness_less(self, *args):
		(cam_value, cam_delta) = (None, -0.1)
		if HardwareBrightness:
			hb = HardwareBrightness()
			if hb.brightness > 0.98:
				cam_value = 0.5
			if hb.brightness > 0.02:
				cam_delta = 0.0
				hb.brightness = max(0.01, hb.brightness - 0.2)
				Logger.info("Control: brightness- %d%%"%int(hb.brightness*100))
			else:
				hb.brightness = 0.01
		if cam_value or cam_delta:
			Logger.info("Control: camera brightness =%s -%s", cam_value, cam_delta)
			self._adjust_cams_brightness(
				value=cam_value,
				delta=cam_delta,
				video_montages=(self.video_montage_target,),
				btn_setters=self._get_button_disabled_setters(
					(self.buttons_layout_target or self.buttons_layout_drive,),
					'sight_brightness_less')
				)

	def on_contrast_more(self, *args):
		self._adjust_cams_contrast(
			delta=0.2,
			video_montages=(self.video_montage_drive, self.video_montage_target),
			btn_setters=self._get_button_disabled_setters(
				(self.buttons_layout_drive, self.buttons_layout_target),
				'contrast_more')
			)

	def on_contrast_less(self, *args):
		self._adjust_cams_contrast(
			delta=-0.2,
			video_montages=(self.video_montage_drive, self.video_montage_target),
			btn_setters=self._get_button_disabled_setters(
				(self.buttons_layout_drive, self.buttons_layout_target),
				'contrast_less')
			)

	def on_drive_contrast_more(self, *args):
		self._adjust_cams_contrast(
			delta=0.2,
			video_montages=(self.video_montage_drive, self.video_montage_target),
			btn_setters=self._get_button_disabled_setters(
				(self.buttons_layout_drive, self.buttons_layout_target),
				'drive_contrast_more')
			)

	def on_drive_contrast_less(self, *args):
		self._adjust_cams_contrast(
			delta=-0.2,
			video_montages=(self.video_montage_drive,),
			btn_setters=self._get_button_disabled_setters(
				(self.buttons_layout_drive,),
				'drive_contrast_less')
			)

	def on_sight_contrast_more(self, *args):
		self._adjust_cams_contrast(
			delta=0.2,
			video_montages=(self.video_montage_target,),
			btn_setters=self._get_button_disabled_setters(
				(self.buttons_layout_target or self.buttons_layout_drive,),
				'sight_contrast_more')
			)

	def on_sight_contrast_less(self, *args):
		self._adjust_cams_contrast(
			delta=-0.2,
			video_montages=(self.video_montage_target,),
			btn_setters=self._get_button_disabled_setters(
				(self.buttons_layout_target or self.buttons_layout_drive,),
				'sight_contrast_less')
			)

	def on_drive_cams_reset(self, *args):
		btn_setters = self._get_button_disabled_setters(
			(self.buttons_layout_drive,),
			['drive_cams_night_req', 'drive_cams_reset', 'drive_brightness_more', 'drive_brightness_less', 'drive_contrast_more', 'drive_contrast_less'])
		self.enable_setters(btn_setters, False)

		@mainthread
		def on_done(res):
			self.setter('drive_cams_night')(self, False)
			self.enable_setters(btn_setters, True)

		self._cams_reset(video_montages=(self.video_montage_drive,), on_done=on_done)


	def on_sight_cams_reset(self, *args):
		btn_setters = self._get_button_disabled_setters((
			self.buttons_layout_target or self.buttons_layout_drive,),
			['sight_cams_night_req', 'sight_cams_reset', 'sight_brightness_more', 'sight_brightness_less', 'sight_contrast_more', 'sight_contrast_less'])
		self.enable_setters(btn_setters, False)

		@mainthread
		def on_done(res):
			self.enable_setters(btn_setters, True)
			self.update_camera_buttons(self.video_montage_target)

		self._cams_reset(video_montages=(self.video_montage_target,), on_done=on_done)

	def on_cams_reset(self, *args):
		btn_setters = self._get_button_disabled_setters(
			(self.buttons_layout_drive, self.buttons_layout_target,),
			['sight_cams_night_req', 'sight_cams_reset', 'sight_brightness_more', 'sight_brightness_less', 'sight_contrast_more', 'sight_contrast_less',
			 'drive_cams_night_req', 'drive_cams_reset', 'drive_brightness_more', 'drive_brightness_less', 'drive_contrast_more', 'drive_contrast_less'])
		self.enable_setters(btn_setters, False)

		@mainthread
		def on_done(res):
			self.enable_setters(btn_setters, True)

		self._cams_reset(video_montages=(self.video_montage_drive, self.video_montage_target,), on_done=on_done)

	def on_cams_pip(self, instance, value):
		Logger.info("Control: ALL cams PIP %s", value)
		self.drive_cams_pip = self.sight_cams_pip = self.cams_pip

	def on_drive_cam_selected(self, instance, value):
		if self.reverse:
			# avoid both cams is the same
			if self.drive_cam_selected == self.drive_front_cam_selected:
				self.drive_front_cam_selected = 'front'
			self.drive_rear_cam_selected = self.drive_cam_selected
		else:
			# avoid both cams is the same
			if self.drive_cam_selected == self.drive_rear_cam_selected:
				self.drive_rear_cam_selected = 'rear'
			self.drive_front_cam_selected = self.drive_cam_selected
			
	def on_drive_front_cam_selected(self, instance, value):
		Logger.info("Control: Drive front cam %s", value)
		if self.video_montage_drive:
			self.video_montage_drive.monitors[0] = value
			if not self.reverse:
				self.video_montage_drive.prime = value
			if self.guidelines:
				self._update_guideline_bindings()

	def on_drive_rear_cam_selected(self, instance, value):
		Logger.info("Control: Drive rear cam %s", value)
		if self.video_montage_drive:
			self.video_montage_drive.monitors[1] = value
			if self.reverse:
				self.video_montage_drive.prime = value
			if self.guidelines:
				self._update_guideline_bindings()

	def on_drive_cams_quality(self, instance, value):
		Logger.info("Control: CAM %s quality", value)
		self._show_hint_label(_("Video quality: %s"%value.upper()))
		if self.video_montage_drive:
			self.video_montage_drive.quality = value

	def on_drive_cams_pip(self, instance, value):
		Logger.info("Control: CAM PIP %s", value)
		if self.video_montage_drive:
			self.video_montage_drive.show_limit = 2 if value else 1

	def on_sight_cam_selected(self, instance, value):
		if check_cam_in_group(value, 'wide'):
			Logger.info("Control: SIGHT cam selected %s to coarse, axisunit fast forced", value)
			self.sight_coarse_cam_selected = value
			self.axisunit_slow = False
		elif check_cam_in_group(value, 'tele'):
			Logger.info("Control: SIGHT cam selected %s to fine, axisunit slow forced", value)
			self.sight_fine_cam_selected = value
			self.axisunit_slow = True
		elif check_cam_in_group(value, 'thermo'):
			Logger.info("Control: SIGHT cam selected %s to fine", value)
			self.sight_thermo_cam_selected = value
			self._update_pip_order()
		elif self.axisunit_slow:
			Logger.info("Control: SIGHT cam selected %s to fine", value)
			self.sight_fine_cam_selected = value
		else:
			Logger.info("Control: SIGHT cam selected %s to coarse", value)
			self.sight_coarse_cam_selected = value
		if self.coarse_scope:
			self._update_coarse_scope_bindings()
			self.coarse_scope.show_legend = (value == self.sight_coarse_cam_selected)
		if self.fine_scope:
			self._update_fine_scope_bindings()
			self.fine_scope.show_legend = (value == self.sight_fine_cam_selected)
		if self.thermo_scope:
			self._update_thermo_scope_bindings()
			self.thermo_scope.show_legend = (value == self.sight_thermo_cam_selected)
		if self.video_montage_target:
			self.video_montage_target.prime = self.sight_cam_selected
			self.update_camera_buttons(self.video_montage_target)

		self.sight_zoom = self.video_montage_target.get_prime_camera_config().zoom or 1.0
		self._update_pip_order()

	def update_camera_buttons(self, montage):
		prime_camera_id = montage.prime
		camera_state = self._camera_states.get(prime_camera_id, None)
		if camera_state:
			is_night = camera_state.get('night', None)
			if is_night is not None:
				self.sight_cams_night = is_night
				self.sight_cams_night_req = is_night

	def on_sight_coarse_cam_selected(self, instance, value):
		Logger.info("Control: SIGHT coarse cam %s", value)
		if self.video_montage_target:
			self.video_montage_target.monitors[0] = value
			if self.coarse_scope:
				self._update_coarse_scope_bindings()
				self._update_coarse_scope_elevation()
				self._update_coarse_scope_windage()
			self._update_sight_zoom_max()
		if not self.axisunit_slow:
			self.sight_cam_selected = value

	def on_sight_fine_cam_selected(self, instance, value):
		Logger.info("Control: SIGHT fine cam %s", value)
		if self.video_montage_target:
			self.video_montage_target.monitors[1] = value
			if self.fine_scope:
				self._update_fine_scope_bindings()
				self._update_fine_scope_elevation()
				self._update_fine_scope_windage()
			self._update_sight_zoom_max()
		if self.axisunit_slow:
			self.sight_cam_selected = value

	def on_sight_thermo_cam_selected(self, instance, value):
		Logger.info("Control: SIGHT thermo cam %s", value)
		if self.video_montage_target:
			self.video_montage_target.monitors[2] = value
			if self.thermo_scope:
				self._update_thermo_scope_bindings()
				self._update_thermo_scope_elevation()
				self._update_thermo_scope_windage()

	def on_sight_cams_quality(self, instance, value):
		Logger.info("Control: SIGHT %s quality", value)
		self._show_hint_label(_("Video quality: %s"%value.upper()))
		if self.video_montage_target:
			self.video_montage_target.quality = value

	def on_sight_cams_pip(self, instance, value):
		Logger.info("Control: SIGHT PIP %s", value)
		if self.video_montage_target:
			self.video_montage_target.show_limit = 2 if value else 1

	def on_sight_zoom(self, instance, value):
		Logger.info("Control: SIGHT zoom {}".format(value))
		if self.video_montage_target:
			self.video_montage_target.video_scale = value

	def on_sight_zoom_in(self, *args):
		Logger.info("Control: SIGHT zoom in from {} {}x".format(self.sight_cam_selected, self.sight_zoom))
		zooms = self.video_montage_target.get_prime_camera_config().zooms
		if zooms:
			for zoom in zooms:
				if zoom > self.sight_zoom:  
					break
			else: # max_reached
				zoom = None
		else:
			zoom = round(self.sight_zoom * 1.2, 1)
		if check_cam_in_group(self.sight_cam_selected, 'wide'):
			if zoom and zoom <= self.sight_zoom_max_wide:
				self.sight_zoom = zoom
			else:
				Logger.info('Control: axisunit cam tele by zoom in {}x'.format(zoom))
				self.sight_cam_selected = self.sight_fine_cam_selected
				self.sight_zoom = 1
		else:
			self.sight_zoom = min(zoom, self.sight_zoom_max_tele) if zoom else self.sight_zoom_max_tele

	def on_sight_zoom_out(self, *args):
		Logger.info("Control: SIGHT zoom out from {} {}x".format(self.sight_cam_selected, self.sight_zoom))
		zooms = self.video_montage_target.get_prime_camera_config().zooms
		if zooms:
			for zoom in reversed(zooms):
				if zoom < self.sight_zoom:  
					break
			else: # max_reached
				zoom = None
		else:
			zoom = round(self.sight_zoom / 1.2, 1)
		if check_cam_in_group(self.sight_cam_selected, 'tele'):
			if zoom and zoom >= 1:
				self.sight_zoom = zoom
			else:
				Logger.info('Control: axisunit cam wide by zoom out{}x'.format(zoom))
				self.sight_cam_selected = self.sight_coarse_cam_selected
				self.sight_zoom = round(self.sight_zoom_max_wide, 1)
		else:
			self.sight_zoom = max(zoom, 1.0) if zoom else 1.0
		

	def on_sight_zoom_loop(self, *args):
		Logger.info("Control: SIGHT zoom loop from {} {}x".format(self.sight_cam_selected, self.sight_zoom))
		if self.sight_cam_selected == 'tele' and self.sight_zoom >= self.sight_zoom_max_tele:
			self.on_sight_zoom_reset(*args)
		else:
			self.on_sight_zoom_in(*args)
		
	def on_sight_zoom_reset(self, *args):
		Logger.info("Control: SIGHT zoom reset from {} {}x".format(self.sight_cam_selected, self.sight_zoom))
		self.sight_cam_selected = self.sight_coarse_cam_selected
		self.sight_zoom = 1.0
		
	def on_sight_mark_loop(self, *args):
		scope = self._get_current_scope()
		if scope:
			scope.switch_mark()
			Logger.info("Control: SIGHT mark changed to {}".format(scope.mark))
		
	def on_sight_grid_loop(self, *args):
		scope = self._get_current_scope()
		if scope:
			scope.switch_grid()
			Logger.info("Control: SIGHT grid changed to {}".format(scope.grid))

	def on_sight_rangefinder_loop(self, *args):
		scope = self._get_current_scope()
		if scope:
			scope.switch_rangefinder()
			Logger.info("Control: SIGHT rangefinder changed to {}".format(scope.rangefinder))
		
	def on_sight_legend_loop(self, *args):
		scope = self._get_current_scope()
		if scope:
			scope.toggle_legend()
			Logger.info("Control: SIGHT legend changed to {}".format(scope.show_legend))
		
	def on_map_zoom_in(self):
		if self.map:
			self.map.zoom_in()
			Logger.info("Control: Map zoom in %d" % self.map.zoom)

	def on_map_zoom_out(self):
		if self.map:
			self.map.zoom_out()
			Logger.info("Control: Map zoom out %d" % self.map.zoom)

	def on_map_zoom_loop(self):
		if self.map:
			Logger.info("Control: Map zoom loop %d" % self.map.zoom)
			self.map.zoom_loop()

	def on_menu_ok(self):
		Logger.info("Control: menu ok")
		if self.popup and self.popup.is_active:
			self.popup.do_validate()
		elif self.buttons_layout_drive:
			self.buttons_layout_drive.do_click()
		elif self.buttons_layout_target:
			self.buttons_layout_target.do_click()

	def on_menu_cancel(self):
		Logger.info("Control: menu cancel")
		if self.popup and self.popup.is_active:
			self.popup.do_cancel()
		elif self.buttons_layout_drive:
			self.buttons_layout_drive.do_cancel()
		elif self.buttons_layout_target:
			self.buttons_layout_target.do_cancel()

	def on_menu_next(self):
		Logger.info("Control: menu next")
		if self.popup and self.popup.is_active:
			self.popup.do_next()
		elif self.buttons_layout_drive:
			self.buttons_layout_drive.do_next()
		elif self.buttons_layout_target:
			self.buttons_layout_target.do_next()

	def on_menu_prev(self):
		Logger.info("Control: menu prev")
		if self.popup and self.popup.is_active:
			self.popup.do_prev()
		elif self.buttons_layout_drive:
			self.buttons_layout_drive.do_prev()
		elif self.buttons_layout_target:
			self.buttons_layout_target.do_prev()

	def on_ammo_reload_request(self, *args):
		if self._reload_ammo:
			self.on_reload_ammo()
		else:
			self._popup_loaded_ammo()
		
	def on_azimuth_request(self, *args):
		self._popup_azimuth()

	def on_lat_request(self, *args):
		self._popup_lat()

	def on_lon_request(self, *args):
		self._popup_lon()

	def on_alt_request(self, *args):
		self._popup_alt()

	def on_sector_set_left_request(self, *args):
		if self.status_axisunit and self.status_axisunit.act_rotationP is not None:
			self.status_axisunit.rotation_from = self._rotation_from = self.status_axisunit.act_rotationP
			self._show_sector_horizontal_hint()
		if self.target_menu:
			self.target_menu.dismiss()

	def on_sector_set_right_request(self, *args):
		if self.status_axisunit and self.status_axisunit.act_rotationP is not None:
			self.status_axisunit.rotation_to = self._rotation_to = self.status_axisunit.act_rotationP
			self._show_sector_horizontal_hint()
		if self.target_menu:
			self.target_menu.dismiss()

	def on_sector_set_up_request(self, *args):
		if self.status_axisunit and self.status_axisunit.act_elevationP is not None:
			self.status_axisunit.elevation_to = self._elevation_to = self.status_axisunit.act_elevationP
			self._show_sector_vertical_hint()
		if self.target_menu:
			self.target_menu.dismiss()

	def on_sector_set_down_request(self, *args):
		if self.status_axisunit and self.status_axisunit.act_elevationP is not None:
			self.status_axisunit.elevation_from = self._elevation_from = self.status_axisunit.act_elevationP
			self._show_sector_vertical_hint()
		if self.target_menu:
			self.target_menu.dismiss()

	def on_sector_store_request(self, *args):
		if self.config:
			cfg = self.config.config
			cfg.set('rover', 'rotation_from', self._rotation_from)
			cfg.set('rover', 'rotation_to', self._rotation_to)
			cfg.set('rover', 'elevation_from', self._elevation_from)
			cfg.set('rover', 'elevation_to', self._elevation_to)
			cfg.write()
			self._show_sector_stored_hint()
		if self.target_menu:
			self.target_menu.dismiss()

	def on_sector_reset_request(self, *args):
		if self.target_menu:
			self.target_menu.dismiss()
		if (self.status_axisunit
			and self._rotation_from == self.status_axisunit.rotation_minP
			and self._rotation_to == self.status_axisunit.rotation_maxP
			and self._elevation_from == self.status_axisunit.elevation_minP
			and self._elevation_to == self.status_axisunit.elevation_maxP
		):
			self.axisunit_force_home = True
			Clock.schedule_once(lambda dt:
							self.setter('axisunit_force_home')(self, False), 1.0)
			self._trigger_update_control_data()
			self._show_forced_homing_hint()
			return

		if self.status_axisunit and self.status_axisunit.rotation_minP is not None:
			self.status_axisunit.rotation_from = self._rotation_from = self.status_axisunit.rotation_minP
		if self.status_axisunit and self.status_axisunit.rotation_maxP is not None:
			self.status_axisunit.rotation_to = self._rotation_to = self.status_axisunit.rotation_maxP

		if self.status_axisunit and self.status_axisunit.elevation_minP is not None:
			self.status_axisunit.elevation_from = self._elevation_from = self.status_axisunit.elevation_minP
		if self.status_axisunit and self.status_axisunit.elevation_maxP is not None:
			self.status_axisunit.elevation_to = self._elevation_to = self.status_axisunit.elevation_maxP

		self._show_sector_reset_hint()

	def switch_compass_size(self, *args):
		if self.compass_size == 1:
			self.compass_size = 0.3
		else:
			self.compass_size = 1
		self._update_compass_size()

	def switch_rover_schema_size(self, *args):
		if self.rover_schema_size == 1:
			self.rover_schema_size = 0.4
		else:
			self.rover_schema_size = 1
		self._update_rover_schema_size()

	def switch_rws_schema_size(self, *args):
		if self.rws_schema_size == 1:
			self.rws_schema_size = 0.4
		else:
			self.rws_schema_size = 1
		self._update_rws_schema_size()

	def _update_compass_size(self, *args):
		ar_layout = self.compass.parent
		ar_layout.size_hint = (0.3 * self.compass_size, 0.3 * self.compass_size)

	def _update_rover_schema_size(self, *args):
		ar_layout = self.rover_schema.parent
		ar_layout.size_hint = (0.2 * self.rover_schema_size, 0.3 * self.rover_schema_size)

	def _update_rws_schema_size(self, *args):
		ar_layout = self.rws_schema.parent
		ar_layout.size_hint = (0.2 * self.rws_schema_size, 0.3 * self.rws_schema_size)

	def _update_color_schema(self, *args, init=False):
		Logger.info("Control: HUD color schema %s"%self.hud_color_schema)
		self._update_guidelines_color_schema(*args)
		self._update_scopes_color_schema(*args, init=init)
		self._update_status_axisunit_color_schema(*args)

	def _update_guidelines_color_schema(self, *args):
		if self.config:
			hud_color = self.config.getcolor('hud','color', preset=self.hud_color_schema)
			hud_shadow_color = self.config.getcolor('hud','shadow_color', preset=self.hud_color_schema)
			if self.guidelines:
				self.guidelines.line_color = hud_color
				self.guidelines.shadow_color = hud_shadow_color 
				self.guidelines.line_width = self.config.getint('hud', 'guideline_width', preset=self.hud_color_schema)
				self.guidelines.shadow_width = self.config.getint('hud', 'guideline_shadow_width', preset=self.hud_color_schema)

	def _update_scopes_color_schema(self, *args, init=False):
		if self.config:
			hud_color = self.config.getcolor('hud','color', preset=self.hud_color_schema)
			hud_shadow_color = self.config.getcolor('hud','shadow_color', preset=self.hud_color_schema)
			hud_mark_color = self.config.getcolor('hud','mark_color', preset=self.hud_color_schema)
			for scope, name in (self.coarse_scope, 'coarse'), (self.fine_scope, 'fine'), (self.thermo_scope, 'thermo'):
				if scope:
					scope.grid_color = hud_color
					scope.mark_color = hud_mark_color
					scope.shadow_color = hud_shadow_color
					scope.line_width = self.config.getint('hud', 'scope_line_width', preset=self.hud_color_schema)
					scope.shadow_width = self.config.getint('hud', 'scope_shadow_width', preset=self.hud_color_schema)
			if self.targets_box:
				self.targets_box.line_color = hud_color
				self.targets_box.shadow_color = hud_shadow_color
		self._update_scopes_types(init=init)

	def _update_scopes_types(self, *args, init=False):
		if self.config:
			for scope, name in (self.coarse_scope, 'coarse'), (self.fine_scope, 'fine'), (self.thermo_scope, 'thermo'):
				if scope:
					if self.rangefinder_on:
						scope.prev_grid = scope.grid
						scope.prev_mark = scope.mark
						scope.grid = 'none'
						scope.mark = 'none'
						scope.rangefinder = self.config.get('hud', 'rangefinder_mark', preset=self.hud_color_schema) 
						scope.rangefinder_size = self.config.getfloat('hud', 'rangefinder_mark_size', preset=self.hud_color_schema)
					else:

						_scope = getattr(self, f'{name}_scope')
						scope.grid = self.config.get(
							'hud', name + '_grid', preset=self.hud_color_schema
						) if init else (_scope.grid if _scope.grid != 'none' else _scope.prev_grid)
						scope.mark = self.config.get(
							'hud', name + '_mark', preset=self.hud_color_schema
						) if init else (_scope.mark if _scope.mark != 'none' else _scope.prev_mark)
						scope.rangefinder = self.config.get('hud', name + '_rangefinder', preset=self.hud_color_schema)
						scope.rangefinder_size = None
			if self.fine_scope:
				self._update_fine_scope_elevation()
				self._update_fine_scope_windage()
			if self.coarse_scope:
				self._update_coarse_scope_elevation()
				self._update_coarse_scope_windage()
			if self.thermo_scope:
				self._update_thermo_scope_elevation()
				self._update_thermo_scope_windage()

	def _update_status_axisunit_color_schema(self, *args):
		if self.config:
			hud_color = self.config.getcolor('hud','color', preset=self.hud_color_schema)
			hud_shadow_color = self.config.getcolor('hud','shadow_color', preset=self.hud_color_schema)
			if self.status_axisunit:
				self.status_axisunit.line_color = hud_color
				self.status_axisunit.shadow_color = hud_shadow_color 
				self.status_axisunit.line_width = self.config.getint('hud', 'guideline_width', preset=self.hud_color_schema)
				self.status_axisunit.shadow_width = self.config.getint('hud', 'guideline_shadow_width', preset=self.hud_color_schema)

	def _delayed_disable(self, dt):
		self.motion_enable_delayed = False
		
	def _update_control_data(self, dt):
		if self.status_ugv:
			self.status_ugv.cmd_throttle = self.throttle_out
			self.status_ugv.cmd_steer = self.steering_out
		if self.status_axisunit:
			self.status_axisunit.cmd_elevation = self.elevation + self.elevation_slow*0.1
			self.status_axisunit.cmd_rotation = self.rotation + self.rotation_slow*0.1
		if self.control_comm:
			self._update_ugv_control_data()
			self._update_rws_control_data()

	def _update_ugv_control_data(self):
		control_data = self.control_comm.ugv_control
		control_data.throttle = self.get_cmd_throttle()
		control_data.steering = self.get_cmd_steering()
		control_data.motion_enable = self.motion_enable or self.motion_enable_delayed
		control_data.motion_slow   = self.motion_slow
		control_data.engine_on     = self.engine_on
		control_data.engine_start  = self.engine_start
		control_data.lights_on     = self.lights_on
		control_data.headlight     = self.headlight
		control_data.marker_light  = self.marker_light
		control_data.charging_enable = self.charging_enable
		control_data.pull_on       = (self.steer_mode == 'pull')
		control_data.horn_on       = self.horn_on
		control_data.diffsteer_on  = (self.steer_mode == 'diff')
		control_data.steerassist_on = self.steerassist_on
		control_data.winch_out     = self.winch_out
		control_data.winch_in      = self.winch_in
		control_data.followme_on   = self.followme_on
		self.control_comm.ugv_control = control_data
			
	def _update_rws_control_data(self):
		FIRE = self.armed and self.fire
		if not FIRE:
			if self._fire_burst_end_event:
				self._fire_burst_end_event.cancel()
				self._fire_burst_end_event = None
				Logger.info("Control: Fire burst end canceled")
		elif not self._fire_burst_end_event and self.burst_duration > 0:
			Logger.info("Control: Fire burst end scheduled %d, %.2fs"%(self.fire_burst, self.burst_duration))
			self._fire_burst_end_event = Clock.schedule_interval(self._end_fire_burst, self.burst_duration)
		control_data = self.control_comm.rws_control
		control_data.rotationV = self.get_cmd_rotationV()
		control_data.elevationV = self.get_cmd_elevationV()
		control_data.rotationP = self.limit_rotationP(self.get_cmd_rotationP(control_data.rotationV, control_data.rotationP)) 
		control_data.elevationP = self.limit_elevationP(self.get_cmd_elevationP(control_data.elevationV, control_data.elevationP))
		if self.auto_move_to_target and (
			self.target_azimuth is not None and self.target_elevation is not None and
			self.axisunit_azimuth is not None and self.axisunit_elevation is not None
		): 
			dX = abs(self.target_azimuth - self.axisunit_azimuth)
			dY = abs(self.target_elevation - self.axisunit_elevation)
			if dX > 0.001 and dY > 0.001:
				if dX > dY:
					control_data.elevationV *= dY / dX
				else:
					control_data.rotationV *= dX / dY
		control_data.axisunit_enable = self.axisunit_enable
		control_data.axisunit_slow = self.axisunit_slow and not self.auto_move_to_target  
		control_data.axisunit_force_home = self.axisunit_force_home
		control_data.axisunit_vel_prio = (
			not self.auto_move_to_target
			or self.target_elevation is None
			or self.target_azimuth is None
			)  
		control_data.safetyARM = 'ARM' if self.armed else None
		control_data.FIRE = 'FIRE' if FIRE else None
		control_data.fire_duration = int(self.burst_duration * 1000)
		control_data.fire_seq = self.fire_seq
		control_data.rangefinder_seq = self.rangefinder_seq
#		self.control_comm.rws_control_data = control_data

	def get_cmd_throttle(self):
		if not self.motion_enable: return 0.0
		return -self.throttle_out if self.reverse else self.throttle_out

	def get_cmd_steering(self):
# 		if not self.motion_enable: return 0.0
		return -self.steering_out if self.reverse else self.steering_out

	def get_cmd_rotationV(self):
		if not self.axisunit_enable: return 0.0
		if self.axisunit_center: return 0.2
		if self.auto_move_to_target and self.target_azimuth is not None: return 1.0 
		if self.target_mode in ('move', 'rangefinder'):
			return self.rotation + self.rotation_slow*0.1
		return 0.0

	def get_cmd_elevationV(self):
		if not self.axisunit_enable: return 0.0
		if self.axisunit_center: return 0.2
		if self.auto_move_to_target and self.target_elevation is not None: return 1.0
		if self.target_mode in ('move', 'rangefinder'):
			return self.elevation + self.elevation_slow*0.1
		return 0.0

	def get_cmd_rotationP(self, v, prev_p):
		if not self.axisunit_enable: return None
		if self.axisunit_center: return 0.0
		if self.auto_move_to_target and self.target_azimuth is not None:
			target_azimuth = self.target_azimuth 
			return target_azimuth
		return math.pi if v > 0.0 else (-math.pi if v < 0.0 else (
			prev_p if prev_p is not None else None))

	def get_cmd_elevationP(self, v, prev_p):
		if not self.axisunit_enable: return None
		if self.axisunit_center: return 0.0
		if self.auto_move_to_target and self.target_elevation is not None:
			target_elevation = self.target_elevation
			return target_elevation 
		return math.pi if v > 0.0 else (-math.pi if v < 0.0 else (
			prev_p if prev_p is not None else None))

	def limit_rotationP(self, rotation_p):
		if rotation_p is None: return None
		return min(self._rotation_to, max(self._rotation_from, rotation_p))
		
	def limit_elevationP(self, elevation_p):
		if elevation_p is None: return None
		return min(self._elevation_to, max(self._elevation_from, elevation_p))

	def _show_hint_label(self, message, expose_period=2, sound=None, priority=None):
		Logger.info('Control: HINT: %s', '  '.join(message.splitlines()))
		if self.hint_label:
			self.hint_label.show(expose_period, text=message, sound=sound, priority=priority)

	def _hide_hint_label(self, message=None, duration=None):
		if self.hint_label:
			if message is None or self.hint_label.text == message:
				self.hint_label.hide(duration)

	def _add_slider_2d(self, property_x, range_x, property_y, range_y, pos_hint, size_hint):
		slider = Slider2DAutoZero(
						range_x=range_x,
						range_y=range_y,
						pos_hint=pos_hint,
						size_hint=size_hint,
						opacity=0.5,
						background_color=[0,0,0,0.3],
						)
		slider.bind(value_x=self.setter(property_x))
		slider.bind(value_y=self.setter(property_y))
		self.add_widget(slider)
		return slider
	
	def _add_slider(self, property, range, orientation, pos_hint, size_hint):
		slider = SliderAutoZero(
						range=range,
						value=0.0,
						orientation=orientation,
						pos_hint=pos_hint,
						size_hint=size_hint,
						opacity=0.5,
						)
		slider.bind(value=self.setter(property))
		self.add_widget(slider)
		return slider
	
	def _remove_slider(self, slider):
		if slider:
			slider.unbind()
			self.remove_widget(slider)
	
	def _update_sliders_layout(self):
		portrait = (self.width < self.height)
		if self.slider_thr_str:
			self.slider_thr_str.pos_hint, self.slider_thr_str.size_hint = \
						({'center_x':0.5,  'center_y':0.85}, (0.8, 0.3)) if portrait else \
						({'center_x':0.15, 'center_y':0.25}, (0.3, 0.5))
		if self.slider_thr:
			self.slider_thr.size_hint_y = 1.0 - 4.0 * (
						0.0 if not portrait else ( 
						float(self.slider_str.height) / float(self.height) if self.slider_str else (
						float(self.slider_rot.height) / float(self.height) if self.slider_rot else (
						0.0))))
		if self.slider_str:
			self.slider_str.size_hint_x = 1.0 - 2.0 * ( 
						0.0 if portrait else ( 
						float(self.slider_thr.width) / float(self.width) if self.slider_thr else (
						float(self.slider_ele.width) / float(self.width) if self.slider_ele else (
						0.0))))
		if self.slider_rot_ele:
			self.slider_rot_ele.pos_hint, self.slider_rot_ele.size_hint = \
						({'center_x':0.5,  'center_y':0.15}, (0.8, 0.3)) if portrait else \
						({'center_x':0.85, 'center_y':0.25}, (0.3, 0.5))
		if self.slider_ele:
			self.slider_ele.size_hint_y = 1.0 - 4.0 * ( 
						0.0 if not portrait else ( 
						float(self.slider_str.height) / float(self.height) if self.slider_str else (
						float(self.slider_rot.height) / float(self.height) if self.slider_rot else (
						0.0))))
		if self.slider_rot:
			self.slider_rot.size_hint_x = 1.0 - 2.0 * ( 
						0.0 if portrait else ( 
						float(self.slider_thr.width) / float(self.width) if self.slider_thr else (
						float(self.slider_ele.width) / float(self.width) if self.slider_ele else (
						0.0))))
	
	def _process_direction_lock(self, x, prev):
		if x == 0 and abs(x) < 0.05:
			self.throttle_allowed_direction = 0
		elif self.throttle_allowed_direction == 0 and abs(x) >= 0.2:
			self.throttle_allowed_direction = 1 if x > 0 else -1
	
	def _apply_direction_lock(self, out):
		if not samesign(out, self.throttle_allowed_direction):
			return 0
#			Logger.debug("Control: Throttle dir {} x {:.2f} out {:.2f}".format(self.throttle_allowed_direction, out, self.throttle_out))
		else:
			return out
	
	def _update_throttle_by_vel(self, dt):
		if self.brake != 0.0:
			x = -self.brake
		else:
			x = self.throttle
		if self.direction_lock: 
			self._process_direction_lock(x, self.throttle_out)
			x = self._apply_direction_lock(x)
		self.throttle_out = x * self.throttle_gain
#		Logger.debug("Control: Throttle vel {:.2f} x {:.2f} dir {} out {:.2f}".format(self.throttle_gain, x, self.throttle_allowed_direction, self.throttle_out))
	
	def _update_throttle_by_acc(self, dt):
		x = -self.brake if self.brake != 0.0 else self.throttle
#		prev = self.status_ugv.act_throttle if self.status_ugv.connected else self.throttle_out
		prev = self.throttle_out
		gain = self.acceleration_gain if samesign(x, prev) else self.brakes_gain 
		self._process_direction_lock(x, prev)
		out = limit(prev + x * gain * dt,
					top=self.slow_gain if self.motion_slow else 1.0, unidirect=False)
		if self.direction_lock:
			out = self._apply_direction_lock(out)
		self.throttle_out = out
#		Logger.debug("Control: Throttle acc {:.2f} x {:.2f} dt {:.2f} dir {} prev {} => out {:.2f}".format(gain, x, dt, self.throttle_allowed_direction, prev, self.throttle_out))
		self._trigger_throttle_updater()

	def _update_steering_by_vel(self, dt):
		self.steering_out = self.steering * self.steering_gain
#		Logger.debug("Control: Steering vel {:.2f} x {:.2f} out {:.2f}".format(self.steering_gain, x, self.steering_out))
	
	def _update_steering_by_acc(self, dt):
		x = self.steering
#		prev = self.status_ugv.act_steering if self.status_ugv.connected else self.steering_out
		prev = self.steering_out
		if x:
			out = limit(prev + x * self.steering_acceleration * dt)
		elif prev > 0:
			out = limit(prev - self.steering_recover * dt, unidirect=True)
		elif prev < 0:
			out = -limit(-prev - self.steering_recover * dt, unidirect=True)
		else:
			out = 0
		self.steering_out = out
#		Logger.debug("Control: Steering acc {:.2f} x {:.2f} dt {:.2f} prev {} => out {:.2f}".format(gain, x, dt, prev, self.steering_out))
		self._trigger_steering_updater()

	def _update_adjustment_by_joystick(self, dt):
		if self.target_mode == 'adjust' and self._trigger_adjustment_updater:
			Logger.debug("Control: Adjustment +")
			m = dt * (0.001 if self.axisunit_slow else 0.01)
			elevation = (self.elevation + self.elevation_slow*0.1) * m
			rotation = (self.rotation + self.rotation_slow*0.1) * m
			if abs(elevation) > abs(rotation):
				rotation = 0
			else:
				elevation = 0
			if self.rangefinder_on:
				self._update_rangefinder_adjustment(elevation, rotation, dt)
			else:
				self._update_scope_adjustment(elevation, rotation, dt)

	def _rangefinder_adjustment_allowed(self):
		# the logic described in RS-185
		lock_cams = self.config.getboolean('video', 'lock_cams') if self.config else True
		if self._rangefinder_present == 'external':
			return True
		elif not lock_cams and self._rangefinder_present == 'internal':
			return True
		else:
			return False

	def _update_rangefinder_adjustment(self, elevation, rotation, dt):
		t = .01
		if not self._rangefinder_adjustment_allowed():
			self._show_hint_label(_("The rangefinder adjustment is locked by config"))
		else:
			self._rangefinder_elevation += elevation
			self._rangefinder_windage += rotation
			self._update_coarse_scope_elevation()
			self._update_coarse_scope_windage()
			self._update_fine_scope_elevation()
			self._update_fine_scope_windage()
			self._update_thermo_scope_elevation()
			self._update_thermo_scope_windage()
			if self._trigger_adjustment_updater:
				self._trigger_adjustment_updater.timeout = t
				self._trigger_adjustment_updater()

	def _update_scope_adjustment(self, elevation=0, windage=0, dt=0):
		cv = self._adjust_click_value
		elevation_click_prev = divmod(self.adjust_elevation, cv)[0]
		windage_click_prev = divmod(self.adjust_windage, cv)[0]
		self.adjust_elevation += elevation
		self.adjust_windage += windage
		elevation_click = divmod(self.adjust_elevation, cv)[0]
		windage_click = divmod(self.adjust_windage, cv)[0]
		elevation_clicks = elevation_click - elevation_click_prev
		windage_clicks = windage_click - windage_click_prev
		clicks = (abs(windage_clicks), abs(elevation_clicks))
		click_sound = (None if not any(clicks)
				else 'click-clear' if self.adjust_elevation == 0.0 and self.adjust_windage == 0.0
				else 'clicks' if sum(clicks) > 1
				else 'click2' if clicks[0] and (windage_click % 5) == 0
				else 'click2' if clicks[1] and (elevation_click % 5) == 0
				else 'click')
		if click_sound:
			self._show_adjustment_hint(click_sound = click_sound)
		t = 0.05
		if not click_sound and dt > 0:
			if elevation:
				velocity = elevation / dt
				next_click = elevation_click + (cv if elevation > 0 else -cv)
				time_to_next_click = (next_click - self.adjust_elevation) / velocity
				if time_to_next_click < t:
					t = time_to_next_click
			if windage:
				windage_velocity = windage / dt
				next_click = windage_click + (cv if windage > 0 else -cv)
				time_to_next_click = (next_click - self.adjust_windage) / windage_velocity
				if time_to_next_click < t:
					t = time_to_next_click
			t = min(0.2, max(0.01, t))
#		Logger.info("Control: adj clicks (%d, %d) %s (%.5f, %.5f), t:%.2fs",
#				windage_clicks, elevation_clicks, click_sound,
#				self.adjust_windage, self.adjust_elevation, t)
		if self._trigger_adjustment_updater:
			self._trigger_adjustment_updater.timeout = t
			self._trigger_adjustment_updater()
		
	
	def _start_communication(self):
		Logger.info("Control: Communication started")
		if self.outpost_comm:
			self.outpost_comm.OnDataChanged = self.map.updateOutpostObject if self.map else None
			self.outpost_comm.Start()
		if self.control_comm:
			self.control_comm.OnConnectionLost = self.OnConnectionLost 
			self.control_comm.OnConnectionEstablished = self.OnConnectionEstablished
			self.control_comm.OnPositionFixed = self.OnPositionFixed
			self.control_comm.OnHeadingChanged = self.OnHeadingChanged
			self.control_comm.OnPositionLost = self.OnPositionLost
			self.control_comm.OnPositionChanged = self.OnPositionChanged
			if self.status_ugv:
				self.control_comm.OnUgvStateChanged = self.OnUgvStateChanged 
				self.control_comm.OnUgvTelemetryChanged = self.OnUgvTelemetryChanged
				self.control_comm.OnFollowMeChanged = self.OnFollowMeChanged
				self.control_comm.OnTemperaturesChanged = self.OnTemperaturesChanged
			if self.status_axisunit:
				self.control_comm.OnRwsStateChanged = self.OnRwsStateChanged 
				self.control_comm.OnRwsTelemetryChanged = self.OnRwsTelemetryChanged
				self.control_comm.OnDistanceChanged = self.OnDistanceChanged
			self.control_comm.Start()
		if self.rssi: self.rssi.start()
	
	def _stop_communication(self):
		if self.rssi: self.rssi.stop()
		if self.control_comm:
			self.control_comm.Stop()
			self.control_comm.OnUgvStateChanged = None 
			self.control_comm.OnRwsStateChanged = None 
			self.control_comm.OnConnectionLost = None 
			self.control_comm.OnConnectionEstablished = None
			self.control_comm.OnPositionFixed = None
			self.control_comm.OnPositionLost = None
			self.control_comm.OnPositionChanged = None
			self.control_comm.OnHeadingChanged = None
			self.control_comm.OnTemperaturesChanged = None
			self.control_comm.OnUgvTelemetryChanged = None
			self.control_comm.OnRwsTelemetryChanged = None
		if self.outpost_comm:
			self.outpost_comm.Stop()
			self.outpost_comm.OnDataChanged = None
		Logger.info("Control: Communication stoped")
	
	def OnUgvStateChanged(self):
		state = self.control_comm.ugv_state
		Logger.debug("Control: new UGV state th={:.2f}, st={:.2f}({:.0f}), Ubat={}".format(
				state.throttleV, state.steeringP, math.degrees(state.steeringP * math.pi), 
				state.batteryU))
		rev = -1 if self.reverse else 0
		self.rover_speed = state.throttleV * self._rover_speed_max * rev
		self.rover_steer = state.steeringP * math.pi
		if self.status_ugv:
			self.status_ugv.act_throttle = state.throttleV * rev
			self.status_ugv.act_steer = state.steeringP * rev
			self.status_ugv.avg_loop_time = self.control_comm.avg_loop_time
			self.status_ugv.connected = state.connected
			self.status_ugv.battery_charging = state.charging
			self.status_ugv.battery_voltage = state.batteryU
			self.status_ugv.battery_percentage = state.batteryPercentage
			self.status_ugv.battery_current = state.batteryI
			self.status_ugv.generator_voltage = state.generatorU
			self.status_ugv.generator_current = state.generatorI
			self.status_ugv.worst_cell_voltage = state.cell_lowestU
			self.status_ugv.best_cell_voltage = state.cell_highestU
			self.status_ugv.drive_faults = state.drive_faults
			self.status_ugv.followme_on = state.followme_on
		if self.status_axisunit:
			self.status_axisunit.battery_charging = state.charging
			self.status_axisunit.battery_voltage = state.batteryU
			self.status_axisunit.battery_percentage = state.batteryPercentage
			self.status_axisunit.battery_current = state.batteryI
			self.status_axisunit.generator_voltage = state.generatorU
			self.status_axisunit.generator_current = state.generatorI
			self.status_axisunit.worst_cell_voltage = state.cell_lowestU
			self.status_axisunit.best_cell_voltage = state.cell_highestU
		if self.rover_schema:
			self.rover_schema.set_rover_state_data(state)
		if self.preset in ('drive', 'dual'):
			if state.followme_on:
				if self.followme_on:
					coord_obj = self.control_comm.unit_follow_me
					if not coord_obj.state:
						self.buttons_layout_drive.set(button_followme='ready')
			else:
				#print(f"OnStateChanged: followme_on = {state.followme_on}")
				if self.followme_on:
					self.buttons_layout_drive.set(button_followme='on')
				else:
					self.followme_box.set_coordinates(control.FollowMeData(), self.status_ugv.followme_on)
					self.buttons_layout_drive.set(button_followme='off')
		# update throttle/steer by acc from actual values 
# 		if self._trigger_throttle_updater: self._trigger_throttle_updater()
# 		if self._trigger_steering_updater: self._trigger_steering_updater()

	def OnRwsStateChanged(self):
		state = self.control_comm.rws_state
		Logger.debug("Control: new RWS state rot={}, ele={}, sh={}".format(
				('-' if state.rotationP is None else '%.4f'%state.rotationP),
				('-' if state.elevationP is None else '%.4f'%state.elevationP),
				state.shots))
		if self.rws_schema:
			self.rws_schema.set_axisunit_state_data(state)
		if self.status_axisunit:
			self.status_axisunit.connected = state.connected 
			self.status_axisunit.act_rotationP = state.rotationP
			self.status_axisunit.act_elevationP = state.elevationP
			if self.shots_offset is None:
				self.shots_offset = state.shots
			self.status_axisunit.ammo_count = self.loaded_ammo - (state.shots - self.shots_offset)
		self.axisunit_azimuth = state.rotationP if state.rotationP is not None else None
		self.axisunit_elevation = state.elevationP if state.elevationP is not None else None
		if self.motion_detector:
			self.motion_detector.track_stage_motion('sight', state)

	def OnRssiConnected(self):
		Logger.info("Control: rssi connected")

	def OnRssiChanged(self):
		rssi_state = self.rssi.rssi_state
		Logger.debug("Control: new rssi %d, %d, %d, %d"%(rssi_state.signal, rssi_state.noise, rssi_state.ccq, rssi_state.distance))
		if self.status_ugv:
			self.status_ugv.set_rssi_status(rssi_state)
		if self.status_axisunit:
			self.status_axisunit.set_rssi_status(rssi_state)

	def OnRssiDisconnected(self):
		Logger.info("Control: rssi disconnected")
	
	def _restart_throttle_updater(self):
		self._stop_throttle_updater()
		self._start_throttle_updater()
	
	def _start_throttle_updater(self):
		if not self._trigger_throttle_updater:
			self._trigger_throttle_updater = Clock.create_trigger(
						self._update_throttle_by_acc if self.throttle_mode == 'acc' else self._update_throttle_by_vel,
						0.05 if self.throttle_mode == 'acc' else 0)
			self._trigger_throttle_updater()
			Logger.debug("Control: ThrottleCtrl started %s"%self.throttle_mode)
	
	def _stop_throttle_updater(self):
		if self._trigger_throttle_updater:
#			Clock.unschedule(self._update_throttle_by_acc)
			self._trigger_throttle_updater.cancel()
			self._trigger_throttle_updater = None
			self.throttle_allowed_direction = 0
			self.throttle_out = 0.0
			Logger.debug("Control: ThrottleCtrl stopped")
	
	def _restart_steering_updater(self):
		self._stop_steering_updater()
		self._start_steering_updater()
	
	def _start_steering_updater(self):
		if not self._trigger_steering_updater:
			self._trigger_steering_updater = Clock.create_trigger(
						self._update_steering_by_acc if self.steering_mode == 'acc' else self._update_steering_by_vel,
						0.05 if self.steering_mode == 'acc' else 0)
			self._trigger_steering_updater()
			Logger.debug("Control: SteeringCtrl started %s"%self.steering_mode)
	
	def _stop_steering_updater(self):
		if self._trigger_steering_updater:
#			Clock.unschedule(self._update_steering_by_acc)
			self._trigger_steering_updater.cancel()
			self._trigger_steering_updater = None
			self.steering_allowed_direction = 0
			self.steering_out = 0.0
			Logger.debug("Control: SteeringCtrl stopped")
	
	def _start_adjustment_updater(self):
		if not self._trigger_adjustment_updater:
			self._trigger_adjustment_updater = Clock.create_trigger(
						self._update_adjustment_by_joystick,
						0.05)
			self._trigger_adjustment_updater()
			Logger.info("Control: AdjustmentCtrl started")
			return True
	
	def _stop_adjustment_updater(self):
		if self._trigger_adjustment_updater:
#			Clock.unschedule(self._update_throttle_by_acc)
			self._trigger_adjustment_updater.cancel()
			self._trigger_adjustment_updater = None
			Logger.info("Control: AdjustmentCtrl stopped")
			return True
	
	def OnConnectionLost(self):
		Logger.error("Connection lost")
		if self.status_ugv:
			self.status_ugv.connected = False
		if self.rover_schema:
			self.rover_schema.opacity = 0.5
		if self.status_axisunit:
			self.status_axisunit.connected = False
		if self.rws_schema:
			self.rws_schema.opacity = 0.5
# 		if has_notification:
# 			notification.notify(title='Unit connection lost',
# 							message='Connection to unit lost. Check WiFi connection.',
# 							app_name=self.__class__.title,
# 							app_icon=self.net_error.source,
# 							timeout=1)
		if has_vibrator:
			vibrator.vibrate(5)
	
	def OnConnectionEstablished(self):
		Logger.info("Control: Connection established")
		if 	self.status_ugv:
			self.status_ugv.connected = True
		if self.rover_schema:
			self.rover_schema.opacity = 1.0
		if 	self.status_axisunit:
			self.status_axisunit.connected = True
		if self.rws_schema:
			self.rws_schema.opacity = 1.0
# 		if has_notification:
# 			notification.notify(title='Unit connection established',
# 							message='Connection to unit established :).',
# 							app_name=self.__class__.title,
# 							app_icon=self.__class__.icon,
# 							timeout=10)
		if has_vibrator:
			vibrator.cancel()
	
	def OnPositionFixed(self):
		Logger.info("Control: GPS Position fixed")

	def OnPositionLost(self):
		Logger.warning("Control: GPS Position lost")

	def OnPositionChanged(self):
		Logger.debug("Control: GPS Position changed")
		gps = self.control_comm.unit_gps
		if gps.fixed:
			if self.map: self.map.rover_coord = (gps.lat, gps.lon)
			if self.gps_logger:
				self.gps_logger.log(gps.lat, gps.lon, gps.alt)

	def OnHeadingChanged(self):
		Logger.debug("Control: Heading changed")
		self.compass_heading = math.radians(self.control_comm.unit_heading.heading)
		
	def OnTemperaturesChanged(self):
		Logger.debug("Control: Temperatures changed")
		if self.rover_schema:
			self.rover_schema.set_temperatures_data(self.control_comm.unit_temperatures)
		
	def OnUgvTelemetryChanged(self):
		Logger.debug("Control: UGV telemetry changed")
		if self.rover_schema:
			self.rover_schema.set_telemetry_data(self.control_comm.ugv_telemetry)
		
	def OnRwsTelemetryChanged(self):
		Logger.debug("Control: RWS telemetry changed")
		# testing
		# t = self.control_comm.rws_telemetry
		# print(f'ControlLayout: OnRWSTelemetryChanged')
		# print(f"alive = {t.alive}, firing = {t.firing}, \
		# 	alive_Y = {t.alive_Y}, ready_Y = {t.ready_Y},  alarm_Y = {t.alarm_Y}, \
		# 	alive_x = {t.alive_X}, ready_x = {t.ready_X},  alarm_Y = {t.alarm_X}")
		if self.rws_schema:
			self.rws_schema.set_telemetry_data(self.control_comm.rws_telemetry)
		if self.status_axisunit:
			self.status_axisunit.battery_voltage = self.control_comm.rws_telemetry.voltage_bat
			self.status_axisunit.battery_percentage = self.control_comm.rws_telemetry.percentage_bat
			t = self.control_comm.rws_telemetry
			self.status_axisunit.warnings = [
                not t.ready_X or t.alarm_X,
                not t.ready_Y or t.alarm_Y]
			self.status_axisunit.faults = [
                not t.alive_X or t.home_fault_X,
                not t.alive_Y or t.home_fault_Y]

	def OnDistanceChanged(self):
		Logger.info("Control: Distance changed")
		if self.control_comm.rws_state.rangefinder_distance:
			self.ballistic_distance = self.rangefinder_distance = self.control_comm.rws_state.rangefinder_distance
			if self.rangefinder_distance:
				self._trigger_rangefinder_measurement.cancel()
		elif self._external_rangefinder_comm.distance:
			self.ballistic_distance = self.rangefinder_distance = self._external_rangefinder_comm.distance
			if self.rangefinder_distance:
				self._trigger_rangefinder_measurement.cancel()
#		self.dispatch('on_add_target_request')

	def OnFollowMeChanged(self):
		Logger.info("Control: FollowMe packet changed")
		if self.followme_box:
			print("OnFollowMeChanged: self.status_ugv.followme_on: ", self.status_ugv.followme_on)
			coord_obj = self.control_comm.unit_follow_me
			if coord_obj.state:
				self.buttons_layout_drive.set(button_followme='run')
			else:
				self.buttons_layout_drive.set(button_followme='ready')
			self.followme_box.set_coordinates(coord_obj, self.status_ugv.followme_on)
		
	def _unassign_serial(self):
		for name, uid in self._serial_bindings:
			self.serialhids.unbind_uid(name, uid)
		self._serial_bindings = []

	def _unassign_joystick(self):
		for name, uid in self._joystick_bindings:
			self.joystickhids.unbind_uid(name, uid)
		self._joystick_bindings = []

	def _unassign_buttons(self):
		for holder, name, uid in self._buttons_bindings:
			holder.unbind_uid(name, uid)
		self._buttons_bindings = []

	def _reconfig_inputs(self, *args):
		self.active = False
		self._unassign_buttons()
		if self.serialhids:
			self._unassign_serial()
			self.serialhids.stop()
		if self.joystickhids:
			self._unassign_joystick()
			self.joystickhids.stop()
		if self.gsensor:
			Logger.debug("Control: GSensor pre reconfig inputs.")
			self.gsensor.stop()
		self._stop_throttle_updater()
		self._stop_steering_updater()
		self._stop_adjustment_updater()

		self.joystickhids_used = False
		self.serialhids_used = False
		self.gsensor_used = False

		hint_msg = []

		throttle_src = None if self.preset not in ('drive', 'dual') else (
				'joystickhids' if self.joystickhids and 'axis_throttle' in self.joystickhids.properties() else (
				'serialhids' if self.serialhids and 'axis_throttle' in self.serialhids.properties() else (
				'gsens' if self.gsensor and self.gsensor.gsensor_sensivity_thr != 0.0 else (
				'slider'
		))))
		steering_src = None if self.preset not in ('drive', 'dual') else (
				'joystickhids' if self.joystickhids and 'axis_steering' in self.joystickhids.properties() else (
				'serialhids' if self.serialhids and 'axis_steering' in self.serialhids.properties() else (
				'gsens' if self.gsensor and self.gsensor.gsensor_sensivity_str != 0.0 else (
				'slider'
		))))
		if throttle_src == 'slider' and steering_src == 'slider' and self.allow_slider2d:
			throttle_src = 'slider2d'
			steering_src = 'slider2d'

		Logger.debug("Control: throttle source {}".format(throttle_src))
		if throttle_src == 'slider2d':
			self.slider_thr_str = self._add_slider_2d(
					'steering', (-1.0, 1.0),
					'throttle', (0.0 if self.throttle_mode == 'vel_uni' else -1.0, 1.0),
					{'center_x':0.5, 'center_y':0.5}, (0.3, 0.3)
				)
		else:
			self.slider_thr_str = self._remove_slider(self.slider_thr_str)
		if throttle_src == 'slider':
			if not self.slider_thr:
				self.slider_thr = self._add_slider('throttle',
						 (0.0 if self.throttle_mode == 'vel_uni' else -1.0, 1.0),
						 'vertical', {'x':0.0, 'center_y':0.5}, (None, 1.0))
		else:
			self.slider_thr = self._remove_slider(self.slider_thr)
		if throttle_src == 'joystickhids':
			self.joystickhids.bind(axis_throttle=self.setter('throttle'))
			self.joystickhids_used = True
		elif self.joystickhids and 'axis_throttle' in self.joystickhids.properties():
			self.joystickhids.unbind(axis_throttle=self.setter('throttle'))
		if throttle_src == 'serialhids':
			self.serialhids.bind(axis_throttle=self.setter('throttle'))
			self.serialhids_used = True
		elif self.serialhids and 'axis_throttle' in self.serialhids.properties():
			self.serialhids.unbind(axis_throttle=self.setter('throttle'))
		if throttle_src == 'gsens':
			self.gsensor_used = True
			self.gsensor.bind(gsensor_throttle=self.setter('throttle'))
		elif self.gsensor:
			self.gsensor.unbind(gsensor_throttle=self.setter('throttle'))

		Logger.debug("Control: steering source {}".format(steering_src))
		if steering_src == 'slider':
			if not self.slider_str:
				self.slider_str = self._add_slider('steering', (-1.0, 1.0), 'horizontal',	{'center_x':0.5, 'y':0.0}, (1.0, None))
		else:
			self.slider_str = self._remove_slider(self.slider_str)
		if steering_src == 'joystickhids':
			self.joystickhids.bind(axis_steering=self.setter('steering'))
			self.joystickhids_used = True
		elif self.joystickhids and 'axis_steering' in self.joystickhids.properties():
			self.joystickhids.unbind(axis_steering=self.setter('steering'))
		if steering_src == 'serialhids':
			self.serialhids.bind(axis_steering=self.setter('steering'))
			self.serialhids_used = True
		elif self.serialhids and 'axis_steering' in self.serialhids.properties():
			self.serialhids.unbind(axis_steering=self.setter('steering'))
		if steering_src == 'gsens':
			self.gsensor.bind(gsensor_steering=self.setter('steering'))
			self.gsensor_used = True
		elif self.gsensor:
			self.gsensor.unbind(gsensor_steering=self.setter('steering'))

		rotation_src = None if self.preset not in ('target', 'dual') else (
				'joystickhids' if self.joystickhids and 'axis_rotation' in self.joystickhids.properties() else (
				'serialhids' if self.serialhids and 'axis_rotation' in self.serialhids.properties() else (
#				'gsens' if self.gsensor and self.preset in ('target') and self.gsensor.gsensor_sensivity_str != 0.0 else (
				'slider'
		)))
		elevation_src = None if self.preset not in ('target', 'dual') else (
				'joystickhids' if self.joystickhids and 'axis_elevation' in self.joystickhids.properties() else (
				'serialhids' if self.serialhids and 'axis_elevation' in self.serialhids.properties() else (
#				'gsens' if self.gsensor and self.preset in ('target') and self.gsensor.gsensor_sensivity_str != 0.0 else (
				'slider'
		)))
		if rotation_src == 'slider' and elevation_src == 'slider' and self.allow_slider2d:
			rotation_src = 'slider2d'
			elevation_src = 'slider2d'

		Logger.debug("Control: rotation source {}".format(rotation_src))
		if rotation_src == 'slider2d':
			self.slider_rot_ele = self._add_slider_2d(
					'rotation', (-1.0, 1.0),
					'elevation', (-1.0, 1.0),
					{'center_x':0.5, 'center_y':0.5}, (0.3, 0.3)
				)
		else:
			self.slider_rot_ele = self._remove_slider(self.slider_rot_ele)
		#TODO
		#self.assign_axis('axis_rotation', rotation_src, 'rotation', self.slider_rot):
		if rotation_src == 'joystickhids':
			self.joystickhids.bind(axis_rotation=self.setter('rotation'))
			self.joystickhids_used = True
		elif self.joystickhids and 'axis_rotation' in self.joystickhids.properties():
			self.joystickhids.unbind(axis_rotation=self.setter('rotation'))
		if rotation_src == 'serialhids':
			self.serialhids.bind(axis_rotation=self.setter('rotation'))
			self.serialhids_used = True
		elif self.serialhids and 'axis_rotation' in self.serialhids.properties():
			self.serialhids.unbind(axis_rotation=self.setter('rotation'))
		if rotation_src == 'gsens':
			self.gsensor.bind(gsensor_steering=self.setter('rotation'))
			self.gsensor_used = True
		elif self.gsensor:
			self.gsensor.unbind(gsensor_steering=self.setter('rotation'))
		if rotation_src == 'slider':
			if not self.slider_rot:
				self.slider_rot = self._add_slider('rotation', (-1.0, 1.0), 'horizontal',	{'center_x':0.5, 'top':1.0}, (1.0, None))
		else:
			self.slider_rot = self._remove_slider(self.slider_rot)

		Logger.debug("Control: elevation source {}".format(elevation_src))
		if elevation_src == 'joystickhids':
			self.joystickhids.bind(axis_elevation=self.setter('elevation'))
			self.joystickhids_used = True
		elif self.joystickhids and 'axis_elevation' in self.joystickhids.properties():
			self.joystickhids.unbind(axis_elevation=self.setter('elevation'))
		if elevation_src == 'serialhids':
			self.serialhids.bind(axis_elevation=self.setter('elevation'))
			self.serialhids_used = True
		elif self.serialhids and 'axis_elevation' in self.serialhids.properties():
			self.serialhids.unbind(axis_elevation=self.setter('elevation'))
		if elevation_src == 'gsens':
			self.gsensor.bind(gsensor_throttle=self.setter('elevation'))
			self.gsensor_used = True
		elif self.gsensor:
			self.gsensor.unbind(gsensor_throttle=self.setter('elevation'))
		if elevation_src == 'slider':
			if not self.slider_ele:
				self.slider_ele = self._add_slider('elevation', (-1.0, 1.0), 'vertical', {'right':1.0, 'center_y':0.5}, (None, 1.0))
		else:
			self.slider_ele = self._remove_slider(self.slider_ele)

		rotation_slow_src = None if self.preset not in ('target', 'dual') else (
				'joystickhids' if self.joystickhids and 'axis_rotation_slow' in self.joystickhids.properties() else (
				'serialhids' if self.serialhids and 'axis_rotation_slow' in self.serialhids.properties() else (
				None
		)))
		elevation_slow_src = None if self.preset not in ('target', 'dual') else (
				'joystickhids' if self.joystickhids and 'axis_elevation_slow' in self.joystickhids.properties() else (
				'serialhids' if self.serialhids and 'axis_elevation_slow' in self.serialhids.properties() else (
				None
		)))
		Logger.debug("Control: rotation_slow source {}".format(rotation_slow_src))
		if rotation_slow_src == 'joystickhids':
			self.joystickhids.bind(axis_rotation_slow=self.setter('rotation_slow'))
			self.joystickhids_used = True
		elif self.joystickhids and 'axis_rotation_slow' in self.joystickhids.properties():
			self.joystickhids.unbind(axis_rotation_slow=self.setter('rotation_slow'))
		if rotation_slow_src == 'serialhids':
			self.serialhids.bind(axis_rotation_slow=self.setter('rotation_slow'))
			self.serialhids_used = True
		elif self.serialhids and 'axis_rotation_slow' in self.serialhids.properties():
			self.serialhids.unbind(axis_rotation_slow=self.setter('rotation_slow'))

		Logger.debug("Control: elevation_slow source {}".format(elevation_slow_src))
		if elevation_slow_src == 'joystickhids':
			self.joystickhids.bind(axis_elevation_slow=self.setter('elevation_slow'))
			self.joystickhids_used = True
		elif self.joystickhids and 'axis_elevation_slow' in self.joystickhids.properties():
			self.joystickhids.unbind(axis_elevation_slow=self.setter('elevation_slow'))
		if elevation_slow_src == 'serialhids':
			self.serialhids.bind(axis_elevation=self.setter('elevation_slow'))
			self.serialhids_used = True
		elif self.serialhids and 'axis_elevation_slow' in self.serialhids.properties():
			self.serialhids.unbind(axis_elevation_slow=self.setter('elevation_slow'))

		deadman_src = (
				'joystickhids' if self.joystickhids and 'button_deadman' in self.joystickhids.properties() else (
				'serialhids' if self.serialhids and 'button_deadman' in self.serialhids.properties() else (
				'slider2d' if throttle_src == 'slider2d' else (
				'slider' if throttle_src == 'slider' or steering_src == 'slider' else (
				'touch' if throttle_src == 'gsens' or steering_src == 'gsens' or (throttle_src and self.throttle_mode == 'acc') else (
				None
		))))))
		Logger.debug('Control: deadman source {}, jprops {}, sprops {}'.format(deadman_src, self.joystickhids and self.joystickhids.properties(), self.serialhids and self.serialhids.properties()))	
		if deadman_src == 'touch':
			self.bind(touched=self.setter('active'))
			hint_msg.append(_("Touch screen and"))
		else:
			self.unbind(touched=self.setter('active'))
		if deadman_src == 'slider2d': 
			self.slider_thr_str.bind(active=self.setter('active'))
			hint_msg.append(_("Touch the slider and"))
		elif self.slider_thr_str: 
			self.slider_thr_str.unbind(active=self.setter('active'))
		if deadman_src == 'slider': 
			if self.slider_thr:
				if self.slider_str:
					self.slider_str.unbind(active=self.setter('active'))
				self.slider_thr.bind(active=self.setter('active'))
				hint_msg.append(_("Touch throttle slider and"))
			else:
				self.slider_str.bind(active=self.setter('active'))
				hint_msg.append(_("Touch steering slider and"))
		else:
			if self.slider_thr:
				self.slider_thr.unbind(active=self.setter('active'))
			if self.slider_str:
				self.slider_str.unbind(active=self.setter('active'))
		if deadman_src == 'joystickhids': 
			self.joystickhids.bind(button_deadman=self.setter('active'))
			self.joystickhids_used = True
		elif self.joystickhids and 'button_deadman' in self.joystickhids.properties():
			self.joystickhids.unbind(button_deadman=self.setter('active'))
		if deadman_src == 'serialhids': 
			self.serialhids.bind(button_deadman=self.setter('active'))
			self.serialhids_used = True
		elif self.serialhids and 'button_deadman' in self.serialhids.properties():
			self.serialhids.unbind(button_deadman=self.setter('active'))
		if not deadman_src:
			self.active = True

		self.known_button_actions = {'button_deadmen':('deadmen',)}

		self.buttons_layout_drive.buttons_list = []
		if self.preset in ('drive', 'dual'):
			btns_overlay = self.buttons_layout_drive
			self.assign_cycled('motion_enable', btns_holder=btns_overlay)
			self.assign_setter('motion_enable', 'motion_enable_set')
			self.assign_cycled('motion_slow', btns_holder=btns_overlay)
			self.assign_setter('motion_slow', 'motion_slow_set')
			self.assign_select2('motion_slow', 0, 1)
			self.assign_cycled('reverse', btns_holder=btns_overlay)
			self.assign_setter('reverse', 'reverse_set')
			drive_cam_ids = []
			drive_has_extra_cams = False
			self.assign_cycled('drive_cams_quality', btns_holder=btns_overlay)
			for o in ControlLayout.drive_cams_quality.options:
				self.assign_select('drive_cams_quality', o, 'drive_cams_%s'%o)
			self.assign_cycled('steer_mode', btns_holder=(btns_overlay,'more'))
			self.assign_select('steer_mode', 'pull')
			self.assign_select('steer_mode', 'diff')
			self.assign_select('steer_mode', 'normal')
			self.assign_cycled('followme', btns_holder=(btns_overlay,'more'))
			self.assign_select('followme', 'on')
			self.assign_select('followme', 'ready')
			self.assign_select('followme', 'run')
			self.assign_cycled('engine_on', 'engine', btns_holder=(btns_overlay,'more'))
			self.assign_setter('engine_on', 'engine_set')
			self.assign_setter('engine_start', 'start', btns_holder=(btns_overlay,'more'))
			self.assign_setter('charging_enable', 'charge')
			self.assign_cycled('lights_on', 'allow_lights', btns_holder=(btns_overlay,'more/lights'))
			self.assign_setter('lights_on', 'allow_lights_set')
			self.assign_cycled('headlight', btns_holder=(btns_overlay,'more/lights'))
			self.assign_setter('headlight', 'headlight_set')
			self.assign_cycled('marker_light', btns_holder=(btns_overlay,'more/lights'))
			self.assign_setter('marker_light', 'marker_light_set')
			self.assign_setter('horn_on', 'horn')#, btns_holder=(btns_overlay,'more'))
			self.assign_setter('steerassist_on')#, 'steerassist', btns_holder=(btns_overlay,'more'))
			self.assign_setter('winch_out')#, btns_holder=(btns_overlay,'more/winch'))
			self.assign_setter('winch_in')#, btns_holder=(btns_overlay,'more/winch'))
			for grp in ControlLayout.DRIVE_CAM_GROUPS:
				cam_ids = [cam_id for cam_id in self.cameras_config.keys()
							if check_cam_in_group(cam_id, grp)]
				for cam_id in cam_ids:
					self.assign_select('drive_cam_selected', cam_id, 'drive_cam_%s'%cam_id)
					if cam_id not in drive_cam_ids: 
						drive_cam_ids.append(cam_id)
					if cam_id not in ('front', 'rear'):
						drive_has_extra_cams = True
				self.assign_cycled('drive_cam_selected', 'drive_cams_%s_loop'%grp, values=cam_ids)
			self.assign_cycled('drive_cam_selected', 'drive_cams_loop', values=drive_cam_ids,
							btns_holder=(btns_overlay,'more/videocam') if drive_has_extra_cams else None)
			self.assign_cycled('drive_cams_pip', btns_holder=(btns_overlay,'more/videocam'))
			self.assign_cycled('drive_cams_night', btns_holder=(btns_overlay, 'more/videocam/tune_video'))
			self.assign_setter('drive_cams_night', 'drive_cams_night_set')
			self.assign_event('on_drive_brightness_less', btns_holder=(btns_overlay, 'more/videocam/tune_video'))
			self.assign_event('on_drive_brightness_more', btns_holder=(btns_overlay, 'more/videocam/tune_video'))
			self.assign_event('on_drive_contrast_less', btns_holder=(btns_overlay, 'more/videocam/tune_video'))
			self.assign_event('on_drive_contrast_more', btns_holder=(btns_overlay, 'more/videocam/tune_video'))
			self.assign_event('on_drive_cams_reset', btns_holder=(btns_overlay, 'more/videocam/tune_video'))
		if self.preset in ('target', 'dual'):
			# buttons_list_target = ['axisunit_enable', 'axisunit_slow', 'target_menu', 'safety', 'fire']
			if self.preset == 'dual' and self.displays > 1:
				self.buttons_layout_target.buttons_list = []
				btns_overlay = self.buttons_layout_target
			else:
				btns_overlay = self.buttons_layout_drive
			self.assign_cycled('axisunit_enable', btns_holder=btns_overlay)
			self.assign_setter('axisunit_enable', 'axisunit_enable_set')
			self.assign_cycled('axisunit_slow', btns_holder=btns_overlay)
			self.assign_setter('axisunit_slow', 'axisunit_slow_set')
			sight_cam_ids = []
			sight_has_extra_cams = False
			self.assign_cycled('sight_cam_selected', 'sight_cams_thermo', values=['thermo', 'wide'],
							btns_holder=btns_overlay if 'thermo' in self.cameras_config else None)
			self.assign_cycled('fire_burst', values=(1,5,0), btns_holder=btns_overlay)
			self.assign_setter('armed_req', 'safety', btns_holder=btns_overlay, indicator_property='armed')
			self.assign_setter('fire_req', 'fire', btns_holder=btns_overlay, indicator_property='fire')

			if self.video_recorder:
				self.assign_cycled('video_recorder_req', 'video_recorder_indicator',
								   btns_holder=btns_overlay,
											indicator_property='video_recorder_indicator')
			self.assign_setter('axisunit_center')
			self.assign_setter('rangefinder_on', 'rangefinder')
			self.assign_event('on_sight_zoom_in', btns_holder=(btns_overlay,'more'))
			self.assign_event('on_sight_zoom_out', btns_holder=(btns_overlay,'more'))
			self.assign_event('on_sight_zoom_loop')
			self.assign_event('on_sight_zoom_reset')
			self.assign_event('on_distance_request', 'distance', btns_holder=(btns_overlay,'more'))
			self.assign_event('on_prev_target_request', 'prev_target', btns_holder=(btns_overlay,'more/targets'))
			self.assign_event('on_next_target_request', 'next_target', btns_holder=(btns_overlay,'more/targets'))
			self.assign_event('on_add_target_request', 'add_target', btns_holder=(btns_overlay,'more/targets'))
			self.assign_event('on_move_target_request', 'move_target', btns_holder=(btns_overlay,'more/targets'))
			self.assign_event('on_del_target_request', 'del_target', btns_holder=(btns_overlay,'more/targets'))
			self.assign_event('on_clear_targets_request', 'clear_targets', btns_holder=(btns_overlay,'more/targets'))
			self.assign_event('on_sight_mark_loop', 'reticle_mark', btns_holder=(btns_overlay,'more/reticle'))
			self.assign_event('on_sight_grid_loop', 'reticle_grid', btns_holder=(btns_overlay,'more/reticle'))
#			self.assign_event('on_sight_rangefinder_loop', 'reticle_rangefinder', btns_holder=(btns_overlay,'more/reticle'))
			self.assign_event('on_sight_legend_loop', 'reticle_text', btns_holder=(btns_overlay,'more/reticle'))
			self.assign_select('fire_burst', 1)
			self.assign_select('fire_burst', 5)
			self.assign_select('fire_burst', 0)
			self.assign_select2('fire_burst', 1, 5)
			self.assign_select2('fire_burst', 5, 0)
			self.assign_event('on_ammo_reload_request', 'ammo_reload', btns_holder=(btns_overlay,'more/ammo'))
			for grp in ControlLayout.SIGHT_CAM_GROUPS:
				cam_ids = [cam_id for cam_id in self.cameras_config.keys()
							if check_cam_in_group(cam_id, grp)]
				for cam_id in cam_ids:
					self.assign_select('sight_cam_selected', cam_id, 'sight_cam_%s'%cam_id)
					if cam_id not in sight_cam_ids:
						sight_cam_ids.append(cam_id)
					if cam_id not in ('wide', 'tele'):
						sight_has_extra_cams = True
				self.assign_cycled('sight_cam_selected',   'sight_cams_%s_loop'%grp, values=cam_ids)
			self.assign_cycled('sight_cam_selected', 'sight_cams_loop', values=sight_cam_ids,
							btns_holder=(btns_overlay,'more/videocam') if sight_has_extra_cams else None)
			self.assign_cycled('sight_cams_quality', btns_holder=(btns_overlay,'more/videocam'))
			for o in ControlLayout.sight_cams_quality.options:
				self.assign_select('sight_cams_quality', o, 'sight_cams_%s'%o)
			self.assign_cycled('sight_cams_pip', btns_holder=(btns_overlay,'more/videocam'))
			self.assign_setter('sight_cams_pip', 'sight_cams_pip_set')

			# if self.motion_detector:
			# 	self.assign_cycled('sight_cams_motion_sensor', btns_holder=(btns_overlay,'more/videocam'))
			# 	self.assign_setter('sight_cams_motion_sensor', 'sight_cams_motion_sensor_set')

			if self.video_recorder:
				self.assign_event('on_video_recorder_settings', btns_holder=(btns_overlay,'more/videocam'))
			self.assign_event('on_target_menu_request', 'target_menu', btns_holder=(btns_overlay, 'more'))

			if HardwareVolume:
				self.assign_cycled('master_volume_level', btns_holder=(btns_overlay, 'more/videocam/tune_video'))
				# self.assign_event('on_master_volume_level_less', btns_holder=(btns_overlay, 'more/videocam/tune_video'))
				# self.assign_event('on_master_volume_level_more', btns_holder=(btns_overlay, 'more/videocam/tune_video'))

			self.assign_cycled('sight_cams_night_req', btns_holder=(btns_overlay, 'more/videocam/tune_video'))
			self.assign_event('on_sight_brightness_less', btns_holder=(btns_overlay, 'more/videocam/tune_video'))
			self.assign_event('on_sight_brightness_more', btns_holder=(btns_overlay, 'more/videocam/tune_video'))
			self.assign_event('on_sight_contrast_less', btns_holder=(btns_overlay, 'more/videocam/tune_video'))
			self.assign_event('on_sight_contrast_more', btns_holder=(btns_overlay, 'more/videocam/tune_video'))
			self.assign_event('on_sight_cams_reset', btns_holder=(btns_overlay, 'more/videocam/tune_video'))

		self.assign_cycled('cams_quality')
		self.assign_cycled('cams_night')
		self.assign_setter('cams_pip')
		self.assign_event('on_map_zoom_in')
		self.assign_event('on_map_zoom_out')
		self.assign_event('on_map_zoom_loop')
		self.assign_cycled('hud_color_schema', 'grid', btns_holder=(btns_overlay,'more/reticle'))
		self.assign_event('on_menu_ok')
		self.assign_event('on_menu_cancel')
		self.assign_event('on_menu_next')
		self.assign_event('on_menu_prev')
		self.assign_event('on_back', btns_holder=None if not all((
					self.config.getboolean('rover', 'ugv'),
					self.config.getboolean('rover', 'rws'),
					)) else (btns_overlay, 'more'))

		if self.joystickhids:
			for p in self.joystickhids.properties():
				if p.startswith('button_') and p[7:] not in self.known_button_actions:
					suggested = [k for k in self.known_button_actions.keys() if all([s in k for s in p.split('_')[1:]])]
					Logger.error('Control: Unknown Joystick button %s, suggested %s', p, ','.join(suggested))
		if self.serialhids:
			for p in self.serialhids.properties():
				if p.startswith('button_') and p[7:] not in self.known_button_actions:
					suggested = [k for k in self.known_button_actions.keys() if all([s in k for s in p.split('_')[1:]])]
					Logger.error('Control: Unknown Serial button %s, suggested %s', p, ','.join(suggested))

		if 'joystickhids' in (steering_src, steering_src, rotation_src, elevation_src):
			hint_msg.append(_("use joystick to control"))
		if 'serialhids' in (steering_src, steering_src, rotation_src, elevation_src):
			hint_msg.append(_("use console to control"))
		if 'gsens' in (throttle_src, steering_src):
			hint_msg.append(_("tilt device to control"))
		if 'slider' in (throttle_src, steering_src, elevation_src, rotation_src):
			hint_msg.append(_("drag slider(s) to control"))
		if hint_msg:
			self._show_hint_label("\n".join(hint_msg), sound='ready')

		if self.joystickhids_used:
			Logger.debug("Control: joysticks starting")
			if self.joystickhids.start():
				Logger.debug("Control: active binded to joystick")
			else:
				Logger.warning("Control: unable to start joystics")
				self.joystickhids = None
				self._trigger_reconfig_inputs()
		elif self.joystickhids:
			Logger.debug("Control: active unbinded from joystick")
			self.unbind(active=self.joystickhids.setter('active'))

		if self.serialhids_used:
			Logger.debug("Control: serialHids starting")
			if self.serialhids.start():
				Logger.debug("Control: active binded to serialHids")
			else:
				Logger.debug("Control: unable to start serialHids")
				self.serialhids = None
				self._trigger_reconfig_inputs()
		elif self.serialhids:
			Logger.debug("Control: active unbinded from serialHids")
			self.unbind(active=self.serialhids.setter('active'))

		if self.gsensor_used:
			Logger.debug("Control: GSensor starting")
			if self.gsensor.start():
				Logger.debug("Control: active binded to GSensor")
				self.bind(active=self.gsensor.setter('active'))
				self.gsensor.active = self.active
			else:
				Logger.debug("Control: GSensor unable to start")
				self.gsensor = None
				self._trigger_reconfig_inputs()
		elif self.gsensor:
			Logger.debug("Control: active unbinded from GSensor")
			self.unbind(active=self.gsensor.setter('active'))

		self._update_sliders_layout()

	#TODO
	def assign_axis(self, action_name, src, prop, slider):
		if src == 'joystickhids':
			uid = self.joystickhids.fbind(action_name, self.setter(prop))
			self._joystick_bindings.append((action_name, uid))
			self.joystickhids_used = True
		if src == 'serialhids':
			uid = self.serialhids.fbind(action_name, self.setter(prop))
			self._serial_bindings.append((action_name, uid))
			self.serialhids_used = True
		if src == 'gsens':
			uid = self.gsensor.fbind(gsensor_steering, self.setter(prop))
			self.gsensor_used = True
		if src == 'slider':
			if not slider:
				slider = self._add_slider(prop, (-1.0, 1.0), 'horizontal',	{'center_x':0.5, 'top':1.0}, (1.0, None))
		else:
			self._remove_slider(self.slider_rot)
			self.slider_rot = None

	def assign_indicator(self, ind, action_name, prop):
		setter = ind.setter('indicator_' + action_name)
		setter(ind, prop.get(self))
		prop.bind(self, setter)

	def assign_button(self, action_name, action_fn, btns_holder, act_type='button'):
		indicators = []
		button_name = act_type + '_' + action_name
		if isinstance(btns_holder, (tuple, list)):
			btns_holder, btn_path = btns_holder
			btn_name = btn_path + '/' + action_name
		else:
			btn_name = action_name
		if self.joystickhids and (button_name in self.joystickhids.properties() or button_name in self.joystickhids.events()):
			Logger.info('Control: %s action %s assigned to Joystick %s',
						action_name, act_type, button_name)
			uid = self.joystickhids.fbind(button_name, action_fn)
			self._joystick_bindings.append((button_name, uid))
			self.joystickhids_used = True
			indicators.append(self.joystickhids)
		if self.serialhids and (button_name in self.serialhids.properties() or button_name in self.serialhids.events()):
			Logger.info('Control: %s action %s assigned to SerialHid %s',
						action_name, act_type, button_name)
			uid = self.serialhids.fbind(button_name, action_fn)
			self._serial_bindings.append((button_name, uid))
			self.serialhids_used = True
			indicators.append(self.serialhids)
		if btns_holder:
			Logger.info('Control: %s action %s assigned to BtnOver %s_%s %s',
						action_name, act_type, act_type, action_name, btn_name)
			btns_holder.add_button(btn_name)
			btn_prop_name = act_type + '_' + action_name
			uid = btns_holder.fbind(btn_prop_name, action_fn)
			self._buttons_bindings.append((btns_holder, btn_prop_name, uid))
			indicators.append(btns_holder)
		return indicators
		
	def assign_event(self, event_name, action_name = None, btns_holder=None):
		"""the event triggers each time the button is pressed"""
		if action_name is None:
			action_name = event_name[3:] #remove "on_"
		def event_fn(i):
			# Logger.debug('Control: Event %s dispatched', event_name)
			self.dispatch(event_name)
		self.known_button_actions[action_name] = ('event', event_name, True)
		self.assign_button(action_name, event_fn,
						btns_holder=btns_holder, 
						act_type='on_press')

	def assign_setter(self, property_name, action_name = None, btns_holder=None, indicator_property=None):
		"""the property value is set to True while the button is pressed"""
		p = self.property(property_name)
		if action_name is None:
			action_name = property_name
		def action_fn(i, v):
			# Logger.debug('Control: Property %s set %s', property_name, v)
			p.set(self, v)
		self.known_button_actions[action_name] = ('property', property_name, True)
		indicators = self.assign_button(action_name, action_fn, btns_holder)

		pi = self.property(indicator_property) if indicator_property else p
		for ind in indicators:
			self.assign_indicator(ind, action_name, pi)

	def assign_cycled(self, property_name, action_name=None, values=None, btns_holder=None, indicator_property=None):
		"""the property value cycles through the possible values each time the button is pressed"""
		p = self.property(property_name)
		if not action_name:
			action_name = property_name
		if not values:
			if isinstance(p, BooleanProperty): values = [True, False]
			elif isinstance(p, OptionProperty): values = p.options
			else:
				Logger.error('Control: Property values required for cycling %s', property_name)
				return
		def cycle_fn(i):
			try:
				value = values[(values.index(p.get(self)) + 1) % len(values)]
			except ValueError:
				value = values[0]
			p.set(self, value)
			Logger.info('Control: Property %s cycled to %s', property_name, p.get(self))
		self.known_button_actions[action_name] = ('cycle', property_name, values)
		indicators = self.assign_button(action_name, cycle_fn, btns_holder,
									act_type='on_release')

		pi = self.property(indicator_property) if indicator_property else p
		for ind in indicators:
			self.assign_indicator(ind, action_name, pi)

	def assign_select(self, property_name, value, action_name=None, btns_holder=None, indicator_property=None):
		"""the property value is set to specified value when the button is pressed"""
		p = self.property(property_name)
		if action_name is None:
			action_name = property_name + '_' + str(value)
		def select_fn(i):
			p.set(self, value)
			Logger.info('Control: Property %s select %s by %s', property_name, value, action_name)
		self.known_button_actions[action_name] = ('select', property_name, value)
		indicators = self.assign_button(action_name, select_fn, btns_holder,
									act_type='on_press')

		pi = self.property(indicator_property) if indicator_property else p
		for ind in indicators:
			self.assign_indicator(ind, action_name, pi)

	def assign_select2(self, property_name, value_off, value_on, action_name=None, btns_holder=None, indicator_property=None):
		"""the property value is set to one value when the button is released and to other when pressed"""
		p = self.property(property_name)
		if action_name is None:
			action_name = property_name + '_' + str(value_off) + '_' + str(value_on) 
		def select_fn(i, v):
			value = value_on if v else value_off
			p.set(self, value)
			Logger.info('Control: Property %s select %s by %s', property_name, value, action_name)
		self.known_button_actions[action_name] = ('select', property_name, value_off, value_on)
		indicators = self.assign_button(action_name, select_fn, btns_holder)

		pi = self.property(indicator_property) if indicator_property else p
		for ind in indicators:
			self.assign_indicator(ind, action_name, pi)

	def _end_fire_burst(self, instance):
		Logger.info("Control: Fire burst ended")
		self.fire = False

	def on_touch_down(self, touch):
		if super(ControlLayout, self).on_touch_down(touch):
			return True
		if self.collide_point(*touch.pos):
			touch.grab(self)
			self.touched = True
			return True
	def on_touch_up(self, touch):
		if super(ControlLayout, self).on_touch_up(touch):
			return True
		if touch.grab_current is self:
			touch.ungrab(self)
			self.touched = False
			return True
	def on_back(self):
		self.parent.dispatch('on_back')

	def target_menu_on_close(self, *args):
		self.assign_event('on_menu_ok')
		self.assign_event('on_menu_cancel')
		self.assign_event('on_menu_next')
		self.assign_event('on_menu_prev')


if __name__ == '__main__':
	from multilingual import MultilingualApp as App
	from VideoMontage import CameraConfig
	from Config import AntibugAppConfig
	from kivy.core.window import Window
	from kivy.modules import inspector

	class ControlLayoutTestKivyApp(App):
		title = 'ControlLayout test'

		def __init__(self, *args, **kwargs):
			super(ControlLayoutTestKivyApp, self).__init__(**kwargs)

		@classmethod
		def get_version_string(cls):
			from main import AntibugUnitControlKivyApp 
			return AntibugUnitControlKivyApp.get_version_string()
		def get_application_config(self):
			return super(ControlLayoutTestKivyApp, self).get_application_config('%(appdir)s/antibugunitcontrolkivy.ini')
		def build_config(self, config):
			AntibugAppConfig.build_config(self, config)

		def build(self):
			control = ControlLayout(
				# preset = 'drive',
				# preset = 'dual',
				preset = 'target',
				displays = 1,
				axisunit_azimuth = 0,
				axisunit_elevation = 0,
				cameras_config = {
#					'front':    CameraConfig(id='front', url='test/img2.FHD.jpg', name='^ FRONT ^',  pos=(0,1.5,0.9), fov=2),
#					'front_l':  CameraConfig(id='front_l', url='test/img2.FHD.jpg', name='^^ FRONT LEFT',  pos=(-1,1.5,0.9), fov=1.3),
#					'front_r':  CameraConfig(id='front_r', url='test/img2.FHD.jpg', name='FRONT RIGHT ^^',  pos=(1,1.5,0.9), fov=1.3),
					'rear':   CameraConfig(id='rear',  url='test/img3.FHD.jpg', name='v REAR v',   pos=(0,-1.5,0.9), fov=1),
					'left':   CameraConfig(id='left',  url='test/img4.FHD.jpg', name='< LEFT <',   pos=(0,0,2), fov=1),
					'right':  CameraConfig(id='right', url='test/img5.FHD.jpg', name='> RIGHT >',  pos=(0,0,2), fov=1),
					'wide':   CameraConfig(id='wide',  url='test/img1.FHD.jpg', name='<< WIDE >>', pos=(0.35,0.20,2), rot=(0.000, 0.001, 0), fov=0.8, zooms=[1,2,5]),
					'tele':   CameraConfig(id='tele',  url='test/img1z.HD.jpg', name='>>TELE<<',   pos=(0.45,0.15,2), rot=(0.001, 0.002, 0), fov=0.08, zoom=2.0),
					'thermo':   CameraConfig(id='thermo',  url='test/img.thermo.jpg', name='>>THERMO<<',   pos=(0.45,0.15,2), rot=(0.001, 0.002, 0), fov=0.08),
					'front':   CameraConfig(id='front',
										url='rtsp://192.168.88.95/av0_0',
										opt={'gst_pipeline':'rtspsrc latency=0 protocol=udp drop-on-latency=true location={uri} ! decodebin ! videoconvert ! queue2 max-size-buffers=2'},
										onvif_address='admin:admin@192.168.88.95:2000',
										name='^ FRONT ^',
										pos=(0.35,0.20,2),
										rot=(0.000, 0.001, 0),
										fov=0.8),
					'front_left':   CameraConfig(id='front_left',
										url='rtsp://192.168.88.95/av0_1',
										opt={'gst_pipeline':'rtspsrc latency=0 protocol=udp drop-on-latency=true location={uri} ! decodebin ! videoconvert ! queue2 max-size-buffers=2'},
										onvif_address='admin:admin@192.168.88.95:2000',
										name='^^ FRONT L',
										pos=(0.35,0.20,2),
										rot=(0.000, 0.001, 0),
										fov=0.8),
					'front_right':   CameraConfig(id='front_right',
										url='rtsp://192.168.88.95/av0_2',
										url_hd='rtsp://192.168.88.95/av0_2',
										url_sd='rtsp://192.168.88.95/av0_2',
										url_ld='rtsp://192.168.88.95/av0_2',
										opt={'gst_pipeline':'rtspsrc latency=0 protocol=udp drop-on-latency=true location={uri} ! decodebin ! videoconvert ! queue2 max-size-buffers=2'},
										opt_hd={'gst_pipeline':'rtspsrc latency=0 protocol=udp drop-on-latency=true location={uri} ! decodebin ! videoconvert ! queue2 max-size-buffers=2'},
										opt_sd={'gst_pipeline':'rtspsrc latency=0 protocol=udp drop-on-latency=true location={uri} ! decodebin ! videoconvert ! videoscale ! video/x-raw,width=640,height=360 ! queue2 max-size-buffers=2'},
										opt_ld={'gst_pipeline':'rtspsrc latency=0 protocol=udp drop-on-latency=true location={uri} ! decodebin ! videoconvert ! videoscale ! video/x-raw,width=320,height=180 ! queue2 max-size-buffers=2'},
										onvif_address='admin:admin@192.168.88.95:2000',
										name='FRONT R ^^',
										pos=(0.35,0.20,2),
										rot=(0.000, 0.001, 0),
										fov=0.8),
				},
				allow_slider2d = False,
				config = AntibugAppConfig(self.config),
			)

			inspector.create_inspector(Window, control)
			return control

	def test():
		a = ControlLayoutTestKivyApp()
		a.run()

	test()
