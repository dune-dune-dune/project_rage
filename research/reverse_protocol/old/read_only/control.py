#!/usr/bin/python
from __future__ import print_function
from time import time
import struct
import codecs
import copy
import random
import math
from abc import ABC, abstractmethod
from twisted.internet.protocol import DatagramProtocol, ServerFactory
from twisted.internet.task import LoopingCall
from twisted.internet import reactor

if __name__ == '__main__' and __package__ is None:
	import sys
	from os import path
	sys.path.append(path.dirname(path.dirname(path.abspath(__file__))))
	from udpcomm import UdpProtocolTwisted, CommunicationAnimator
	from cl_util import CommandLineKeyReader, StatusLinePrinter
else:
	from .udpcomm import UdpProtocolTwisted, CommunicationAnimator

UDP_HOST = "192.168.88.56"
UDP_PORT = 7780
LISTEN_PORT = 7770
PERIOD_MS = 50
TIMEOUT_MS = 100000

def ftos(x):
	return 0 if x is None else max(-0x8000, min(0x7FFF, int(x * 0x7FFF)))
def ftol(x):
	return 0 if x is None else max(-0x80000000, min(0x7FFFFFFF, int(x * 0x7FFFFFFF)))
def rtol(x):
	return 0 if x is None else max(-0x80000000, min(0x7FFFFFFF, int(x/math.pi * 0x7FFFFFFF)))
def stof(x, valid=True):
	return None if not valid else float(x) / 0x7FFF
def ustof(x, valid=True):
	return None if not valid else float(x) / 0xFFFF
def ltof(x, valid=True):
	return None if not valid else float(x) / 0x7FFFFFFF
def ltor(x, valid=True):
	return None if not valid else float(x*math.pi) / 0x7FFFFFFF
def ultof(x, valid=True):
	return None if not valid else float(x) / 0xFFFFFFFF
def unpack_bits(intval, bits=8):
	return (bool(intval & (1 << nbit)) for nbit in range(bits))

# assert tuple(unpack_bits(0x0, 1)) == (False,)
# assert tuple(unpack_bits(0x1, 1)) == (True,)
# assert tuple(unpack_bits(0x0, 4)) == (False,False,False,False,)
# assert tuple(unpack_bits(0x1, 4)) == (True,False,False,False,)
# assert tuple(unpack_bits(0x8, 4)) == (False,False,False,True,)
# assert tuple(unpack_bits(0xF, 4)) == (True,True,True,True,)
# assert tuple(unpack_bits(0x0F)) == (True,True,True,True,False,False,False,False,)
# assert tuple(unpack_bits(0xFF)) == (True,True,True,True,True,True,True,True,)
#x = tuple(bit for bits in (unpack_bits(b) for b in (0x01, 0x80)) for bit in bits)
#assert x == (True,False,False,False,False,False,False,False,False,False,False,False,False,False,False,True)

"""
	class UgvControlData:
	
	representation of control values for UGV (Unmanned Ground Vehicle, rover)
"""
class UgvControlData(object):
	def __init__(self):
		super(UgvControlData,self).__init__()
		self.throttle = 0.0
		self.steering = 0.0
		self.motion_enable = False
		self.motion_slow   = False
		self.engine_on     = False
		self.engine_start  = False
		self.lights_on     = False
		self.headlight    = False
		self.marker_light  = False
		self.charging_enable = False
		self.pull_on       = False
		self.horn_on       = False
		self.diffsteer_on  = False
		self.steerassist_on = False
		self.winch_out     = False
		self.winch_in      = False
		self.followme_on = False

"""
	class RwsControlData:
	
	representation of control values for RWS (Remote Weapon Station, axisunit)
"""																							
class RwsControlData(object):
	def __init__(self):
		super(RwsControlData,self).__init__()
		self.rotationV = 0.0
		self.elevationV = 0.0
		self.rotationP = None
		self.elevationP = None
		self.zoom = 0.0
		self.axisunit_enable = False
		self.axisunit_slow   = False
		self.axisunit_vel_prio = False # control by velocity has priority
		self.axisunit_reload   = False
		self.axisunit_force_home = False
		self.safetyARM = None   # 'ARM' or any
		self.JUMP = None   # 'JUMP' or any
		self.jump_duration = 0
		self.jump_seq = 0
		self.camerasP = 0
		self.rangefinder_seq = 0

"""
	class UgvStateData:
	
	representation of unit current state values of UGV
"""
class UgvStateData(object):
	def __init__(self):
		super(UgvStateData,self).__init__()
		self.connected = False
		self.last_error = None
		self.throttleV = 0.0
		self.steeringP = 0.0
		self.batteryPercentage = 0.0
		self.batteryU = 0.0
		self.batteryI = 0.0
		self.generatorU = 0.0
		self.generatorI = 0.0
		self.cell_lowestU = 0.0
		self.cell_highestU = 0.0
		self.charging = False
		self.drive_faults = tuple([False for n in range(0,5)])
		self.followme_on = False
		self.zoom = 0.0
		self.flag1 = 0
		self.flag2 = 0

"""
	class RwsStateData:
	
	representation of unit current state values of RWS
"""
class RwsStateData(object):
	def __init__(self):
		super(RwsStateData,self).__init__()
		self.connected = False
		self.rotationV = 0.0
		self.elevationV = 0.0
		self.rotationP = 0.0
		self.elevationP = 0.0
		self.camerasP = 0
		self.rangefinder_seq = 0
		self.rangefinder_distance = None
		self.jump_seq = 0
		self.shots = 0

"""
	class GPSData:
	
	representation of unit current GPS position
"""
class GPSData(object):
	def __init__(self):
		self.fixed = False
		self.lat = 0.0
		self.lon = 0.0
		self.alt = 0.0

"""
	class CompassData:
	
	representation of unit current compass heading
"""
class CompassData(object):
	def __init__(self):
		self.level = 0.0
		self.heading = 0.0
		self.elevation = 0.0

"""
	class TemperaturesData:
	
	representation of unit current unit temperatures
"""
class TemperaturesData(object):
	def __init__(self):
		self.temperature_battery1     = None
		self.temperature_battery2     = None
		self.temperature_battery3     = None
		self.temperature_battery4     = None
		self.temperature_battery5     = None
		self.temperature_engine       = None
		self.temperature_generator    = None
		self.temperature_charger      = None
		self.temperature_inverter_FL  = None
		self.temperature_inverter_FR  = None
		self.temperature_inverter_RL  = None
		self.temperature_inverter_RR  = None
		self.temperature_inverter_S   = None
		self.temperature_motor_FL     = None
		self.temperature_motor_FR     = None
		self.temperature_motor_RL     = None
		self.temperature_motor_RR     = None
		self.temperature_motor_S      = None
		self.temperature_motor_ext_FL = None
		self.temperature_motor_ext_FR = None
		self.temperature_motor_ext_RL = None
		self.temperature_motor_ext_RR = None
		self.temperature_motor_ext_S  = None
		self.temperature_psu          = None
		self.temperature_body_F       = None
		self.temperature_body_R       = None

"""
	class UgvTelemetryData:
	
	representation of UGV current unit telemetry data
"""
class UgvTelemetryData(object):
	def __init__(self):
		self.temp_warn_battery1      = False
		self.temp_warn_battery2      = False
		self.temp_warn_battery3      = False
		self.temp_warn_battery4      = False
		self.temp_warn_battery5      = False
		self.temp_warn_engine        = False
		self.temp_warn_generator     = False
		self.temp_warn_charger       = False
		self.temp_warn_inverter_FL   = False
		self.temp_warn_inverter_FR   = False
		self.temp_warn_inverter_RL   = False
		self.temp_warn_inverter_RR   = False
		self.temp_warn_inverter_S    = False
		self.temp_warn_motor_FL      = False
		self.temp_warn_motor_FR      = False
		self.temp_warn_motor_RL      = False
		self.temp_warn_motor_RR      = False
		self.temp_warn_motor_S       = False
		self.temp_warn_motor_ext_FL  = False
		self.temp_warn_motor_ext_FR  = False
		self.temp_warn_motor_ext_RL  = False
		self.temp_warn_motor_ext_RR  = False
		self.temp_warn_motor_ext_S   = False
		self.temp_warn_psu           = False
		self.temp_warn_body_F        = False
		self.temp_warn_body_R        = False
		self.temp_err_battery1       = False
		self.temp_err_battery2       = False
		self.temp_err_battery3       = False
		self.temp_err_battery4       = False
		self.temp_err_battery5       = False
		self.temp_err_engine         = False
		self.temp_err_generator      = False
		self.temp_err_charger        = False
		self.temp_err_inverter_FL    = False
		self.temp_err_inverter_FR    = False
		self.temp_err_inverter_RL    = False
		self.temp_err_inverter_RR    = False
		self.temp_err_inverter_S     = False
		self.temp_err_motor_FL       = False
		self.temp_err_motor_FR       = False
		self.temp_err_motor_RL       = False
		self.temp_err_motor_RR       = False
		self.temp_err_motor_S        = False
		self.temp_err_motor_ext_FL   = False
		self.temp_err_motor_ext_FR   = False
		self.temp_err_motor_ext_RL   = False
		self.temp_err_motor_ext_RR   = False
		self.temp_err_motor_ext_S    = False
		self.temp_err_psu            = False
		self.temp_err_body_F         = False
		self.temp_err_body_R         = False
		self.overload_inverter_FL    = False
		self.overload_inverter_FR    = False
		self.overload_inverter_RL    = False
		self.overload_inverter_RR    = False
		self.overload_inverter_S     = False
		self.protection_inverter_FL  = False
		self.protection_inverter_FR  = False
		self.protection_inverter_RL  = False
		self.protection_inverter_RR  = False
		self.protection_inverter_S   = False
		self.disconnected_inverter_FL = False
		self.disconnected_inverter_FR = False
		self.disconnected_inverter_RL = False
		self.disconnected_inverter_RR = False
		self.disconnected_inverter_S  = False
		self.fault_command_FL        = False
		self.fault_command_FR        = False
		self.fault_command_RL        = False
		self.fault_command_RR        = False
		self.fault_command_S         = False
		self.fault_voltage_FL        = False
		self.fault_voltage_FR        = False
		self.fault_voltage_RL        = False
		self.fault_voltage_RR        = False
		self.fault_voltage_S         = False
		self.fault_encoder_FL        = False
		self.fault_encoder_FR        = False
		self.fault_encoder_RL        = False
		self.fault_encoder_RR        = False
		self.fault_encoder_S         = False
		self.fault_drive_FL          = False
		self.fault_drive_FR          = False
		self.fault_drive_RL          = False
		self.fault_drive_RR          = False
		self.fault_drive_S           = False
		self.fault_overload_FL       = False
		self.fault_overload_FR       = False
		self.fault_overload_RL       = False
		self.fault_overload_RR       = False
		self.fault_overload_S        = False
		self.fault_overheat_FL       = False
		self.fault_overheat_FR       = False
		self.fault_overheat_RL       = False
		self.fault_overheat_RR       = False
		self.fault_overheat_S        = False
		self.amperage_inverter_FL = 0.0
		self.amperage_inverter_FR = 0.0
		self.amperage_inverter_RL = 0.0
		self.amperage_inverter_RR = 0.0
		self.amperage_inverter_S  = 0.0
		self.voltage_inverter_FL = 0.0
		self.voltage_inverter_FR = 0.0
		self.voltage_inverter_RL = 0.0
		self.voltage_inverter_RR = 0.0
		self.voltage_inverter_S  = 0.0
		self.rpm_motor_FL = 0.0
		self.rpm_motor_FR = 0.0
		self.rpm_motor_RL = 0.0
		self.rpm_motor_RR = 0.0
		self.rpm_motor_S  = 0.0

"""
	class RwsTelemetryData:
	
	representation of RWS telemetry data
"""
class RwsTelemetryData(object):
	def __init__(self):
		self.rpm_X = 0
		self.voltage_X = 0
		self.amperage_X = 0
		self.temperature_X = 0
		self.rpm_Y = 0
		self.voltage_Y = 0
		self.amperage_Y = 0
		self.temperature_Y = 0
		self.voltage_bat = 0
		self.voltage_jump = 0
		self.voltage_cpu = 0
		self.percentage_bat = 0
		self.alive = False
		self.jumping = False
		self.alive_X = False
		self.ready_X = False
		self.alarm_X = False
		self.home_fault_X = False
		self.alive_Y = False
		self.ready_Y = False
		self.alarm_Y = False
		self.home_fault_Y = False

"""
	class FollowMeData:

	representation of unit current follow me position
"""
class FollowMeData(object):
	def __init__(self):
		self.state = False
		self.screen_width = 0
		self.screen_height = 0
		self.marker_x = 0
		self.marker_y = 0
		self.distance = 0

"""
	interface UgvControlContext:
	
	access methods to set of UGV control values.
"""
class UgvControlContext(ABC):
	@abstractmethod
	def GetUgvControlData(self):
		raise NotImplementedError

"""
	interface UgvStateContext:
	
	access methods to report actual UGV state values.
"""
class UgvStateContext(ABC):
	@abstractmethod
	def GetRecentUgvState(self):
		raise NotImplementedError
	@abstractmethod
	def SetActualUgvState(self, state_data, loop_time):
		raise NotImplementedError

"""
	interface RwsControlContext:
	
	access methods to set of RWS control values.
"""
class RwsControlContext(ABC):
	@abstractmethod
	def GetRwsControlData(self):
		raise NotImplementedError

"""
	interface RwsStateContext:
	
	access methods to report actual unit state values.
"""
class RwsStateContext(ABC):
	def GetRecentRwsState(self):
		return None
	@abstractmethod
	def SetActualRwsState(self, state_data, loop_time):
		raise NotImplementedError

"""
	interface GPSContext:
	
	access methods to report actual GPS position.
"""
class GPSContext(ABC):
	def GetRecentPosition(self):
		return None
	@abstractmethod
	def SetActualPosition(self, gps_data):
		raise NotImplementedError

"""
	interface CompassContext:
	
	access methods to report actual compass heading.
"""
class CompassContext(ABC):
	def GetRecentHeading(self):
		return None
	@abstractmethod
	def SetActualHeading(self, compass_data):
		raise NotImplementedError

"""
	interface TemperaturesContext:
	
	access methods to report temperatures data.
"""
class TemperaturesContext(ABC):
	def GetRecentTemperatures(self):
		return None
	@abstractmethod
	def SetActualTemperatures(self, temperatures_data):
		raise NotImplementedError

"""
	interface UgvTelemetryContext:
	
	access methods to report UGV telemetry data.
"""
class UgvTelemetryContext(ABC):
	def GetRecentUgvTelemetry(self):
		return None
	@abstractmethod
	def SetActualUgvTelemetry(self, telemetry_data):
		raise NotImplementedError

"""
	interface RwsTelemetryContext:
	
	access methods to report RWS telemetry data.
"""
class RwsTelemetryContext(ABC):
	def GetRecentRwsTelemetry(self):
		return None
	@abstractmethod
	def SetActualRwsTelemetry(self, telemetry_data):
		raise NotImplementedError

"""
	interface FollowMeContext:

	access methods to report followme data.
"""
class FollowMeContext(ABC):
	def GetRecentFollowMeData(self):
		return None
	@abstractmethod
	def SetActualFollowMeData(self, follow_me_data):
		raise NotImplementedError


class UgvContext(UgvControlContext, UgvStateContext, UgvTelemetryContext, TemperaturesContext):
	pass
class RwsContext(RwsControlContext, RwsStateContext, RwsTelemetryContext):
	pass
class UnitContext(UgvContext, RwsContext, GPSContext, CompassContext):
	pass

"""
	class ControlDatagramProtocolBase:
	
	base constatnts and UDP packet composer/parser.
	
	ControlDatagramProtocolBase(context)
	
	context - reference to UnitContext implementing instance 
"""
class ControlDatagramProtocolBase(object):

	RWS_COMMAND_PKT_TYPE = 1
	RWS_COMMAND_FORMAT = "!4B 2h 2i 6B H i 2B 2x" # flags[4], rotV, eleV, rotP, eleP, arm[4], jump[2], jumpDuration cameraP, rangeSeq, jumpSeq
	RWS_RESPONCE_PKT_TYPE = 1
	RWS_RESPONCE_FORMAT = "!4B 3i I H 2B" # flags[4], rotP, eleP, cameraP, distance, shots, rangeSeq, jumpSeq
	UGV_COMMAND_PKT_TYPE = 2
	UGV_COMMAND_FORMAT = "!4B 2h" # flags[4], thr, steer 
	UGV_RESPONCE_PKT_TYPE = 2
	UGV_RESPONCE_FORMAT = "!4B 2h 2H 3b 1x" # flags[4], thrV, steerP, generatorVlt, batteryVlt, batteryPercentage, lowestVlt, highestVlt
	GPS_REPORT_PKT_TYPE = 3
	GPS_REPORT_FORMAT = "!4B 3i" # flags[4], lat, lon, alt
	COMPASS_REPORT_PKT_TYPE = 4
	COMPASS_REPORT_FORMAT = "!4B 2i H 2x" # flags[4], heading, elevation, level
	GYRO_REPORT_PKT_TYPE = 5
	GYRO_REPORT_FORMAT = "!4B 3h" # flags[4], x,y,z
	TEMPERATURES_REPORT_PKT_TYPE = 6
	TEMPERATURES_REPORT_FORMAT = "!8B 34b" # validFlags[8], temperatures[]
	UGV_TELEMETRY_REPORT_PKT_TYPE = 8
	UGV_TELEMETRY_REPORT_FORMAT = "!4B 8B 8B 5H 6x 5h 5H 5h" # flags[4], tempWarns[8], tempErr[8], drvFlags[5], rpms[5], voltages[5], amperages[5]
	RWS_TELEMETRY_REPORT_PKT_TYPE = 12
	RWS_TELEMETRY_REPORT_FORMAT = "!4B 4h 4h 3h H" # flags[4], xRpm, xU, xI, xT, yRpm, yU, yI, yT, batU, jumpU, cpuU, bat% 
	POWERS_REPORT_PKT_TYPE = 10
	FOLLOWME_REPORT_PKT_TYPE = 11
	FOLLOWME_REPORT_FORMAT = "!4B 4h i"
	COMMAND_RESPONCES_PKTS = {RWS_COMMAND_PKT_TYPE: RWS_RESPONCE_PKT_TYPE, UGV_COMMAND_PKT_TYPE: UGV_RESPONCE_PKT_TYPE}

	def __init__(self, context, has_ugv, has_rws):
		self.context = context
		self.has_ugv = has_ugv
		self.has_rws = has_rws
		self.retry = None
		self.last_pkt_type = None
		self.count_pkt_send = 0
		self.count_pkt_received = 0

	def ComposeUdpPacket(self):
		self.count_pkt_send += 1
#		self.GenerateTestPacket()
		if (self.has_rws and (not self.has_ugv
					or self.last_pkt_type != self.RWS_COMMAND_PKT_TYPE)):
			self.last_pkt_type = self.RWS_COMMAND_PKT_TYPE
			return self.RWS_COMMAND_PKT_TYPE, self.ComposeRwsControlUdpPacket()
		if (self.has_ugv and (not self.has_rws
					or self.last_pkt_type != self.UGV_COMMAND_PKT_TYPE)):
			self.last_pkt_type = self.UGV_COMMAND_PKT_TYPE
			return self.UGV_COMMAND_PKT_TYPE, self.ComposeUgvControlUdpPacket()

	def ComposeUgvControlUdpPacket(self):
		control_data = self.context.GetUgvControlData()
# 		print("thr:%.2f, str:%.2f"%(control_data.throttle, control_data.steering))
		return struct.pack(self.UGV_COMMAND_FORMAT,
				sum( [ 
					0x01 if control_data.motion_enable else 0,
					0x02 if control_data.motion_slow   else 0,
					0x04 if control_data.engine_on     else 0,
					0x08 if control_data.engine_start  else 0,
					0x10 if control_data.lights_on     else 0,
					0x20 if control_data.headlight     else 0,
					0x40 if control_data.marker_light  else 0,
					0x80 if control_data.charging_enable else 0,
				] ), # flags 1
				sum( [ 
					0x01 if control_data.pull_on        else 0,
					0x02 if control_data.horn_on        else 0,
					0x04 if control_data.diffsteer_on   else 0,
					0x08 if control_data.steerassist_on else 0,
					0x10 if control_data.winch_out      else 0,
					0x20 if control_data.winch_in       else 0,
					0x40 if control_data.followme_on    else 0,
				] ), # flags 2
				0, 0, # flags 3-4 
				ftos(control_data.throttle), ftos(control_data.steering),
		)

	def ComposeRwsControlUdpPacket(self):
		control_data = self.context.GetRwsControlData()
#		def fmt_fn(v):
#			return "---" if v is None else "{:.3f}".format(v)
#		print("rotV:{}, eleV:{}, rotP:{}, eleP:{}".format(
#				fmt_fn(control_data.rotationV), fmt_fn(control_data.elevationV),
#				fmt_fn(control_data.rotationP), fmt_fn(control_data.elevationP)))
		return struct.pack(self.RWS_COMMAND_FORMAT,
				sum( [
					0x01 if control_data.axisunit_enable else 0,
					0x02 if control_data.axisunit_slow   else 0,
					0x04 if control_data.axisunit_reload else 0,
					0x08 if control_data.axisunit_force_home else 0,
				] ), # flags 1
				sum( [
					0x01 if not control_data.rotationV is None else 0,
					0x02 if not control_data.elevationV is None else 0,
					0x04 if not control_data.rotationP is None else 0,
					0x08 if not control_data.elevationP is None else 0,
					0x10 if control_data.axisunit_vel_prio else 0,
					0x20 if control_data.axisunit_vel_prio else 0,
				] ), # flags 2
				0, 0, # flags 3-4 
				ftos(control_data.rotationV),
				ftos(control_data.elevationV),
				rtol(control_data.rotationP),
				rtol(control_data.elevationP),
				ord('A') if control_data.safetyARM == 'ARM' else 0,
				0, 0, 0,
				ord('F') if control_data.JUMP == 'JUMP' else 0,
				0,
				control_data.jump_duration,
				control_data.camerasP,
				control_data.rangefinder_seq,
				control_data.jump_seq % 256,
		)

	def GenerateTestPacket(self):
		if self.count_pkt_send % 10 == 1:
			t = ControlDatagramProtocolBase.UGV_RESPONCE_PKT_TYPE
			f = ControlDatagramProtocolBase.UGV_RESPONCE_FORMAT
		elif self.count_pkt_send % 10 == 2:
			t = ControlDatagramProtocolBase.RWS_RESPONCE_PKT_TYPE
			f = ControlDatagramProtocolBase.RWS_RESPONCE_FORMAT
		elif self.count_pkt_send % 20 == 5:
			t = ControlDatagramProtocolBase.TEMPERATURES_REPORT_PKT_TYPE
			f = ControlDatagramProtocolBase.TEMPERATURES_REPORT_FORMAT
		elif self.count_pkt_send % 20 == 15:
			t = ControlDatagramProtocolBase.UGV_TELEMETRY_REPORT_PKT_TYPE
			f = ControlDatagramProtocolBase.UGV_TELEMETRY_REPORT_FORMAT
		elif self.count_pkt_send % 20 == 17:
			t = ControlDatagramProtocolBase.RWS_TELEMETRY_REPORT_PKT_TYPE
			f = ControlDatagramProtocolBase.RWS_TELEMETRY_REPORT_FORMAT
		else:
			return
		print("UDP RX Test:", t)
		l = struct.calcsize(f)
		data = [random.randrange(255) for n in range(l)]
		self.ParseUdpPacket(t, bytes(data), 0)

	def ParseUdpPacket(self, pkt_type, data, loop_time):
		self.count_pkt_received += 1
		if pkt_type == self.RWS_RESPONCE_PKT_TYPE:
			return self.ParseRwsUdpPacket(data, loop_time)
		elif pkt_type == self.UGV_RESPONCE_PKT_TYPE:
			return self.ParseUgvUdpPacket(data, loop_time)
		elif pkt_type == self.GPS_REPORT_PKT_TYPE:
			return self.ParseGpsUdpPacket(data)
		elif pkt_type == self.COMPASS_REPORT_PKT_TYPE:
			return self.ParseCompassUdpPacket(data)
		elif pkt_type == self.GYRO_REPORT_PKT_TYPE:
			return self.ParseGyroUdpPacket(data)
		elif pkt_type == self.TEMPERATURES_REPORT_PKT_TYPE:
			return self.ParseTemperaturesUdpPacket(data)
		elif pkt_type == self.UGV_TELEMETRY_REPORT_PKT_TYPE:
			return self.ParseUgvTelemetryUdpPacket(data)
		elif pkt_type == self.RWS_TELEMETRY_REPORT_PKT_TYPE:
			return self.ParseRwsTelemetryUdpPacket(data)
		elif pkt_type == self.FOLLOWME_REPORT_PKT_TYPE:
			return self.ParseFollowMeUdpPacket(data)
		return False

	def ParseUgvUdpPacket(self, data, loop_time):
#		print("RX ugv l:%i t:%i"%(len(data), loop_time))
		state = self.context.GetRecentUgvState()
		if not state: return
		(flags0, flags1, flags2, flags3, throttle, steering,
				generatorVlt, batteryVlt, batteryPercentage,
				lowestVlt, highestVlt) = struct.unpack(self.UGV_RESPONCE_FORMAT, data)
		state.connected = True
		state.throttleV = stof(throttle)
		state.steeringP = stof(steering)
		state.batteryU = batteryVlt
		state.batteryPercentage = batteryPercentage
		state.generatorU = generatorVlt
		state.cell_lowestU = lowestVlt * 0.1
		state.cell_highestU = highestVlt * 0.1
		state.drive_faults = tuple([bool(flags2 & (1 << n)) for n in range(0,5)])
		state.followme_on = bool(flags2 & (1 << 5))
		self.context.SetActualUgvState(state, loop_time)
#		print("Vb={}, Vg={} lo={} hi={} OK".format(batteryVlt, generatorVlt, lowestVlt, highestVlt))
		return True

	def ParseRwsUdpPacket(self, data, loop_time):
#		print("RX RWS status l:%i t:%i"%(len(data), loop_time))
		state = self.context.GetRecentRwsState()
		if not state: return
		(flags0, flags1, flags2, flags3, rotationP, elevationP, camP, dist, shots, rangeSeq, jumpSeq) = struct.unpack(self.RWS_RESPONCE_FORMAT, data)
#		print("RX RWS status f:%02X r:%d, dist:%f (%d)"%(flags1, rotationP, dist, rangeSeq ))
		state.connected = True
#		state.rotationV = stof(rotationV, flags1 & 0x01)
#		state.elevationV = stof(elevationV, flags1 & 0x02)
		state.rotationP = ltor(rotationP, flags1 & 0x04) 
		state.elevationP = ltor(elevationP, flags1 & 0x08)
		state.camerasP = camP
		state.rangefinder_distance = dist * 0.001 # mm to m
		state.rangefinder_seq = rangeSeq
		state.jump_seq = jumpSeq
		state.shots = shots
		self.context.SetActualRwsState(state, loop_time)
		return True

	def ParseGpsUdpPacket(self, data):
		gps = self.context.GetRecentPosition()
		if not gps: return
		(flags0, flags1, flags2, flags3, lon, lat, alt) = struct.unpack(self.GPS_REPORT_FORMAT, data)
		gps.fixed = bool(flags0 & 0x01)
		gps.lat = ltof(lat) * 90.0
		gps.lon = ltof(lon) * 90.0
#		print("RX GPS l:%i, %i, %i, %i, %.4f, %.4f"%(len(data), lat, lon, alt, gps.lat, gps.lon))
		self.context.SetActualPosition(gps)
		return True

	def ParseCompassUdpPacket(self, data):
		compass = self.context.GetRecentHeading()
		if not compass: return
		(flags0, flags1, flags2, flags3, heading, elevation, level) = struct.unpack(self.COMPASS_REPORT_FORMAT, data)
		compass.heading = ltof(heading) * 180.0
		compass.elevation = ltof(elevation) * 180.0
		compass.level = level
#		print("RX Compass l:%i, %.1f, %.1f"%(len(data), compass.heading, compass.elevation))
		self.context.SetActualHeading(compass)
		return True

	def ParseGyroUdpPacket(self, data):
#		print("RX Gyro l:%i"%(len(data)))
		return True

	def ParseTemperaturesUdpPacket(self, data):
#		print("RX Temperature l:%i"%len(data))
		temperatures = self.context.GetRecentTemperatures()
		if not temperatures: return
		items = struct.unpack(self.TEMPERATURES_REPORT_FORMAT, data) 
		valids = tuple(bit for bits in (unpack_bits(items[n]) for n in range(5)) for bit in bits)
		temps = items[8:42]
		(
			temperatures.temperature_inverter_FL,
			temperatures.temperature_inverter_FR,
			temperatures.temperature_inverter_RL,
			temperatures.temperature_inverter_RR,
			temperatures.temperature_inverter_S,
			temperatures.temperature_charger,
			temperatures.temperature_motor_S,  
			temperatures.temperature_motor_ext_S,  
			temperatures.temperature_motor_FL,
			temperatures.temperature_motor_ext_FL,
			temperatures.temperature_motor_FR,  
			temperatures.temperature_motor_ext_FR,  
			temperatures.temperature_motor_RL,  
			temperatures.temperature_motor_ext_RL,  
			temperatures.temperature_motor_RR,  
			temperatures.temperature_motor_ext_RR,  
			temperatures.temperature_generator,
			temperatures.temperature_battery1,
			temperatures.temperature_battery2,
			temperatures.temperature_battery3,
			temperatures.temperature_battery4,
			temperatures.temperature_battery5,
			temperatures.temperature_psu,
			pad, 
			pad, 
			temperatures.temperature_body_F,
			temperatures.temperature_engine, 
			temperatures.temperature_body_R,
		) = [(i if v else None) for i,v in zip(temps, valids)][:28]
		self.context.SetActualTemperatures(temperatures)
		# for ind, (t, v) in enumerate(zip(temps, valids), 1):
		# 	print('#:', ind, ' temp: ', t, ' valid: ', v)
		return True

	def ParseUgvTelemetryUdpPacket(self, data):
#		print("RX Telemetry l:%i"%len(data))
		telemetry = self.context.GetRecentUgvTelemetry()
		if not telemetry: return
		(
			flags0, flags1, flags2, flags3,
			flags_temp_warn0, flags_temp_warn1, flags_temp_warn2, flags_temp_warn3, flags_temp_warn4,
			pad, pad, pad,
			flags_temp_err0, flags_temp_err1, flags_temp_err2, flags_temp_err3, flags_temp_err4,
			pad, pad, pad,
			flags_inverter_FL,
			flags_inverter_FR,
			flags_inverter_RL,
			flags_inverter_RR,
			flags_inverter_S,
			telemetry.rpm_motor_FL,
			telemetry.rpm_motor_FR,
			telemetry.rpm_motor_RL,
			telemetry.rpm_motor_RR,
			telemetry.rpm_motor_S,
			telemetry.voltage_inverter_FL,
			telemetry.voltage_inverter_FR,
			telemetry.voltage_inverter_RL,
			telemetry.voltage_inverter_RR,
			telemetry.voltage_inverter_S,
			telemetry.amperage_inverter_FL,
			telemetry.amperage_inverter_FR,
			telemetry.amperage_inverter_RL,
			telemetry.amperage_inverter_RR,
			telemetry.amperage_inverter_S,
		) = struct.unpack(self.UGV_TELEMETRY_REPORT_FORMAT, data)
		(
			telemetry.temp_warn_inverter_FL,
			telemetry.temp_warn_inverter_FR,
			telemetry.temp_warn_inverter_RL,
			telemetry.temp_warn_inverter_RR,
			telemetry.temp_warn_inverter_S,
			telemetry.temp_warn_charger,
			telemetry.temp_warn_motor_S,
			telemetry.temp_warn_motor_ext_S,
		) = unpack_bits(flags_temp_warn0)
		(
			telemetry.temp_warn_motor_FL,
			telemetry.temp_warn_motor_ext_FL,
			telemetry.temp_warn_motor_FR,
			telemetry.temp_warn_motor_ext_FR,
			telemetry.temp_warn_motor_RL,
			telemetry.temp_warn_motor_ext_RL,
			telemetry.temp_warn_motor_RR,
			telemetry.temp_warn_motor_ext_RR,
		) = unpack_bits(flags_temp_warn1)
		(
			telemetry.temp_warn_generator,
			telemetry.temp_warn_battery1,
			telemetry.temp_warn_battery2,
			telemetry.temp_warn_battery3,
			telemetry.temp_warn_battery4,
			telemetry.temp_warn_battery5,
			telemetry.temp_warn_psu,
			pad,
		) = unpack_bits(flags_temp_warn2)
		(
			pad,
			telemetry.temp_warn_body_F,
			telemetry.temp_warn_engine,
			telemetry.temp_warn_body_R,
		) = unpack_bits(flags_temp_warn3, 4)
		(
			telemetry.temp_err_inverter_FL,
			telemetry.temp_err_inverter_FR,
			telemetry.temp_err_inverter_RL,
			telemetry.temp_err_inverter_RR,
			telemetry.temp_err_inverter_S,
			telemetry.temp_err_charger,
			telemetry.temp_err_motor_S,
			telemetry.temp_err_motor_ext_S,
		) = unpack_bits(flags_temp_err0)
		(
			telemetry.temp_err_motor_FL,
			telemetry.temp_err_motor_ext_FL,
			telemetry.temp_err_motor_FR,
			telemetry.temp_err_motor_ext_FR,
			telemetry.temp_err_motor_RL,
			telemetry.temp_err_motor_ext_RL,
			telemetry.temp_err_motor_RR,
			telemetry.temp_err_motor_ext_RR,
		) = unpack_bits(flags_temp_err1)
		(
			telemetry.temp_err_generator,
			telemetry.temp_err_battery1,
			telemetry.temp_err_battery2,
			telemetry.temp_err_battery3,
			telemetry.temp_err_battery4,
			telemetry.temp_err_battery5,
			telemetry.temp_err_psu,
			pad,
		) = unpack_bits(flags_temp_err2)
		(
			pad,
			telemetry.temp_err_body_F,
			telemetry.temp_err_engine,
			telemetry.temp_warn_body_R,
		) = unpack_bits(flags_temp_err3, 4)
		(
			telemetry.disconnected_inverter_FL,
			telemetry.overload_inverter_FL,
			telemetry.protection_inverter_FL,
			telemetry.fault_command_FL,
			telemetry.fault_voltage_FL,
			telemetry.fault_encoder_FL,
			telemetry.fault_drive_FL,
			telemetry.fault_overload_FL,
			telemetry.fault_overheat_FL,
		) = unpack_bits(flags_inverter_FL, 9)
		(
			telemetry.disconnected_inverter_FR,
			telemetry.overload_inverter_FR,
			telemetry.protection_inverter_FR,
			telemetry.fault_command_FR,
			telemetry.fault_voltage_FR,
			telemetry.fault_encoder_FR,
			telemetry.fault_drive_FR,
			telemetry.fault_overload_FR,
			telemetry.fault_overheat_FR,
		) = unpack_bits(flags_inverter_FR, 9)
		(
			telemetry.disconnected_inverter_RL,
			telemetry.overload_inverter_RL,
			telemetry.protection_inverter_RL,
			telemetry.fault_command_RL,
			telemetry.fault_voltage_RL,
			telemetry.fault_encoder_RL,
			telemetry.fault_drive_RL,
			telemetry.fault_overload_RL,
			telemetry.fault_overheat_RL,
		) = unpack_bits(flags_inverter_RL, 9)
		(
			telemetry.disconnected_inverter_RR,
			telemetry.overload_inverter_RR,
			telemetry.protection_inverter_RR,
			telemetry.fault_command_RR,
			telemetry.fault_voltage_RR,
			telemetry.fault_encoder_RR,
			telemetry.fault_drive_RR,
			telemetry.fault_overload_RR,
			telemetry.fault_overheat_RR,
		) = unpack_bits(flags_inverter_RR, 9)
		(
			telemetry.disconnected_inverter_S,
			telemetry.overload_inverter_S,
			telemetry.protection_inverter_S,
			telemetry.fault_command_S,
			telemetry.fault_voltage_S,
			telemetry.fault_encoder_S,
			telemetry.fault_drive_S,
			telemetry.fault_overload_S,
			telemetry.fault_overheat_S,
		) = unpack_bits(flags_inverter_S, 9)
		self.context.SetActualUgvTelemetry(telemetry)
		return True

	def ParseRwsTelemetryUdpPacket(self, data):
#		print("RX RWS telemetry l:%i"%len(data))
		telemetry = self.context.GetRecentRwsTelemetry()
		if not telemetry: return
		(
			flags0, flags1, flags2, flags3,
			telemetry.rpm_X,
			voltage_X,
			telemetry.amperage_X,
			telemetry.temperature_X,
			telemetry.rpm_Y,
			voltage_Y,
			telemetry.amperage_Y,
			telemetry.temperature_Y,
			voltage_bat,
			voltage_jump,
			voltage_cpu,
			percentage_bat,
		) = struct.unpack(self.RWS_TELEMETRY_REPORT_FORMAT, data)
		telemetry.voltage_X = voltage_X * 0.01
		telemetry.voltage_Y = voltage_Y * 0.01
		telemetry.voltage_bat = voltage_bat * 0.01
		telemetry.voltage_jump = voltage_jump * 0.01
		telemetry.voltage_cpu = voltage_cpu * 0.01
		telemetry.percentage_bat = ustof(percentage_bat) * 100.0
		(
			telemetry.alive,
			telemetry.jumping,
		) = unpack_bits(flags0, 2)
		(
			telemetry.alive_X,
			telemetry.ready_X,
			telemetry.alarm_X,
			telemetry.home_fault_X,
		) = unpack_bits(flags2, 4)
		(
			telemetry.alive_Y,
			telemetry.ready_Y,
			telemetry.alarm_Y,
			telemetry.home_fault_Y,
		) = unpack_bits(flags3, 4)
		self.context.SetActualRwsTelemetry(telemetry)
		return True

	def ParseFollowMeUdpPacket(self, data):
		follow_me = self.context.GetRecentFollowMeData()
		if not follow_me: return
		(flags0, flags1, flags2, flags3,
			screen_w, screen_h, marker_x, marker_y, distance) = struct.unpack(self.FOLLOWME_REPORT_FORMAT, data)
		follow_me.state = bool(flags0 & 0x01)
		follow_me.screen_width = screen_w
		follow_me.screen_height = screen_h
		follow_me.marker_x = marker_x
		follow_me.marker_y = marker_y
		follow_me.distance = distance
		self.context.SetActualFollowMeData(follow_me)
		return True

	def OnConnectionTimeout(self):
		self.OnConnectionError('timeout')

	def OnConnectionError(self, *args):
		state = self.context.GetRecentUgvState()
		if state:
			state.connected = False
			state.last_error = args
			self.context.SetActualUgvState(state, self.timeout)
		state = self.context.GetRecentRwsState()
		if state:
			state.connected = False
			state.last_error = args
			self.context.SetActualRwsState(state, self.timeout)

"""
	class Stm32ParamsDatagramProtocolTwisted:
	
	protocol implementation for twisted reactor library
"""
class ControlDatagramProtocolTwisted(UdpProtocolTwisted, ControlDatagramProtocolBase):
	def __init__(self, udp_host, udp_port, period, timeout,
				listen_port=LISTEN_PORT, on_data=None, on_error=None,
				on_restored=None, on_tx=None, hash_salt=None,
				has_ugv=True, has_rws=True, context=None):
# 		print "proto init", period, timeout
		UdpProtocolTwisted.__init__(self, udp_host, udp_port, period = period,
								timeout = timeout, listen_port = listen_port,
								on_data = on_data, on_error = on_error,
								on_restored = on_restored, on_tx = on_tx,
								hash_salt = hash_salt,
								)
		ControlDatagramProtocolBase.__init__(self, context,
								has_ugv = has_ugv, has_rws = has_rws)

class AntibugUnitCommunicator(UnitContext):
	def __init__(self,
				 host=UDP_HOST,
				 port=UDP_PORT,
				 period=PERIOD_MS / 1000.0,
				 timeout=TIMEOUT_MS / 1000.0,
				 listen_port=LISTEN_PORT,
				 on_data=None,
				 on_error=None,
				 on_restored=None,
				 on_tx=None,
				 hash_salt=None,
				 has_ugv=True, has_rws=True,
	):
		super(AntibugUnitCommunicator, self).__init__()
		self.ugv_control = UgvControlData()
		self.rws_control = RwsControlData()
		self.ugv_state = UgvStateData()
		self.rws_state = RwsStateData()
		self.unit_gps = GPSData()
		self.unit_heading = CompassData()
		self.unit_temperatures = TemperaturesData()
		self.ugv_telemetry = UgvTelemetryData()
		self.rws_telemetry = RwsTelemetryData()
		self.unit_follow_me = FollowMeData()
		self.connected = False
		self.avg_loop_time = 0.0
		self.timeout = timeout
		self.protocol = ControlDatagramProtocolTwisted(
				host, port, period, timeout,
				listen_port=listen_port, on_data=on_data,
				on_error=on_error, on_restored=on_restored, on_tx=on_tx,
				hash_salt=hash_salt,
				has_ugv=has_ugv, has_rws=has_rws, context=self)
		self.reset_call = None
		self.OnUgvStateChanged = None 
		self.OnRwsStateChanged = None 
		self.OnConnectionLost = None 
		self.OnConnectionEstablished = None
		self.OnPositionFixed = None
		self.OnPositionLost = None
		self.OnPositionChanged = None
		self.OnHeadingChanged = None
		self.OnTemperaturesChanged = None 
		self.OnUgvTelemetryChanged = None 
		self.OnRwsTelemetryChanged = None 
		self.OnDistanceChanged = None
		self.OnFollowMeChanged = None 

	def SetConnected(self, connected, loop_time):
		self.avg_loop_time = self.avg_loop_time + 0.3*(loop_time - self.avg_loop_time)
		if self.connected != connected:
			self.connected = connected
			if connected:
				if self.OnConnectionEstablished: self.OnConnectionEstablished()
			else: 
				if self.OnConnectionLost: self.OnConnectionLost()
		if not connected and not self.reset_call:
			self.reset_call = reactor.callLater(self.timeout, self.Reset) 
		elif connected and self.reset_call:
			self.reset_call.cancel()
			self.reset_call = None

	#virtual UgvControlContext implementations
	def GetUgvControlData(self):
		return self.ugv_control

	#virtual RwsControlContext implementations
	def GetRwsControlData(self):
		return self.rws_control

	#virtual UgvStateContext implementations
	def GetRecentUgvState(self):
		return copy.copy(self.ugv_state)

	def SetActualUgvState(self, state, loop_time):
		self.SetConnected(state.connected, loop_time)
		if not self.ugv_state == state:
			self.ugv_state = state
			if self.OnUgvStateChanged: self.OnUgvStateChanged()
	
	#virtual RwsStateContext implementations
	def GetRecentRwsState(self):
		return copy.copy(self.rws_state)

	def SetActualRwsState(self, state, loop_time):
		self.SetConnected(state.connected, loop_time)
		state_changed = (self.rws_state != state)
		if state_changed:
			seq_changed = (self.rws_state.rangefinder_seq != state.rangefinder_seq)
			seq_match = (state.rangefinder_seq and state.rangefinder_seq == self.rws_control.rangefinder_seq)
			self.rws_state = state
			if not seq_match:
				self.rws_state.rangefinder_distance = None
			if seq_changed and seq_match:
				if self.OnDistanceChanged: self.OnDistanceChanged()
			if self.OnRwsStateChanged: self.OnRwsStateChanged()

	#virtual GPSContext implementations
	def GetRecentPosition(self):
		return copy.copy(self.unit_gps)

	def SetActualPosition(self, gps):
		fix_changed = (self.unit_gps.fixed != gps.fixed)
		self.unit_gps = gps
		if fix_changed:
			if gps.fixed:
				if self.OnPositionFixed: self.OnPositionFixed()
			else: 
				if self.OnPositionLost: self.OnPositionLost()
		if self.OnPositionChanged: self.OnPositionChanged()

	#virtual CompassContext implementations
	def GetRecentHeading(self):
		return copy.copy(self.unit_heading)

	def SetActualHeading(self, compass):
		if self.unit_heading != compass:
			self.unit_heading = compass
			if self.OnHeadingChanged: self.OnHeadingChanged()

	#virtual TemperaturesContext implementations
	def GetRecentTemperatures(self):
		return copy.copy(self.unit_temperatures)

	def SetActualTemperatures(self, temperatures):
		if self.unit_temperatures != temperatures:
			self.unit_temperatures = temperatures
			if self.OnTemperaturesChanged: self.OnTemperaturesChanged()

	#virtual UgvTelemetryContext implementations
	def GetRecentUgvTelemetry(self):
		return copy.copy(self.ugv_telemetry)

	def SetActualUgvTelemetry(self, telemetry):
		if self.ugv_telemetry != telemetry:
			self.ugv_telemetry = telemetry
			if self.OnUgvTelemetryChanged: self.OnUgvTelemetryChanged()

	#virtual RwsTelemetryContext implementations
	def GetRecentRwsTelemetry(self):
		return copy.copy(self.rws_telemetry)

	def SetActualRwsTelemetry(self, telemetry):
		if self.rws_telemetry != telemetry:
			self.rws_telemetry = telemetry
			if self.OnRwsTelemetryChanged: self.OnRwsTelemetryChanged()

	#virtual FollowMeContext implementations
	def GetRecentFollowMeData(self):
		return copy.copy(self.unit_follow_me)

	def SetActualFollowMeData(self, follow_me_data):
		if self.unit_follow_me != follow_me_data:
			self.unit_follow_me = follow_me_data
			if self.OnFollowMeChanged: self.OnFollowMeChanged()

	def Reset(self):
		self.reset_call = None
		self.protocol.Stop()
		self.protocol.Start()

	def Start(self):
		self.protocol.Start()

	def Stop(self):
		self.protocol.Stop()

def f2printable(v):
	return "  -  " if v is None else "%+4d%%"%int(v*100.0)
def r2printable(v):
	return "  -  " if v is None else "%+4d\xb0"%round(math.degrees(v))
def rangedChange(value, step):
	value = (value or 0.0) + step
	if value > 1.0: value = 1.0
	if value < -1.0: value = -1.0
	return value
def angleChange(value, degrees):
	value = (value or 0.0) + math.radians(degrees)
	if value > math.pi: value = math.pi
	if value < -math.pi: value = -math.pi
	return value

class AntibugUnitControlCommandLineApp():
	keys_to_buttons_remap = {'`':0, '~':0, '1':1, '2':2, '3':3, '4':4, '5':5, '6':6, '7':7, '8':8, '9':9, '0':10, '-':11, '_':11, '=':12, '+':12, '[':13, ']':14}
	buttons_to_ugv_props_remap = {0:'motion_enable', 1:'motion_slow', 2:'engine_on', 3:'engine_start', 4:'lights_on', 5:'headlight', 6:'marker_light', 7:'charging_enable', 8:'pull_on', 9:'horn_on', 13:'winch_out', 14:'winch_in'}
	buttons_to_rws_props_remap = {10:'axisunit_enable', 11:'axisunit_slow', 12:'axisunit_reload'}
	def __init__(self, host, port, period, timeout, salt=None):
		self.key_reader = CommandLineKeyReader()
		self.status_printer = StatusLinePrinter('{0.thrv} {0.strp} {0.elev} {0.rotv} {0.elep} {0.rotp} {0.btns} {0.ARM}{0.JUMP} {0.gots} {0.tele} {0.anim} {0.msg}')
		self.comm = comm = AntibugUnitCommunicator(host, port, period, timeout, on_data = self.print_state, hash_salt=salt)
		self.animator = CommunicationAnimator(0.25)
		self.msg = ''
		self.got_ugv_state = False
		self.got_rws_state = False
		self.got_position = False
		self.got_heading = False
		self.got_temperatures = False
		self.got_ugv_telemetry = False
		self.got_rws_telemetry = False
		comm.OnUgvStateChanged = self.OnUgvStateChanged 
		comm.OnRwsStateChanged = self.OnRwsStateChanged 
		comm.OnConnectionLost = self.OnConnectionLost 
		comm.OnConnectionEstablished = self.OnConnectionEstablished
		comm.OnPositionFixed = self.OnPositionFixed
		comm.OnPositionLost = self.OnPositionLost
		comm.OnPositionChanged = self.OnPositionChanged
		comm.OnHeadingChanged = self.OnHeadingChanged
		comm.OnTemperaturesChanged = self.OnTemperaturesChanged
		comm.OnUgvTelemetryChanged = self.OnUgvTelemetryChanged
		comm.OnRwsTelemetryChanged = self.OnRwsTelemetryChanged
		comm.OnDistanceChanged = self.OnDistanceChanged
		print("AntibugUnit UDP controller %s:%d" % (host, port))
		print("Use WASD and arrows keys to control movement, SPACE for stop, Enter for center")
		print("    top row keys toggle buttons     `1234567890-=[] Backspace for ARM, Del for JUMP")
		print("                                    MMESLLLCPHTTTWW SF")
		print(" ThrV  StrP  EleV  RotV  EleP  RotP ESntihmEuoESROI FR")
		self.print_state()
	def run(self):
		self.comm.Start()  
		LoopingCall(self.tick).start(1.0 / 10)
# 		LoopingCall(self.print_state).start(1.0 / 4)
		reactor.run()
	def tick(self):
		key = self.key_reader().lower()
		if key:
			ugv_control = self.comm.ugv_control
			rws_control = self.comm.rws_control
			if key in ('w',):
				ugv_control.throttle = rangedChange(ugv_control.throttle, 0.1)
			elif key in ('s',):
				ugv_control.throttle = rangedChange(ugv_control.throttle, - 0.1)
			elif key in ('a',):
				ugv_control.steering = rangedChange(ugv_control.steering, - 0.2)
			elif key in ('d',):
				ugv_control.steering = rangedChange(ugv_control.steering, 0.2)
			elif key in ('up',):
				rws_control.elevationV = rangedChange(rws_control.elevationV, 0.1)
				rws_control.elevationP = math.pi
				rws_control.axisunit_vel_prio = True
			elif key in ('down',):
				rws_control.elevationV = rangedChange(rws_control.elevationV, -0.1)
				rws_control.elevationP = -math.pi
				rws_control.axisunit_vel_prio = True
			elif key in ('left',):
				rws_control.rotationV = rangedChange(rws_control.rotationV, -0.1)
				rws_control.rotationP = -math.pi
				rws_control.axisunit_vel_prio = True
			elif key in ('right',):
				rws_control.rotationV = rangedChange(rws_control.rotationV, 0.1)
				rws_control.rotationP = math.pi
				rws_control.axisunit_vel_prio = True
			elif key in ('ctrlup',):
				rws_control.elevationV = None
				rws_control.elevationP = angleChange(rws_control.elevationP, 90/8)
				rws_control.axisunit_vel_prio = False
			elif key in ('ctrldown',):
				rws_control.elevationV = None
				rws_control.elevationP = angleChange(rws_control.elevationP, -90/8)
				rws_control.axisunit_vel_prio = False
			elif key in ('ctrlleft',):
				rws_control.rotationV = None
				rws_control.rotationP = angleChange(rws_control.rotationP, -90/8)
				rws_control.axisunit_vel_prio = False
			elif key in ('ctrlright',):
				rws_control.rotationV = None
				rws_control.rotationP = angleChange(rws_control.rotationP, 90/8)
				rws_control.axisunit_vel_prio = False
			elif key in ('altup',):
				rws_control.elevationV = 1
				rws_control.elevationP = angleChange(rws_control.elevationP, 1)
				rws_control.axisunit_vel_prio = False
			elif key in ('altdown',):
				rws_control.elevationV = 1
				rws_control.elevationP = angleChange(rws_control.elevationP, -1)
				rws_control.axisunit_vel_prio = False
			elif key in ('altleft',):
				rws_control.rotationV = 1
				rws_control.rotationP = angleChange(rws_control.rotationP, -1)
				rws_control.axisunit_vel_prio = False
			elif key in ('altright',):
				rws_control.rotationV = 1
				rws_control.rotationP = angleChange(rws_control.rotationP, 1)
				rws_control.axisunit_vel_prio = False
			elif key == ' ': #space
				ugv_control.throttle = 0.0
				rws_control.elevationV = 0.0
				rws_control.rotationV = 0.0
				rws_control.elevationP = None
				rws_control.rotationP = None
				rws_control.axisunit_vel_prio = True
			elif key == 'enter': 
				ugv_control.steering = 0.0
				ugv_control.elevationV = None
				ugv_control.rotationV = None
				ugv_control.elevationP = 0.0
				ugv_control.rotationP = 0.0
				rws_control.axisunit_vel_prio = False
			elif key == 'backspace':
				rws_control.safetyARM = (None if rws_control.safetyARM else 'ARM')
				self.msg += 'safety toggled'
			elif key in ('del', 'shiftdel', 'ctrldel', 'altdel'):
				rws_control.JUMP = (None if rws_control.JUMP else 'JUMP')
				if rws_control.JUMP:
					rws_control.jump_seq += 1
					if key == 'del':
						rws_control.jump_duration = 100
						self.msg += 'JUMP.'
					elif key == 'shiftdel':
						rws_control.jump_duration = 1000
						self.msg += 'JUMP..'
					elif key == 'ctrldel':
						rws_control.jump_duration = 10000
						self.msg += 'JUMP...'
					elif key == 'altdel':
						rws_control.jump_duration = 0
						self.msg += 'JUMP___'
			elif key == 'r':
				rws_control.rangefinder_seq = (rws_control.rangefinder_seq + 1) % 256
				self.msg += 'RANGE seq=%d'%rws_control.rangefinder_seq
			elif key in self.keys_to_buttons_remap:
				n = self.keys_to_buttons_remap[key]
				prop = self.buttons_to_ugv_props_remap.get(n, None)
				if prop:
					setattr(ugv_control, prop, not getattr(ugv_control, prop))
					self.msg += '%s toggled'%prop
				prop = self.buttons_to_rws_props_remap.get(n, None)
				if prop:
					setattr(rws_control, prop, not getattr(rws_control, prop))
					self.msg += '%s toggled'%prop
			elif key == 'f': 
				rws_control.JUMP = 'F' if rws_control.JUMP != 'F' else '\0'
				if rws_control.JUMP:
					rws_control.jump_seq += 1
					rws_control.jump_duration = 10
			elif key in ('q', 'esc', 'altf4'): 
				reactor.stop()
			else: self.msg += 'key: "%s" ignored'%repr(key)
			self.print_state()
	def OnUgvStateChanged(self):
		self.got_ugv_state = True
		self.print_state(0)
	def OnRwsStateChanged(self):
		self.got_rws_state = True
		self.print_state(0)
	def OnConnectionLost(self):
		self.msg += repr(self.comm.ugv_state.last_error)
		self.print_state(0)
	def OnConnectionEstablished(self):
		self.msg += "Connection Established"
		self.print_state(0)
	def OnPositionFixed(self):
		self.msg += "GPS fixed"
		self.print_state(0)
	def OnPositionLost(self):
		self.msg += "GPS lost"
		self.print_state(0)
	def OnPositionChanged(self):
		self.got_position = True
		self.msg += "GPS %.4f %.4f"%(self.comm.unit_gps.lat, self.comm.unit_gps.lon)
		self.print_state(0)
	def OnHeadingChanged(self):
		self.got_heading = True
		self.msg += "Heading %.1f"%(self.comm.unit_heading.heading)
		self.print_state(0)
	def OnTemperaturesChanged(self):
		self.got_temperatures = True
#		self.msg += "Temperatures"
		self.print_state(0)
	def OnUgvTelemetryChanged(self):
		self.got_ugv_telemetry = True
#		self.msg += "UGV Telemetry"
		self.print_state(0)
	def OnRwsTelemetryChanged(self):
		self.got_rws_telemetry = True
#		self.msg += "RWS Telemetry"
		self.print_state(0)
	def OnDistanceChanged(self):
		self.msg += "Distance measured %.1f"%self.comm.rws_state.rangefinder_distance
		self.print_state(0)
	def print_state(self, animation_speed=None):
			import sys
			ugv_control = self.comm.ugv_control
			rws_control = self.comm.rws_control
			ugv_state = self.comm.ugv_state
			state_text = " time:%4dms Unit: %+4d%% %+4d%% " % (self.comm.avg_loop_time*1000, int(ugv_state.throttleV*100.0), int(ugv_state.steeringP*100.0))  
			self.status_printer.thrv=f2printable(ugv_control.throttle)
			self.status_printer.strp=f2printable(ugv_control.steering)
			self.status_printer.elev=f2printable(rws_control.elevationV)
			self.status_printer.rotv=f2printable(rws_control.rotationV)
			self.status_printer.elep=r2printable(rws_control.elevationP)
			self.status_printer.rotp=r2printable(rws_control.rotationP)
			self.status_printer.btns="".join([('X' if any([
					*(v and getattr(ugv_control, v) for v in [self.buttons_to_ugv_props_remap.get(n)]),
					*(v and getattr(rws_control, v) for v in [self.buttons_to_rws_props_remap.get(n)]),
					]) else '.'
				) for n in range(1 + max(self.keys_to_buttons_remap.values()))])
			self.status_printer.ARM=('X' if rws_control.safetyARM == 'ARM' else '.')
			self.status_printer.JUMP=('X' if rws_control.JUMP == 'JUMP' else '.')
			self.status_printer.gots=''.join( (abr if got else '~') for abr,got in zip(
					"URPHTur",
					(self.got_ugv_state, self.got_rws_state, self.got_position, self.got_heading, self.got_temperatures, self.got_ugv_telemetry, self.got_rws_telemetry)
				))
			self.status_printer.tele=state_text if ugv_state.connected else "NO CONNECTION".center(len(state_text), '-')
			self.status_printer.anim=(self.animator.AnimateOk if ugv_state.connected else self.animator.AnimateErr)(animation_speed)
			self.status_printer.msg=self.msg
			self.status_printer.print()
			if animation_speed is None:
				self.msg = ''
				self.got_state = False
				self.got_position = False
				self.got_heading = False
				self.got_temperatures = False
				self.got_ugv_telemetry = False
				self.got_rws_telemetry = False

class TestEchoProtocol(DatagramProtocol):
	def __init__(self, reply_port=None):
		self.reply_port = reply_port
	def datagramReceived(self, data, src):
		server = self.factory
		(host, port) = src
		print(datetime.now().time(),
			"received %u:%32s from %s:%d" % (len(data), codecs.encode(data[:16],'hex_codec'), host, port))
		self.transport.write(data, (host, server.reply_port))

class TestServerApp(ServerFactory):
	protocol = TestEchoProtocol
	def __init__(self, listen_port, reply_port = None):
		self.listen_port = listen_port
		self.reply_port = reply_port or listen_port
	def run(self):
		reactor.listenUDP(self.listen_port, self)
		print(datetime.now().time(), "Listening at port:", self.port)
		reactor.run()

def parse_args():
	from argparse import ArgumentParser
	parser = ArgumentParser(description='STM32 UDP params manager.')
	parser.add_argument("-a", "--host", "--address", help="target host ip name or address",
					default = UDP_HOST)
	parser.add_argument("-p", "--port", help="target ip port (UDP)",
					type = int, default = UDP_PORT)
	parser.add_argument("-T", "--period", help="ip communication period (ms)",
					type = int, default = PERIOD_MS)
	parser.add_argument("-t", "--timeout", help="ip communication timeout (ms)",
					type = int, default = TIMEOUT_MS)
	parser.add_argument("-l", "--listen-port", help="listed ip port (UDP)",
					type = int)
	parser.add_argument("-r", "--reply-port", help="reply ip port (UDP)",
					type = int)
	parser.add_argument("-s", "--salt-file", help="crypto salt file name",
					metavar='FILE', default=b'')
	args = parser.parse_args()
	args.period /= 1000.0 # ms to s 
	args.timeout /= 1000.0 # ms to s
	return args 

def run(args):
	if not args.listen_port:
		a = AntibugUnitControlCommandLineApp(args.host, args.port, args.period, args.timeout, args.salt_file)
	else:
		a = TestServerApp(args.listen_port, TestEchoProtocol(args.reply_port))
	a.run()

if __name__ == '__main__':
	args = parse_args()
	run(args)
