#!/usr/bin/python
from __future__ import print_function
from time import time
from datetime import datetime
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
	class RwsControlData:
	
	representation of control values for RWS (Remote Weapon Station, turret)
"""																							
class RwsControlData(object):
	def __init__(self):
		super(RwsControlData,self).__init__()
		self.rotationV = 0.0
		self.elevationV = 0.0
		self.rotationP = None
		self.elevationP = None
		self.zoom = 0.0
		self.turret_enable = False
		self.turret_slow   = False
		self.turret_vel_prio = False # control by velocity has priority
		self.turret_reload   = False
		self.turret_force_home = False
		self.safetyARM = None   # 'ARM' or any
		self.FIRE = None   # 'FIRE' or any
		self.fire_duration = 0
		self.fire_seq = 0
		self.camerasP = 0
		self.rangefinder_seq = 0

"""
	class RwsStateData:
	
	representation of unit current state values of RWS
"""
class RwsStateData(object):
	def __init__(self):
		super(RwsStateData,self).__init__()
		self.connected = False
		self.last_error = None
		self.rotationV = 0.0
		self.elevationV = 0.0
		self.rotationP = 0.0
		self.elevationP = 0.0
		self.camerasP = 0
		self.rangefinder_seq = 0
		self.rangefinder_distance = None
		self.fire_seq = 0
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
		self.voltage_fire = 0
		self.voltage_cpu = 0
		self.percentage_bat = 0
		self.alive = False
		self.fireing = False
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



class RwsContext(RwsControlContext, RwsStateContext, RwsTelemetryContext):
	pass
class UnitContext(RwsContext, GPSContext, CompassContext):
	pass

"""
	class ControlDatagramProtocolBase:
	
	base constatnts and UDP packet composer/parser.
	
	ControlDatagramProtocolBase(context)
	
	context - reference to UnitContext implementing instance 
"""
class ControlDatagramProtocolBase(object):

	RWS_COMMAND_PKT_TYPE = 1
	RWS_COMMAND_FORMAT = "!4B 2h 2i 6B H i 2B 2x" # flags[4], rotV, eleV, rotP, eleP, arm[4], fire[2], fireDuration cameraP, rangeSeq, fireSeq
	RWS_RESPONCE_PKT_TYPE = 1
	RWS_RESPONCE_FORMAT = "!4B 3i I H 2B" # flags[4], rotP, eleP, cameraP, distance, shots, rangeSeq, fireSeq
	GPS_REPORT_PKT_TYPE = 3
	GPS_REPORT_FORMAT = "!4B 3i" # flags[4], lat, lon, alt
	COMPASS_REPORT_PKT_TYPE = 4
	COMPASS_REPORT_FORMAT = "!4B 2i H 2x" # flags[4], heading, elevation, level
	GYRO_REPORT_PKT_TYPE = 5
	GYRO_REPORT_FORMAT = "!4B 3h" # flags[4], x,y,z
	TEMPERATURES_REPORT_PKT_TYPE = 6
	TEMPERATURES_REPORT_FORMAT = "!8B 34b" # validFlags[8], temperatures[]
	RWS_TELEMETRY_REPORT_PKT_TYPE = 12
	RWS_TELEMETRY_REPORT_FORMAT = "!4B 4h 4h 3h H" # flags[4], xRpm, xU, xI, xT, yRpm, yU, yI, yT, batU, fireU, cpuU, bat% 
	POWERS_REPORT_PKT_TYPE = 10
	FOLLOWME_REPORT_PKT_TYPE = 11
	FOLLOWME_REPORT_FORMAT = "!4B 4h i"
	COMMAND_RESPONCES_PKTS = {RWS_COMMAND_PKT_TYPE: RWS_RESPONCE_PKT_TYPE}

	def __init__(self, context):
		self.context = context
		self.retry = None
		self.count_pkt_send = 0
		self.count_pkt_received = 0

	def ComposeUdpPacket(self):
		self.count_pkt_send += 1
#		self.GenerateTestPacket()
		return self.RWS_COMMAND_PKT_TYPE, self.ComposeRwsControlUdpPacket()

	def ComposeRwsControlUdpPacket(self):
		control_data = self.context.GetRwsControlData()
#		def fmt_fn(v):
#			return "---" if v is None else "{:.3f}".format(v)
#		print("rotV:{}, eleV:{}, rotP:{}, eleP:{}".format(
#				fmt_fn(control_data.rotationV), fmt_fn(control_data.elevationV),
#				fmt_fn(control_data.rotationP), fmt_fn(control_data.elevationP)))
		return struct.pack(self.RWS_COMMAND_FORMAT,
				sum( [
					0x01 if control_data.turret_enable else 0,
					0x02 if control_data.turret_slow   else 0,
					0x04 if control_data.turret_reload else 0,
					0x08 if control_data.turret_force_home else 0,
				] ), # flags 1
				sum( [
					0x01 if not control_data.rotationV is None else 0,
					0x02 if not control_data.elevationV is None else 0,
					0x04 if not control_data.rotationP is None else 0,
					0x08 if not control_data.elevationP is None else 0,
					0x10 if control_data.turret_vel_prio else 0,
					0x20 if control_data.turret_vel_prio else 0,
				] ), # flags 2
				0, 0, # flags 3-4 
				ftos(control_data.rotationV),
				ftos(control_data.elevationV),
				rtol(control_data.rotationP),
				rtol(control_data.elevationP),
				ord('A') if control_data.safetyARM == 'ARM' else 0,
				0, 0, 0,
				ord('F') if control_data.FIRE == 'FIRE' else 0,
				0,
				control_data.fire_duration,
				control_data.camerasP,
				control_data.rangefinder_seq,
				control_data.fire_seq % 256,
		)

	def GenerateTestPacket(self):
		if self.count_pkt_send % 10 == 1:
			t = ControlDatagramProtocolBase.RWS_RESPONCE_PKT_TYPE
			f = ControlDatagramProtocolBase.RWS_RESPONCE_FORMAT
		elif self.count_pkt_send % 20 == 5:
			t = ControlDatagramProtocolBase.TEMPERATURES_REPORT_PKT_TYPE
			f = ControlDatagramProtocolBase.TEMPERATURES_REPORT_FORMAT
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
		elif pkt_type == self.GPS_REPORT_PKT_TYPE:
			return self.ParseGpsUdpPacket(data)
		elif pkt_type == self.COMPASS_REPORT_PKT_TYPE:
			return self.ParseCompassUdpPacket(data)
		elif pkt_type == self.GYRO_REPORT_PKT_TYPE:
			return self.ParseGyroUdpPacket(data)
		elif pkt_type == self.TEMPERATURES_REPORT_PKT_TYPE:
			return self.ParseTemperaturesUdpPacket(data)
		elif pkt_type == self.RWS_TELEMETRY_REPORT_PKT_TYPE:
			return self.ParseRwsTelemetryUdpPacket(data)
		elif pkt_type == self.FOLLOWME_REPORT_PKT_TYPE:
			return self.ParseFollowMeUdpPacket(data)
		return False

	def ParseRwsUdpPacket(self, data, loop_time):
#		print("RX RWS status l:%i t:%i"%(len(data), loop_time))
		state = self.context.GetRecentRwsState()
		if not state: return
		(flags0, flags1, flags2, flags3, rotationP, elevationP, camP, dist, shots, rangeSeq, fireSeq) = struct.unpack(self.RWS_RESPONCE_FORMAT, data)
#		print("RX RWS status f:%02X r:%d, dist:%f (%d)"%(flags1, rotationP, dist, rangeSeq ))
		state.connected = True
#		state.rotationV = stof(rotationV, flags1 & 0x01)
#		state.elevationV = stof(elevationV, flags1 & 0x02)
		state.rotationP = ltor(rotationP, flags1 & 0x04) 
		state.elevationP = ltor(elevationP, flags1 & 0x08)
		state.camerasP = camP
		state.rangefinder_distance = dist * 0.001 # mm to m
		state.rangefinder_seq = rangeSeq
		state.fire_seq = fireSeq
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
			voltage_fire,
			voltage_cpu,
			percentage_bat,
		) = struct.unpack(self.RWS_TELEMETRY_REPORT_FORMAT, data)
		telemetry.voltage_X = voltage_X * 0.01
		telemetry.voltage_Y = voltage_Y * 0.01
		telemetry.voltage_bat = voltage_bat * 0.01
		telemetry.voltage_fire = voltage_fire * 0.01
		telemetry.voltage_cpu = voltage_cpu * 0.01
		telemetry.percentage_bat = ustof(percentage_bat) * 100.0
		(
			telemetry.alive,
			telemetry.fireing,
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
				context=None):
# 		print "proto init", period, timeout
		UdpProtocolTwisted.__init__(self, udp_host, udp_port, period = period,
								timeout = timeout, listen_port = listen_port,
								on_data = on_data, on_error = on_error,
								on_restored = on_restored, on_tx = on_tx,
								hash_salt = hash_salt,
								)
		ControlDatagramProtocolBase.__init__(self, context)

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
	):
		super(AntibugUnitCommunicator, self).__init__()
		self.rws_control = RwsControlData()
		self.rws_state = RwsStateData()
		self.unit_gps = GPSData()
		self.unit_heading = CompassData()
		self.unit_temperatures = TemperaturesData()
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
				context=self)
		self.reset_call = None
		self.OnRwsStateChanged = None 
		self.OnConnectionLost = None 
		self.OnConnectionEstablished = None
		self.OnPositionFixed = None
		self.OnPositionLost = None
		self.OnPositionChanged = None
		self.OnHeadingChanged = None
		self.OnTemperaturesChanged = None 
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

	#virtual RwsControlContext implementations
	def GetRwsControlData(self):
		return self.rws_control

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
	keys_to_buttons_remap = {'0':0, '-':1, '_':1, '=':2, '+':2}
	buttons_to_rws_props_remap = {0:'turret_enable', 1:'turret_slow', 2:'turret_reload'}
	def __init__(self, host, port, period, timeout, salt=None):
		self.key_reader = CommandLineKeyReader()
		self.status_printer = StatusLinePrinter('{0.elev} {0.rotv} {0.elep} {0.rotp} {0.btns} {0.ARM}{0.FIRE} {0.gots} {0.tele} {0.anim} {0.msg}')
		self.comm = comm = AntibugUnitCommunicator(host, port, period, timeout, on_data = self.print_state, hash_salt=salt)
		self.animator = CommunicationAnimator(0.25)
		self.msg = ''
		self.got_rws_state = False
		self.got_position = False
		self.got_heading = False
		self.got_temperatures = False
		self.got_rws_telemetry = False
		comm.OnRwsStateChanged = self.OnRwsStateChanged 
		comm.OnConnectionLost = self.OnConnectionLost 
		comm.OnConnectionEstablished = self.OnConnectionEstablished
		comm.OnPositionFixed = self.OnPositionFixed
		comm.OnPositionLost = self.OnPositionLost
		comm.OnPositionChanged = self.OnPositionChanged
		comm.OnHeadingChanged = self.OnHeadingChanged
		comm.OnTemperaturesChanged = self.OnTemperaturesChanged
		comm.OnRwsTelemetryChanged = self.OnRwsTelemetryChanged
		comm.OnDistanceChanged = self.OnDistanceChanged
		print("AntibugUnit UDP controller %s:%d" % (host, port))
		print("Use arrows keys to control RWS, SPACE for stop, Enter for center")
		print("    top row keys toggle buttons     0-= Backspace for ARM, Del for FIRE")
		print("                                    ESR")
		print(" EleV  RotV  EleP  RotP ESR FR")
		self.print_state()
	def run(self):
		self.comm.Start()  
		LoopingCall(self.tick).start(1.0 / 10)
# 		LoopingCall(self.print_state).start(1.0 / 4)
		reactor.run()
	def tick(self):
		key = self.key_reader().lower()
		if key:
			rws_control = self.comm.rws_control
			if key in ('up',):
				rws_control.elevationV = rangedChange(rws_control.elevationV, 0.1)
				rws_control.elevationP = math.pi
				rws_control.turret_vel_prio = True
			elif key in ('down',):
				rws_control.elevationV = rangedChange(rws_control.elevationV, -0.1)
				rws_control.elevationP = -math.pi
				rws_control.turret_vel_prio = True
			elif key in ('left',):
				rws_control.rotationV = rangedChange(rws_control.rotationV, -0.1)
				rws_control.rotationP = -math.pi
				rws_control.turret_vel_prio = True
			elif key in ('right',):
				rws_control.rotationV = rangedChange(rws_control.rotationV, 0.1)
				rws_control.rotationP = math.pi
				rws_control.turret_vel_prio = True
			elif key in ('ctrlup',):
				rws_control.elevationV = None
				rws_control.elevationP = angleChange(rws_control.elevationP, 90/8)
				rws_control.turret_vel_prio = False
			elif key in ('ctrldown',):
				rws_control.elevationV = None
				rws_control.elevationP = angleChange(rws_control.elevationP, -90/8)
				rws_control.turret_vel_prio = False
			elif key in ('ctrlleft',):
				rws_control.rotationV = None
				rws_control.rotationP = angleChange(rws_control.rotationP, -90/8)
				rws_control.turret_vel_prio = False
			elif key in ('ctrlright',):
				rws_control.rotationV = None
				rws_control.rotationP = angleChange(rws_control.rotationP, 90/8)
				rws_control.turret_vel_prio = False
			elif key in ('altup',):
				rws_control.elevationV = 1
				rws_control.elevationP = angleChange(rws_control.elevationP, 1)
				rws_control.turret_vel_prio = False
			elif key in ('altdown',):
				rws_control.elevationV = 1
				rws_control.elevationP = angleChange(rws_control.elevationP, -1)
				rws_control.turret_vel_prio = False
			elif key in ('altleft',):
				rws_control.rotationV = 1
				rws_control.rotationP = angleChange(rws_control.rotationP, -1)
				rws_control.turret_vel_prio = False
			elif key in ('altright',):
				rws_control.rotationV = 1
				rws_control.rotationP = angleChange(rws_control.rotationP, 1)
				rws_control.turret_vel_prio = False
			elif key == ' ': #space
				rws_control.elevationV = 0.0
				rws_control.rotationV = 0.0
				rws_control.elevationP = None
				rws_control.rotationP = None
				rws_control.turret_vel_prio = True
			elif key == 'enter': 
				rws_control.elevationV = None
				rws_control.rotationV = None
				rws_control.elevationP = 0.0
				rws_control.rotationP = 0.0
				rws_control.turret_vel_prio = False
			elif key == 'backspace':
				rws_control.safetyARM = (None if rws_control.safetyARM else 'ARM')
				self.msg += 'safety toggled'
			elif key in ('del', 'shiftdel', 'ctrldel', 'altdel'):
				rws_control.FIRE = (None if rws_control.FIRE else 'FIRE')
				if rws_control.FIRE:
					rws_control.fire_seq += 1
					if key == 'del':
						rws_control.fire_duration = 100
						self.msg += 'FIRE.'
					elif key == 'shiftdel':
						rws_control.fire_duration = 1000
						self.msg += 'FIRE..'
					elif key == 'ctrldel':
						rws_control.fire_duration = 10000
						self.msg += 'FIRE...'
					elif key == 'altdel':
						rws_control.fire_duration = 0
						self.msg += 'FIRE___'
			elif key == 'r':
				rws_control.rangefinder_seq = (rws_control.rangefinder_seq + 1) % 256
				self.msg += 'RANGE seq=%d'%rws_control.rangefinder_seq
			elif key in self.keys_to_buttons_remap:
				n = self.keys_to_buttons_remap[key]
				prop = self.buttons_to_rws_props_remap.get(n, None)
				if prop:
					setattr(rws_control, prop, not getattr(rws_control, prop))
					self.msg += '%s toggled'%prop
			elif key == 'f': 
				rws_control.FIRE = 'F' if rws_control.FIRE != 'F' else '\0'
				if rws_control.FIRE:
					rws_control.fire_seq += 1
					rws_control.fire_duration = 10
			elif key in ('q', 'esc', 'altf4'): 
				reactor.stop()
			else: self.msg += 'key: "%s" ignored'%repr(key)
			self.print_state()
	def OnRwsStateChanged(self):
		self.got_rws_state = True
		self.print_state(0)
	def OnConnectionLost(self):
		self.msg += repr(self.comm.rws_state.last_error)
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
	def OnRwsTelemetryChanged(self):
		self.got_rws_telemetry = True
#		self.msg += "RWS Telemetry"
		self.print_state(0)
	def OnDistanceChanged(self):
		self.msg += "Distance measured %.1f"%self.comm.rws_state.rangefinder_distance
		self.print_state(0)
	def print_state(self, animation_speed=None):
			import sys
			rws_control = self.comm.rws_control
			rws_state = self.comm.rws_state
			state_text = " time:%4dms RWS: %s %s " % (
					self.comm.avg_loop_time*1000,
					r2printable(rws_state.elevationP),
					r2printable(rws_state.rotationP),
			)  
			self.status_printer.elev=f2printable(rws_control.elevationV)
			self.status_printer.rotv=f2printable(rws_control.rotationV)
			self.status_printer.elep=r2printable(rws_control.elevationP)
			self.status_printer.rotp=r2printable(rws_control.rotationP)
			self.status_printer.btns="".join([('X' if (v and getattr(rws_control, v)) else '.')
					for n in range(1 + max(self.keys_to_buttons_remap.values()))
					for v in [self.buttons_to_rws_props_remap.get(n)]])
			self.status_printer.ARM=('X' if rws_control.safetyARM == 'ARM' else '.')
			self.status_printer.FIRE=('X' if rws_control.FIRE == 'FIRE' else '.')
			self.status_printer.gots=''.join( (abr if got else '~') for abr,got in zip(
					"RPHTR",
					(self.got_rws_state, self.got_position, self.got_heading, self.got_temperatures, self.got_rws_telemetry)
				))
			self.status_printer.tele=state_text if rws_state.connected else "NO CONNECTION".center(len(state_text), '-')
			self.status_printer.anim=(self.animator.AnimateOk if rws_state.connected else self.animator.AnimateErr)(animation_speed)
			self.status_printer.msg=self.msg
			self.status_printer.print()
			if animation_speed is None:
				self.msg = ''
				self.got_rws_state = False
				self.got_position = False
				self.got_heading = False
				self.got_temperatures = False
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
